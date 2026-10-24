"""A separate worker with a fixed allowlist of read-only instrument queries."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import time

CUSTOM_QUERIES = frozenset((0x4A, 0x3E))
ASCII_QUERIES = frozenset(("Q", "?", "?6", "?202"))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ReadOnlySerial:
    def __init__(self, name, config):
        import serial
        from lab_automation.transport import custom_frame, parse_custom, ascii_frame, parse_ascii
        self.config, self.queries_sent = config, 0
        self.frames = (custom_frame, parse_custom, ascii_frame, parse_ascii)
        settings = config["serial"]
        self.serial = serial.Serial(port=None, baudrate=config["baudrate"], bytesize=settings["data_bits"],
                                    parity=settings["parity"], stopbits=settings["stop_bits"],
                                    timeout=0.05, write_timeout=1, exclusive=True,
                                    xonxoff=False, rtscts=False, dsrdtr=False)
        self.serial.port = config["port"]
        self.serial.dtr = False
        self.serial.rts = False
        try:
            self.serial.open()
        except Exception:
            self.serial.close()
            raise

    def request(self, packet, parser):
        self.serial.reset_input_buffer()
        if self.serial.write(packet) != len(packet):
            raise OSError("Incomplete status query")
        self.queries_sent += 1
        self.serial.flush()
        deadline = time.monotonic() + min(1.0, self.config["response_timeout_s"])
        received = bytearray()
        while time.monotonic() < deadline:
            received.extend(self.serial.read(min(self.serial.in_waiting or 1, 4096)))
            reply = parser(received)
            if reply is not None:
                return reply
            if len(received) > 8192:
                raise ValueError("Invalid status response")
        raise TimeoutError("Device did not answer the status query")

    def custom(self, function):
        if function not in CUSTOM_QUERIES:
            raise ValueError("Only status and channel queries are allowed")
        frame, parse, _, _ = self.frames
        return self.request(frame(self.config["address"], function, 0),
                            lambda raw: parse(raw, self.config["address"]))

    def ascii(self, command):
        if command not in ASCII_QUERIES:
            raise ValueError("Only read-only syringe queries are allowed")
        _, _, frame, parse = self.frames
        return self.request(frame(self.config["address"], command), parse)

    def close(self):
        self.serial.close()


class ReadOnlyStation:
    """Read EC110 identity only; never configure, stop or start a measurement."""
    def __init__(self, name, config):
        import serial
        import termios
        from .ec110 import FrameReader
        self.queries_sent, self.exclusive = 0, False
        self.serial = serial.Serial(port=None, baudrate=config['baudrate'], bytesize=8,
            parity='N', stopbits=1, timeout=.05, write_timeout=1, exclusive=True,
            xonxoff=False, rtscts=False, dsrdtr=False)
        self.serial.port = config['port']
        self.serial.dtr = False; self.serial.rts = False
        try:
            self.serial.open()
            fcntl.ioctl(self.serial.fileno(), termios.TIOCEXCL)
            self.exclusive = True
            self.reader = FrameReader(self.serial)
        except BaseException:
            self.close()
            raise

    def identity(self):
        from .ec110 import frame_bytes
        self.serial.reset_input_buffer()
        self.reader.buffer.clear()
        packet = frame_bytes(0x03, b'', 0)
        if self.serial.write(packet) != len(packet):
            raise OSError('Incomplete station identity query')
        self.queries_sent += 1
        self.serial.flush()
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            frame = self.reader.read(min(.2, max(0, deadline-time.monotonic())))
            if frame and frame[:3] == (1, 0x04, 0):
                payload = frame[3]
                start, end = payload.find(b'{'), payload.rfind(b'}')
                if start < 0 or end < start:
                    raise ValueError('Invalid station identity response')
                info = json.loads(payload[start:end+1])
                if not isinstance(info, dict):
                    raise ValueError('Invalid station identity response')
                return info
        raise TimeoutError('Station did not answer the identity query')

    def close(self):
        try:
            if self.exclusive:
                import termios
                fcntl.ioctl(self.serial.fileno(), termios.TIOCNXCL)
        finally:
            self.exclusive = False
            self.serial.close()


def port_in_use(path, proc_root=Path("/proc")):
    target = Path(path).stat()
    for process in proc_root.iterdir():
        if not process.name.isdigit() or process.name == str(os.getpid()):
            continue
        try:
            for fd in (process / "fd").iterdir():
                try:
                    opened = fd.stat()
                    if (opened.st_dev, opened.st_ino) == (target.st_dev, target.st_ino):
                        return True
                except OSError:
                    continue
        except OSError:
            continue
    return False


def query_device(name, config, link_factory=ReadOnlySerial, check_port=True, station_factory=ReadOnlyStation):
    result = {"id": name, "state": "unknown", "source": "read_only_query", "queried_at": utc_now(),
              "queries_sent": 0, "motion_commands_sent": 0}
    if config.get("kind") not in ("peristaltic", "valve", "syringe", "electrochemical"):
        return {**result, "state": "unsupported", "reason": "unsupported_instrument"}
    link = None
    try:
        if check_port:
            port = Path(config["port"])
            if not port.exists():
                return {**result, "state": "port_missing", "reason": "serial_node_missing"}
            if not stat.S_ISCHR(port.stat().st_mode):
                return {**result, "state": "error", "reason": "invalid_serial_node"}
            if port_in_use(port):
                return {**result, "state": "in_use", "reason": "serial_port_in_use"}
        link = (station_factory if config['kind']=='electrochemical' else link_factory)(name, config)
        if config['kind']=='electrochemical':
            info = link.identity()
            product = str(info.get('ProductType',''))[:160]
            if 'EC110' not in product.upper().replace('-','').replace(' ',''):
                result.update(state='error',reason='identity_mismatch')
            else:
                result.update(state='connected',product=product,
                    firmware=str(info.get('FirmwareVersion',''))[:160],
                    connection_verified=True,measurement_state='unknown',query='ec110_identity')
        elif config["kind"] == "syringe":
            reply = link.ascii("Q")
            result.update(state="error" if reply.error else "idle" if reply.ready else "busy",
                          error_code=reply.error)
            if not reply.error:
                identity = link.ascii("?202")
                if identity.error or identity.payload.strip() != config["expected_serial_number"]:
                    result.update(state="error", reason="identity_mismatch")
                elif reply.ready:
                    position, valve = link.ascii("?"), link.ascii("?6")
                    if position.error or valve.error:
                        raise ValueError("Syringe position query failed")
                    steps = int(position.payload)
                    if not 0 <= steps <= config["stroke_steps"] or valve.payload.lower().strip() not in ("i", "o"):
                        raise ValueError("Invalid syringe position response")
                    result.update(position_steps=steps, valve_position=valve.payload.lower().strip())
        else:
            reply = link.custom(0x4A)
            result.update(state="idle" if reply.status == 0 else "busy" if reply.status == 4 else "not_ready",
                          status_code=reply.status)
            if config["kind"] == "valve" and reply.status == 0:
                position = link.custom(0x3E)
                if position.status != 0 or not 1 <= position.value <= config["channels"]:
                    result.update(state="error", reason="invalid_valve_position")
                else:
                    result["channel"] = position.value
                    result["route"] = next((key for key, value in config["routes"].items() if value == position.value), None)
    except TimeoutError:
        result.update(state="no_response", reason="response_timeout")
    except PermissionError:
        result.update(state="error", reason="serial_permission_denied")
    except Exception:
        result.update(state="error", reason="status_query_failed")
    finally:
        if link:
            result["queries_sent"] = link.queries_sent
            try:
                link.close()
            except Exception:
                result.update(state="error", reason="serial_close_failed")
        result["queried_at"] = utc_now()
    return result


def scan(lab_root, templates, query=query_device):
    logs = Path(lab_root) / "logs/automation"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "execution.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "skipped", "reason": "controller_busy", "devices": [], "queries_sent": 0, "motion_commands_sent": 0}
        devices = [query(name, template["device"]) for name, template in templates.items()]
        return {"state": "completed", "sampled_at": utc_now(), "devices": devices,
                "queries_sent": sum(device["queries_sent"] for device in devices), "motion_commands_sent": 0}


def main():
    payload = json.load(sys.stdin)
    lab_root = Path(payload["lab_root"]).resolve()
    sys.path.insert(0, str(lab_root / "src"))
    result = scan(lab_root, payload["templates"])
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
