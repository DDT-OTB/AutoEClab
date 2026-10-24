"""EC110 CV adapter. Serial access is confined to an explicitly started worker."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import struct
import time

DEFAULTS = dict(enabled=False, port='', baudrate=115200, max_potential_v=3.0,
                max_cycles=499, max_duration_s=3600, max_raw_bytes=268435456,
                dac_reference_mv=10000.0, step_time_unit_ns=1000.0,
                timing_verified=False, zero_i=0.0, cal_i=1.0, polarity_i=1,
                cal_dac=1.0)
RANGES = {1: (.25, 50), 2: (.1, 125), 3: (.01, 1250), 4: (.001, 12500),
          5: (.0001, 125000), 6: (.00001, 1250000), 7: (.000001, 12500000),
          8: (.0000001, 125000000), 9: (.00000001, 1250000000), 10: (.000000001, 12500000000)}


class StationError(RuntimeError):
    pass


def station_policy(value=None):
    if value is None:
        return deepcopy(DEFAULTS)
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('Invalid electrochemical workstation configuration')
    result = {**DEFAULTS, **value}
    if type(result['enabled']) is not bool or type(result['timing_verified']) is not bool:
        raise ValueError('Invalid workstation enable/timing setting')
    if not isinstance(result['port'], str) or (result['enabled'] and not result['port'].startswith('/dev/')):
        raise ValueError('The workstation requires a server-configured device port')
    for name, low, high in [('baudrate', 1200, 4000000), ('max_cycles', 1, 499),
                           ('max_duration_s', 30, 3600), ('max_raw_bytes', 1048576, 1073741824)]:
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError('Invalid workstation setting: ' + name)
    for name, low, high in [('max_potential_v', .001, 3), ('dac_reference_mv', 1, 20000),
                           ('step_time_unit_ns', 1, 1000000), ('cal_i', .1, 10), ('cal_dac', .1, 10)]:
        v = result[name]
        if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('Invalid workstation setting: ' + name)
    if type(result['zero_i']) not in (int, float) or not math.isfinite(result['zero_i']) or abs(result['zero_i']) > 32768:
        raise ValueError('Invalid current zero offset')
    if type(result['polarity_i']) is not int or result['polarity_i'] not in (-1, 1):
        raise ValueError('Invalid current polarity')
    return result


def encode_cv(parameters, profile):
    """Convert UI units; retain exact register values for audit and replay."""
    from .measurement import validate_cv
    p, profile = parameters, station_policy(profile)
    duration = validate_cv(p)
    if p['cycles'] > profile['max_cycles']:
        raise ValueError('CV 圈数超过当前仪器扫描段数允许的完整循环数。')
    if duration > profile['max_duration_s']:
        raise ValueError('预计采集时长超过服务器当前任务时长上限。')
    def mv(key):
        value = p[key] * 1000
        if abs(p[key]) > profile['max_potential_v'] or not math.isclose(value, round(value), abs_tol=1e-8):
            raise ValueError('电位须以 1 mV 为步长，且位于当前工作站的电位限制内。')
        return int(round(value))
    initial, low, high, final = [mv(k) for k in ['initial_potential_v', 'lower_potential_v', 'upper_potential_v', 'final_potential_v']]
    interval = p['potential_step_v'] * 1000
    if not math.isclose(interval, round(interval), abs_tol=1e-8) or not 1 <= round(interval) <= 64:
        raise ValueError('EC110 采样电位间隔须为 1 至 64 mV 的整数。')
    quiet = p['quiet_time_s']
    if not float(quiet).is_integer() or not 0 <= quiet <= 3600:
        raise ValueError('EC110 静置时间须为 0 至 3600 秒的整数。')
    sensitivity = 0
    if p['current_range_a'] is not None:
        sensitivity = next((index for index, (limit, _) in RANGES.items()
                            if math.isclose(limit, p['current_range_a'], rel_tol=1e-9)), None)
        if sensitivity is None:
            raise ValueError('固定电流量程须选择 EC110 支持的档位。')
    # CV step registers use the command DAC's 20 V full span (65535 codes).
    # The adaptive dac_vref_mv in 0x18 is only for decoding returned samples.
    # Official ECProtocol::setDacLsbMv uses 65535 * cal_dac / 20000 codes/mV.
    lsb_v = profile['dac_reference_mv'] / 1000 / 32767.5 / profile['cal_dac']
    tick = profile['step_time_unit_ns'] * 1e-9
    # Public protocol bounds are 100..1000000 for the time register.
    step = max(1, math.ceil(p['scan_rate_v_s'] * 100 * tick / lsb_v))
    timing = int(round(step * lsb_v / p['scan_rate_v_s'] / tick))
    if not 1 <= step <= 32767 or not 100 <= timing <= 1000000:
        raise ValueError('扫描速率不能转换为当前工作站支持的步进时间，请调整速率。')
    result = dict(keep_final=0, signal_filter=0, init_potential_mv=initial, low_potential_mv=low,
                  single_step_dac_code=step, single_step_time_us=timing,
                  sweep_segments=2 * p['cycles'] + 1,
                  initial_scan_polarity=0 if p['initial_scan_direction'] == 'positive' else 1,
                  final_potential_mv=final, high_potential_mv=high,
                  sample_interval_mv=int(round(interval)), quiet_time_s=int(quiet),
                  sensitivity=sensitivity, iv_filter=0, potential_filter=0)
    result['timing_basis'] = dict(step_time_unit_ns=profile['step_time_unit_ns'],
                                  dac_reference_mv=profile['dac_reference_mv'],
                                  reference_basis='command_dac_full_span',
                                  independently_verified=profile['timing_verified'],
                                  requested_scan_rate_v_s=p['scan_rate_v_s'])
    return result


def cv_payload(encoded):
    names = ['keep_final', 'signal_filter', 'init_potential_mv', 'low_potential_mv',
             'single_step_dac_code', 'single_step_time_us', 'sweep_segments',
             'initial_scan_polarity', 'final_potential_mv', 'high_potential_mv',
             'sample_interval_mv', 'quiet_time_s', 'sensitivity', 'iv_filter', 'potential_filter']
    return struct.pack('>15i', *(encoded[name] for name in names))


def frame_bytes(command, payload, seq):
    if len(payload) > 65535:
        raise ValueError('V1 payload too large')
    return bytes([0x55, 0xaa, 1, seq & 255, command]) + len(payload).to_bytes(2, 'big') + payload + b'\xbb\xee'


class FrameReader:
    def __init__(self, port, maximum=1048576):
        self.port, self.maximum, self.buffer = port, maximum, bytearray()

    def read(self, timeout=.25):
        end = time.monotonic() + timeout
        while True:
            while len(self.buffer) >= 2:
                start = self.buffer.find(b'\x55\xaa')
                if start < 0:
                    del self.buffer[:-1]; break
                if start:
                    del self.buffer[:start]
                if len(self.buffer) < 3:
                    break
                version = self.buffer[2]
                header = {1: 7, 2: 9}.get(version)
                if header is None:
                    del self.buffer[0]; continue
                if len(self.buffer) < header:
                    break
                size = int.from_bytes(self.buffer[5:header], 'big')
                if size > self.maximum:
                    del self.buffer[0]; continue
                end_frame = header + size + 2
                if len(self.buffer) < end_frame:
                    break
                if self.buffer[end_frame - 2:end_frame] != b'\xbb\xee':
                    del self.buffer[0]; continue
                frame = (version, self.buffer[4], self.buffer[3], bytes(self.buffer[header:end_frame - 2]))
                del self.buffer[:end_frame]
                return frame
            if time.monotonic() >= end:
                return None
            self.buffer.extend(self.port.read(max(1, min(self.port.in_waiting, 65536))))


def decode_frame(payload, elapsed, profile):
    if len(payload) < 88:
        raise StationError('CV 数据公共头长度不足。')
    header = struct.unpack_from('>12i', payload)
    mode, status, sensitivity, phase, reference = header[:5]
    if mode != 1:
        return mode, status, []
    if status == 1:
        if len(payload) != 90:
            raise StationError('CV 结束帧长度异常。')
        return mode, status, []
    if status != 0 or (len(payload) - 88) % 204:
        raise StationError('CV 采样块长度或状态异常。')
    if sensitivity not in RANGES or not 0 < reference <= 20000:
        raise StationError('CV 返回未支持的量程或 DAC 参考值。')
    cgain = 1
    try:
        for mapping, code in zip(({0: 1, 2: 5}, {0: 1, 3: 10}, {0: 1, 3: 10}), header[9:12]):
            cgain *= mapping[code]
    except KeyError as exc:
        raise StationError('CV 电流增益编码未定义。') from exc
    resistance = RANGES[sensitivity][1] * cgain
    rows = []
    for offset in range(88, len(payload), 204):
        block_phase, dac = struct.unpack_from('<hh', payload, offset)
        values = struct.unpack_from('<100h', payload, offset + 4)
        adc = sum(values) / 100
        potential = dac * reference / 1000 / (32767.5 * profile['cal_dac'])
        current = profile['polarity_i'] * (adc + profile['zero_i']) / 32767 * 12.5 / resistance * profile['cal_i']
        rows.append(dict(time_s=elapsed, potential_v=potential, current_a=current, adc_mean=adc,
                         dac_code=dac, sensitivity=sensitivity, current_gain=cgain,
                         dac_vref_mv=reference, sample_count=100))
    return mode, status, rows


class EC110Session:
    def __init__(self, profile, event, serial_factory=None, kernel_exclusive=True):
        self.profile = station_policy(profile)
        self.event = event
        self.serial_factory = serial_factory
        self.kernel_exclusive = kernel_exclusive
        self.port = self.reader = self.writer = None
        self.seq = 0
        self.pending = []
        self.info = {}
        self.exclusive = False
        self.cleanup_mode = False

    def record(self, direction, version, cmd, seq, payload):
        if self.writer:
            try:
                self.writer.frame(direction, version, cmd, seq, payload, self.profile['max_raw_bytes'])
            except Exception:
                if not self.cleanup_mode:
                    raise

    def send(self, cmd, payload):
        self.seq = (self.seq + 1) & 255
        self.port.write(frame_bytes(cmd, payload, self.seq))
        self.port.flush()
        self.record('tx', 1, cmd, self.seq, payload)
        return self.seq

    def receive(self, timeout=.25):
        frame = self.reader.read(timeout)
        if frame:
            self.record('rx', *frame)
            try:
                self.event('serial_reply', device='electrochemical_station', command=frame[1], payload_bytes=len(frame[3]))
            except Exception:
                if not self.cleanup_mode:
                    raise
        return frame

    def echo(self, command, seq, payload):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            frame = self.receive(min(.25, end - time.monotonic()))
            if frame is None:
                continue
            version, cmd, reply_seq, data = frame
            if cmd == command and reply_seq == seq:
                if version != 1 or len(data) != len(payload) + 4:
                    raise StationError('工作站应答长度与协议不一致，未通过校验。')
                if struct.unpack_from('>i', data)[0] != 0 or data[4:] != payload:
                    raise StationError('工作站拒绝命令或参数回显不一致。')
                return
            if cmd in (0x18, 0x20):
                if self.cleanup_mode:
                    continue
                if len(self.pending) >= 64:
                    raise StationError('等待应答时收到过多旧数据。')
                self.pending.append(frame)
        raise StationError('等待工作站命令应答超时。')

    def control(self, state, mode=1):
        payload = struct.pack('>7i', mode, state, 0, 0, 0, 0, 0)
        seq = self.send(0x11, payload)
        self.echo(0x12, seq, payload)

    def open(self):
        if self.port:
            return
        if not self.profile['enabled']:
            raise StationError('电化学工作站执行尚未启用。')
        factory = self.serial_factory
        if factory is None:
            import serial
            factory = serial.Serial
        try:
            self.port = factory(self.profile['port'], baudrate=self.profile['baudrate'],
                                bytesize=8, parity='N', stopbits=1, timeout=.05,
                                write_timeout=2, exclusive=True)
            if self.kernel_exclusive:
                import fcntl
                import termios
                fcntl.ioctl(self.port.fileno(), termios.TIOCEXCL)
                self.exclusive = True
            self.reader = FrameReader(self.port)
            seq = self.send(0x03, b'')
            end = time.monotonic() + 5
            while time.monotonic() < end:
                frame = self.receive(.25)
                if frame and frame[:3] == (1, 0x04, seq):
                    data = frame[3]
                    start, finish = data.find(b'{'), data.rfind(b'}')
                    if start < 0 or finish < start:
                        raise StationError('工作站身份应答格式异常。')
                    self.info = json.loads(data[start:finish + 1])
                    break
            product = str(self.info.get('ProductType', ''))
            if 'EC110' not in product.upper().replace('-', '').replace(' ', ''):
                raise StationError('工作站身份未匹配 EC110，未开始测量。')
            self.control(0, mode=0)
            self.pending.clear()
            self.port.reset_input_buffer(); self.reader.buffer.clear()
            self.event('preflight_passed', device='electrochemical_station', product=product)
        except BaseException:
            self.close()
            raise

    def measure(self, step, writer):
        self.writer = writer
        encoded = encode_cv(step['parameters'], self.profile)
        payload = cv_payload(encoded)
        started = stopped = False
        state, failure = 'failed', None
        parameters = step['parameters']
        first, second = ((parameters['upper_potential_v'], parameters['lower_potential_v'])
                         if parameters['initial_scan_direction'] == 'positive'
                         else (parameters['lower_potential_v'], parameters['upper_potential_v']))
        targets = [value for _ in range(parameters['cycles']) for value in (first, second)] + [parameters['final_potential_v']]
        cursor = 0
        first_data_elapsed = last_data_elapsed = None
        timing_validation = None
        try:
            seq = self.send(0x05, payload)
            self.echo(0x06, seq, payload)
            self.pending.clear()
            started = True
            self.control(2)
            begin = time.monotonic()
            heartbeat = begin
            timeout = min(self.profile['max_duration_s'], max(30, step['estimated_measurement_s'] * 3 + 20))
            writer.publish(force=True, state='running', started_at=now_iso(),
                           instrument=dict(product=self.info.get('ProductType'), firmware=self.info.get('FirmwareVersion')))
            self.event('measurement_started', device='electrochemical_station', measurement_id=writer.metadata['id'])
            while time.monotonic() - begin < timeout:
                frame = self.pending.pop(0) if self.pending else self.receive(.2)
                if frame is None:
                    if time.monotonic() - heartbeat >= 1:
                        self.event('measurement_heartbeat', device='electrochemical_station', measurement_id=writer.metadata['id'],
                                   point_count=writer.metadata['point_count'])
                        heartbeat = time.monotonic()
                    continue
                version, command, _, data = frame
                if command == 0x20:
                    raise StationError('CV 运行时收到 EIS 原始数据，模式不一致。')
                if command != 0x18:
                    continue
                if version != 2:
                    raise StationError('CV 数据帧版本与协议不一致。')
                mode, end_status, rows = decode_frame(data, time.monotonic() - begin, self.profile)
                if mode != 1:
                    if writer.metadata['point_count'] and mode == 0:
                        break
                    continue
                if end_status == 1:
                    break
                for row in rows:
                    voltage = row['potential_v']
                    quantum = row['dac_vref_mv'] / 1000 / 32767.5 * 3
                    tolerance = max(.00015, quantum, max(abs(parameters['lower_potential_v']), abs(parameters['upper_potential_v'])) * .0015)
                    if voltage < parameters['lower_potential_v'] - tolerance or voltage > parameters['upper_potential_v'] + tolerance:
                        raise StationError('返回电位超出设定窗口，已请求停止。')
                    target_tolerance = max(.00015, quantum, abs(targets[cursor]) * .0015) if cursor < len(targets) else 0
                    if cursor < len(targets) and abs(voltage - targets[cursor]) <= target_tolerance:
                        cursor += 1
                if rows:
                    first_data_elapsed = rows[0]['time_s'] if first_data_elapsed is None else first_data_elapsed
                    last_data_elapsed = rows[-1]['time_s']
                writer.append(rows)
                self.event('measurement_progress', device='electrochemical_station', measurement_id=writer.metadata['id'],
                           point_count=writer.metadata['point_count'], sample_count=writer.metadata['sample_count'],
                           target_count=len(targets), targets_reached=cursor)
                if cursor >= len(targets):
                    break
            if not writer.metadata['point_count'] or cursor < len(targets):
                raise StationError('CV 尚未完成全部设定电位段，保留已采集的部分数据。')
            expected_scan = validate_scan_duration(parameters)
            observed_scan = max(0, last_data_elapsed-first_data_elapsed)
            tolerance = max(2.0, expected_scan*.2)
            timing_validation = dict(expected_scan_s=expected_scan,observed_receive_span_s=observed_scan,
                tolerance_s=tolerance,matches_requested_timing=abs(observed_scan-expected_scan)<=tolerance,
                basis='server_receive_time',command_dac_reference_mv=self.profile['dac_reference_mv'])
            if self.profile['timing_verified'] and not timing_validation['matches_requested_timing']:
                raise StationError('返回数据的扫描时长与设定速率不匹配，已停止；请检查 DAC 步进和计时换算。')
            state = 'completed'
        except BaseException as exc:
            failure = exc
            state = 'stopped' if isinstance(exc, KeyboardInterrupt) else 'failed'
        finally:
            import signal
            import threading
            handlers = {}
            if threading.current_thread() is threading.main_thread():
                handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
            self.cleanup_mode = True
            try:
                if started:
                    try:
                        self.control(0)
                        stopped = True
                    except BaseException as exc:
                        state = 'failed'
                        try:
                            self.event('stop_failed', device='electrochemical_station', message=str(exc))
                        finally:
                            if failure is None:
                                failure = exc
                writer.finish(state, stop_confirmed=stopped, message=str(failure) if failure else None,
                              targets_reached=cursor, target_count=len(targets),
                              completed_cycles=parameters['cycles'] if state=='completed' else None,
                              timing_validation=timing_validation)
            finally:
                self.writer = None
                self.cleanup_mode = False
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
        self.event('measurement_finished', device='electrochemical_station', measurement_id=writer.metadata['id'], state=state)
        if failure is not None:
            raise failure

    def close(self):
        if self.port:
            try:
                if self.exclusive:
                    import fcntl
                    import termios
                    fcntl.ioctl(self.port.fileno(), termios.TIOCNXCL)
            finally:
                self.port.close()
                self.port = None
                self.exclusive = False


def now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def validate_scan_duration(parameters):
    from .measurement import validate_cv
    return validate_cv(parameters)-parameters['quiet_time_s']
