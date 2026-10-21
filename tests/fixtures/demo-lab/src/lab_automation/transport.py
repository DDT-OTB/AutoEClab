"""Serial framing based on the supplied Runze test programs."""
from __future__ import annotations

from dataclasses import dataclass
import time


class DeviceError(RuntimeError):
    pass


STATUSES = {0, 1, 2, 3, 4, 5, 6, 254, 255}


@dataclass(frozen=True)
class CustomReply:
    status: int
    value: int
    raw: bytes


@dataclass(frozen=True)
class AsciiReply:
    ready: bool
    error: int
    payload: str
    raw: bytes


def custom_frame(address, function, value=0):
    if type(address) is not int or not 0 <= address <= 127:
        raise ValueError("需要单设备地址 0..127")
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("参数必须为 0..65535 整数")
    body = bytes((0xCC, address, function, value & 255, value >> 8, 0xDD))
    return body + sum(body).to_bytes(2, "little")


def parse_custom(data, address):
    for offset in range(max(0, len(data) - 7)):
        frame = data[offset:offset + 8]
        if (frame[0] == 0xCC and frame[1] == address and frame[5] == 0xDD
                and frame[2] in STATUSES
                and int.from_bytes(frame[6:], "little") == sum(frame[:6])):
            return CustomReply(frame[2], int.from_bytes(frame[3:5], "little"), bytes(frame))
    return None


def ascii_frame(address, command):
    if not isinstance(address, str) or len(address) != 1 or not 0x31 <= ord(address) <= 0x3F:
        raise ValueError("需要单设备 ASCII 地址")
    if not command or any(ord(c) < 0x20 or ord(c) > 0x7E for c in command):
        raise ValueError("ASCII 指令包含非法字符")
    return ("/" + address + command + "\r").encode("ascii")


def parse_ascii(data):
    offset = 0
    while True:
        start = data.find(b"/0", offset)
        if start < 0:
            return None
        end = data.find(b"\x03\r\n", start + 3)
        if end < 0:
            return None
        offset = end + 3
        status = data[start + 2]
        if not 0x40 <= status <= 0x6F:
            continue
        try:
            payload = data[start + 3:end].decode("ascii")
        except UnicodeDecodeError:
            continue
        return AsciiReply(bool(status & 0x20), status & 15, payload, bytes(data[start:end + 3]))


class SerialLink:
    """No automatic retransmission of commands with ambiguous acknowledgements."""

    def __init__(self, device_id, config, event, serial_factory=None):
        self.device_id, self.config, self.event = device_id, config, event
        if serial_factory is None:
            import serial
            serial_factory = serial.Serial
        settings = config["serial"]
        self.serial = serial_factory(
            port=None, baudrate=config["baudrate"], bytesize=settings["data_bits"],
            parity=settings["parity"], stopbits=settings["stop_bits"],
            timeout=settings["read_timeout_s"], write_timeout=config["response_timeout_s"],
            exclusive=True, xonxoff=settings["xonxoff"], rtscts=settings["rtscts"],
            dsrdtr=settings["dsrdtr"])
        self.serial.port = config["port"]
        self.serial.dtr = settings["dtr"]
        self.serial.rts = settings["rts"]
        try:
            self.serial.open()
        except Exception:
            self.serial.close()
            raise

    def request(self, packet, parser):
        started = time.monotonic()
        received = bytearray()
        try:
            self.serial.reset_input_buffer()
            count = self.serial.write(packet)
            if count != len(packet):
                raise DeviceError("串口报文未完整写出，命令执行结果不确定")
            self.serial.flush()
            deadline = started + self.config["response_timeout_s"]
            while time.monotonic() < deadline:
                chunk = self.serial.read(min(self.serial.in_waiting or 1, 4096))
                if chunk:
                    received.extend(chunk)
                    reply = parser(received)
                    if reply is not None:
                        self.event("serial_reply", device=self.device_id,
                                   tx_hex=packet.hex(" "), rx_hex=bytes(received).hex(" "))
                        return reply
                if len(received) > 8192:
                    raise DeviceError("收到超长应答")
            raise DeviceError(f"{self.device_id}: 应答超时或报文校验失败；不自动重发动作")
        except Exception as error:
            self.event("serial_error", device=self.device_id, tx_hex=packet.hex(" "),
                       rx_hex=bytes(received).hex(" "), message=str(error))
            if isinstance(error, DeviceError):
                raise
            raise DeviceError(f"{self.device_id}: 串口异常 {error}") from error

    def custom(self, function, value=0):
        return self.request(custom_frame(self.config["address"], function, value),
                            lambda raw: parse_custom(raw, self.config["address"]))

    def ascii(self, command):
        return self.request(ascii_frame(self.config["address"], command), parse_ascii)

    def close(self):
        self.serial.close()
