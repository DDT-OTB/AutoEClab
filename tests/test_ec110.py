"""Pure in-memory workstation protocol tests; no physical device is opened."""
import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from pump_console.ec110 import EC110Session, FrameReader, StationError, cv_payload, decode_frame, encode_cv, frame_bytes, station_policy
from pump_console.measurement import measurement_templates, validate_cv
from pump_console.measurement_store import MeasurementStore


def profile():
    return station_policy(dict(enabled=True, port='/dev/pts/synthetic', max_potential_v=.1, max_cycles=10))


def parameters():
    return measurement_templates(profile())['electrochemical_station']['action']['default_parameters']


def data_frame(codes=(27326, -27327, 0), mode=1, status=0):
    header = struct.pack('>22i', mode, status, 3, 0, 120, 0, *([0] * 16))
    body = header + b''.join(struct.pack('<102h', 0, code, *([100] * 100)) for code in codes)
    return b'\x55\xaa\x02\x42\x18' + len(body).to_bytes(4, 'big') + body + b'\xbb\xee'


class Workstation:
    def __init__(self):
        self.buffer = bytearray()
        self.commands = []
        self.closed = False
        self.bad_configuration = None
        self.bad_start = False
        self.bad_stop = False
        self.stream = data_frame()

    @property
    def in_waiting(self):
        return len(self.buffer)

    def read(self, count):
        value = bytes(self.buffer[:count]); del self.buffer[:count]
        return value

    def reset_input_buffer(self):
        self.buffer.clear()

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def write(self, frame):
        cmd, seq, payload = frame[4], frame[3], frame[7:-2]
        self.commands.append((cmd, payload))
        if cmd == 0x03:
            self.buffer.extend(frame_bytes(0x04, b'{"ProductType":"EC-110 Pro V1","FirmwareVersion":"synthetic"}', seq))
        elif cmd == 0x05:
            echo = b'\0' * 4 + payload
            if self.bad_configuration == 'length': echo = echo[:-4]
            elif self.bad_configuration == 'flag': echo = struct.pack('>i', 1) + payload
            elif self.bad_configuration == 'echo': echo = echo[:-1] + bytes([echo[-1] ^ 1])
            self.buffer.extend(frame_bytes(0x06, echo, seq))
        elif cmd == 0x11:
            mode, state = struct.unpack_from('>2i', payload)
            error = (state == 2 and self.bad_start) or (mode == 1 and state == 0 and self.bad_stop)
            self.buffer.extend(frame_bytes(0x12, struct.pack('>i', int(error)) + payload, seq))
            if state == 2 and not error:
                self.buffer.extend(self.stream)
        else:
            raise AssertionError('Undocumented command sent')
        return len(frame)


class EC110Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = MeasurementStore(self.tmp.name)
        self.device = Workstation()
        self.session = EC110Session(profile(), lambda *args, **kwargs: None,
                                    serial_factory=lambda *args, **kwargs: self.device, kernel_exclusive=False)
        self.addCleanup(self.session.close)
        p = parameters()
        self.step = dict(id='cv_step', description='Synthetic CV', parameters=p,
                         estimated_measurement_s=validate_cv(p))

    def writer(self):
        writer = self.store.create('synthetic_run', 'synthetic_sequence', self.step, 'synthetic_admin',
                                   encode_cv(self.step['parameters'], profile()))
        return writer

    def test_three_segments_encode_one_full_cycle_without_retaining_output(self):
        p = parameters(); e = encode_cv(p, profile()); raw = cv_payload(e)
        self.assertEqual(len(raw), 60)
        self.assertEqual(e['sweep_segments'], 3)
        self.assertEqual(e['keep_final'], 0)
        self.assertEqual(struct.unpack_from('>i', raw, 12)[0], -100)
        self.assertFalse(e['timing_basis']['independently_verified'])
        self.assertAlmostEqual(e['single_step_dac_code'] * 10 / 32767.5 /
                               (e['single_step_time_us'] * 1e-6), p['scan_rate_v_s'], delta=.001)

    def test_command_dac_full_span_is_independent_of_decoded_sample_reference(self):
        p={**parameters(), 'upper_potential_v':.5,'lower_potential_v':-.5,'initial_potential_v':.5,
           'final_potential_v':.5,'cycles':3,'scan_rate_v_s':.1}
        settings=station_policy(dict(enabled=True,port='/dev/pts/synthetic'))
        encoded=encode_cv(p,settings)
        self.assertEqual(encoded['single_step_dac_code'],1)
        self.assertEqual(encoded['single_step_time_us'],3052)
        self.assertEqual(encoded['sweep_segments'],7)
        self.assertEqual(encoded['timing_basis']['reference_basis'],'command_dac_full_span')

    def test_verified_timing_rejects_fast_data_even_when_all_voltage_targets_are_present(self):
        self.session.profile['timing_verified']=True
        self.session.open()
        writer=self.writer()
        with self.assertRaisesRegex(StationError,'扫描时长'):
            self.session.measure(self.step,writer)
        self.assertEqual(writer.metadata['state'],'failed')
        self.assertTrue(writer.metadata['stop_confirmed'])
        self.assertFalse(writer.metadata['timing_validation']['matches_requested_timing'])

    def test_full_protocol_voltage_range_is_available_without_small_window_cap(self):
        settings = station_policy(dict(enabled=True, port='/dev/pts/synthetic'))
        p = {**parameters(), 'upper_potential_v': 3, 'lower_potential_v': -3}
        encoded = encode_cv(p, settings)
        self.assertEqual((encoded['low_potential_mv'], encoded['high_potential_mv']), (-3000, 3000))
        self.assertEqual(settings['max_cycles'], 499)
        self.assertEqual(measurement_templates(settings)['electrochemical_station']['action']['default_parameters']['upper_potential_v'], .5)
        with self.assertRaises(ValueError): encode_cv({**p, 'upper_potential_v': 3.001}, settings)

    def test_nominal_dac_calibration_offset_at_full_range_does_not_false_abort(self):
        settings = station_policy(dict(enabled=True, port='/dev/pts/synthetic'))
        self.session = EC110Session(settings, lambda *args, **kwargs:None,
                                    serial_factory=lambda *args, **kwargs:self.device, kernel_exclusive=False)
        p = {**parameters(), 'upper_potential_v':3, 'lower_potential_v':-3}
        self.step = dict(id='wide_cv', description='Synthetic full-range CV', parameters=p,
                         estimated_measurement_s=validate_cv(p))
        packet = data_frame();payload = bytearray(packet[9:-2]);struct.pack_into('>i',payload,16,3600)
        self.device.stream = packet[:9] + bytes(payload) + b'\xbb\xee'
        self.session.open()
        writer = self.store.create('synthetic_wide','synthetic_sequence',self.step,'admin',encode_cv(p,settings))
        self.session.measure(self.step,writer)
        saved = self.store.get(writer.metadata['id'])
        self.assertEqual(saved['state'],'completed')
        self.assertGreater(saved['statistics']['potential_v_max'],3)
        self.assertTrue(saved['nominal_conversion']);self.assertTrue(saved['stop_confirmed'])

    def test_negative_direction_multiple_cycles_and_fixed_range(self):
        p = {**parameters(), 'cycles': 2, 'initial_scan_direction': 'negative', 'current_range_a': .001}
        encoded = encode_cv(p, profile())
        self.assertEqual(encoded['sweep_segments'], 5)
        self.assertEqual(encoded['initial_scan_polarity'], 1)
        self.assertEqual(encoded['sensitivity'], 4)

    def test_device_limits_and_unrepresentable_fields_reject_before_serial_access(self):
        for values in [dict(upper_potential_v=.2), dict(potential_step_v=.0001),
                       dict(quiet_time_s=.5), dict(cycles=11), dict(current_range_a=.123),
                       dict(initial_potential_v=.0001)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                encode_cv({**parameters(), **values}, profile())
        self.assertEqual(self.device.commands, [])

    def test_signed_little_endian_samples_big_endian_header_and_gain(self):
        packet = data_frame()
        mode, end, rows = decode_frame(packet[9:-2], 1.5, profile())
        self.assertEqual((mode, end), (1, 0))
        self.assertLess(rows[1]['potential_v'], -.099)
        self.assertEqual(rows[0]['sample_count'], 100)
        self.assertAlmostEqual(rows[0]['current_a'], 100 / 32767 * 12.5 / 1250)

    def test_frame_reader_keeps_partial_bytes_and_multiple_frames(self):
        reader = FrameReader(self.device)
        packet = data_frame()
        self.device.buffer.extend(b'noise' + packet[:12])
        self.assertIsNone(reader.read(.002))
        self.device.buffer.extend(packet[12:] + frame_bytes(0x12, b'1234', 99))
        self.assertEqual(reader.read(.02)[1], 0x18)
        self.assertEqual(reader.read(.02)[2], 99)

    def test_complete_measurement_handshakes_acquires_and_confirms_stop(self):
        self.session.open(); writer = self.writer()
        self.session.measure(self.step, writer)
        saved = self.store.get(writer.metadata['id'])
        self.assertEqual(saved['state'], 'completed')
        self.assertEqual(saved['point_count'], 3)
        self.assertEqual(saved['sample_count'], 300)
        self.assertTrue(saved['stop_confirmed'])
        self.assertEqual(saved['targets_reached'], 3)
        self.assertNotIn(0x61, [cmd for cmd, _ in self.device.commands])
        self.assertEqual(self.store.latest()['metadata']['id'], writer.metadata['id'])

    def test_bad_configuration_never_sends_cv_start(self):
        self.session.open(); self.device.bad_configuration = 'length'; writer = self.writer()
        with self.assertRaises(StationError):
            self.session.measure(self.step, writer)
        self.assertFalse(any(cmd == 0x11 and struct.unpack_from('>2i', data) == (1, 2)
                             for cmd, data in self.device.commands))
        self.assertEqual(self.store.get(writer.metadata['id'])['state'], 'failed')

    def test_start_rejection_still_sends_and_confirms_stop(self):
        self.session.open(); self.device.bad_start = True; writer = self.writer()
        with self.assertRaises(StationError): self.session.measure(self.step, writer)
        saved = self.store.get(writer.metadata['id'])
        self.assertEqual(saved['state'], 'failed'); self.assertTrue(saved['stop_confirmed'])

    def test_partial_window_is_not_reported_as_completed(self):
        self.device.stream = data_frame((27326,)) + data_frame((), mode=0)
        self.session.open(); writer = self.writer()
        with self.assertRaises(StationError): self.session.measure(self.step, writer)
        saved = self.store.get(writer.metadata['id'])
        self.assertEqual(saved['state'], 'failed'); self.assertEqual(saved['point_count'], 1)
        self.assertTrue(saved['stop_confirmed'])

    def test_out_of_window_feedback_stops_and_keeps_failure(self):
        self.device.stream = data_frame((32000,))
        self.session.open(); writer = self.writer()
        with self.assertRaisesRegex(StationError, '电位超出'): self.session.measure(self.step, writer)
        self.assertTrue(self.store.get(writer.metadata['id'])['stop_confirmed'])

    def test_logging_failure_cannot_prevent_emergency_stop(self):
        self.session.open(); writer = self.writer()
        original = writer.frame
        def failing(direction, version, cmd, seq, payload, *args, **kwargs):
            if cmd == 0x18 or self.session.cleanup_mode:
                raise OSError('synthetic storage failure')
            return original(direction, version, cmd, seq, payload, *args, **kwargs)
        with patch.object(writer, 'frame', side_effect=failing), self.assertRaises(OSError):
            self.session.measure(self.step, writer)
        self.assertTrue(self.store.get(writer.metadata['id'])['stop_confirmed'])

    def test_stop_failure_marks_dataset_failed(self):
        self.session.open(); self.device.bad_stop = True; writer = self.writer()
        with self.assertRaises(StationError): self.session.measure(self.step, writer)
        saved = self.store.get(writer.metadata['id'])
        self.assertEqual(saved['state'], 'failed'); self.assertFalse(saved['stop_confirmed'])

    def test_buffered_sampling_frames_cannot_block_stop_acknowledgement(self):
        original = self.device.write
        def buffered(frame):
            result = original(frame)
            if frame[4] == 0x11 and struct.unpack_from('>2i', frame, 7) == (1, 0):
                self.device.buffer[:0] = data_frame((0,)) * 70
            return result
        self.device.write = buffered
        self.session.open(); writer = self.writer()
        self.session.measure(self.step, writer)
        self.assertTrue(self.store.get(writer.metadata['id'])['stop_confirmed'])


if __name__ == '__main__': unittest.main()
