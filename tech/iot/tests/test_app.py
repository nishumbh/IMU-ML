"""CPython tests; these do not qualify the hardware or firmware."""
import ast
import binascii
import csv
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

IOT = Path(__file__).resolve().parents[1]
if str(IOT) not in sys.path:
    sys.path.insert(0, str(IOT))

from app import master, slave, protocol
from app.config import DEFAULT_PINS, validate
from app.mpu6050 import MPU6050
from app.runtime import Clock, close_all
from app.storage import CSVLogger, SDLogger, COLUMNS

PEER = bytes.fromhex("020102030405")
OTHER = bytes.fromhex("020102030406")
SESSION = b"session1"
OLD_SESSION = b"session0"
SAMPLE = (1.0, 2.0, 9.0, 0.1, -0.2, 0.3)


def config(role="master", iteration=1):
    return dict(role=role, iteration=iteration, channel=6,
                peer_mac=binascii.hexlify(PEER).decode(), pmk=None, lmk=None,
                sample_rate_hz=50, pins=dict(DEFAULT_PINS), imu_address=104)


class FakeClock:
    def __init__(self):
        self.t = 0

    def now_ms(self):
        return self.t

    def raw_ticks(self):
        return self.t + 10000

    def idle(self):
        self.t += 1


class FakeStore:
    def __init__(self):
        self.rows = []
        self.services = []
        self.final = None

    def append(self, row):
        self.rows.append(row)

    def service(self, now, counts):
        self.services.append(now)

    def finish(self, now, counts):
        self.final = (now, dict(counts))


class FakeRadio:
    peer = PEER

    def __init__(self, clock, dropped=(), latency=2, send_time=0):
        self.clock = clock
        self.dropped = dropped
        self.latency = latency
        self.send_time = send_time
        self.sent = []
        self.queue = []

    def send(self, packet):
        decoded = protocol.decode(packet)
        self.sent.append((self.clock.t,) + decoded)
        kind, session, sequence, ticks, values = decoded
        self.clock.t += self.send_time
        if kind == protocol.REQUEST and sequence not in self.dropped:
            reply = protocol.response(session, sequence, 987654321, SAMPLE)
            self.queue.append((self.clock.t + self.latency, PEER, reply))
        return True

    def receive(self):
        if self.queue and self.queue[0][0] <= self.clock.t:
            _, sender, packet = self.queue.pop(0)
            return sender, packet
        return None


class ProtocolTests(unittest.TestCase):
    def test_wire_sizes_roundtrip(self):
        req = protocol.request(SESSION, 0xFFFFFFFF, 123)
        rsp = protocol.response(SESSION, 9, 0xFFFFFFFF, SAMPLE)
        self.assertEqual(len(req), 18)
        self.assertEqual(len(rsp), 42)
        self.assertEqual(protocol.decode(req),
                         (protocol.REQUEST, SESSION, 0xFFFFFFFF, 123, None))
        decoded = protocol.decode(rsp)
        self.assertEqual(decoded[:4], (protocol.RESPONSE, SESSION, 9, 0xFFFFFFFF))
        for actual, expected in zip(decoded[4], SAMPLE):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_strict_shape_version_type(self):
        req = protocol.request(SESSION, 0, 0)
        bad = [b"", req[:-1], req + b"x", req + b"x" * 24,
               b"\x02" + req[1:], req[:1] + b"\x03" + req[2:],
               req[:1] + b"\x02" + req[2:], "not bytes", None]
        for packet in bad:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                protocol.decode(packet)

    def test_nonfinite_and_range_rejected_on_encode_and_decode(self):
        for index, value in ((0, math.nan), (0, math.inf), (1, -math.inf),
                             (2, 40.0), (3, 9.0), (5, -9.0)):
            sample = list(SAMPLE)
            sample[index] = value
            with self.subTest(index=index, value=value):
                with self.assertRaises(ValueError):
                    protocol.response(SESSION, 0, 0, sample)
                raw = struct.pack(protocol.HEADER, 1, 2, SESSION, 0, 0)
                raw += struct.pack("<6f", *sample)
                with self.assertRaises(ValueError):
                    protocol.decode(raw)

    def test_header_fields_never_silently_truncated(self):
        for session, seq, ticks in ((b"short", 0, 0), (SESSION, -1, 0),
                                    (SESSION, 2**32, 0), (SESSION, 0, -1),
                                    (SESSION, True, 0), (SESSION, 0, 2**32)):
            with self.assertRaises(ValueError):
                protocol.request(session, seq, ticks)

    def test_exactly_one_pair_uses_master_timestamp(self):
        p = protocol.Pairer(PEER, SESSION)
        p.begin(7, 100, 99999, SAMPLE)
        packet = protocol.response(SESSION, 7, 12, SAMPLE)
        row = p.accept(PEER, packet, 102)
        self.assertEqual(len(row), 13)
        self.assertEqual(row[0], 100)
        self.assertEqual(row[1:7], SAMPLE)
        self.assertIsNone(p.accept(PEER, packet, 103))
        self.assertEqual(p.counts["paired"], 1)
        self.assertEqual(p.counts["unmatched"], 1)

    def test_sender_nonce_sequence_type_and_invalid_counters(self):
        p = protocol.Pairer(PEER, SESSION)
        p.begin(7, 100, 100, SAMPLE)
        packets = [(OTHER, protocol.response(SESSION, 7, 1, SAMPLE)),
                   (PEER, protocol.response(OLD_SESSION, 7, 1, SAMPLE)),
                   (PEER, protocol.response(SESSION, 6, 1, SAMPLE)),
                   (PEER, protocol.response(SESSION, 8, 1, SAMPLE)),
                   (PEER, protocol.request(SESSION, 7, 1)), (PEER, b"bad")]
        for sender, data in packets:
            self.assertIsNone(p.accept(sender, data, 102))
        self.assertIsNotNone(p.pending)
        self.assertEqual(p.counts["wrong_sender"], 1)
        self.assertEqual(p.counts["stale_session"], 1)
        self.assertEqual(p.counts["unmatched"], 3)
        self.assertEqual(p.counts["invalid"], 1)

    def test_deadline_is_exclusive_no_late_or_missing_fill(self):
        p = protocol.Pairer(PEER, SESSION)
        p.begin(0, 0, 0, SAMPLE)
        self.assertIsNone(p.accept(PEER, protocol.response(SESSION, 0, 0, SAMPLE), 15))
        self.assertEqual(p.counts["timeout"], 1)
        self.assertFalse(p.expire(100))
        p.begin(1, 20, 20, SAMPLE)
        self.assertIsNone(p.accept(PEER, protocol.response(SESSION, 0, 0, SAMPLE), 21))
        self.assertEqual(p.accept(PEER, protocol.response(SESSION, 1, 0, SAMPLE), 22)[0], 20)
        self.assertEqual(p.counts["paired"], 1)

    def test_begin_never_overwrites_pending_or_wraps(self):
        p = protocol.Pairer(PEER, SESSION)
        p.begin(0xFFFFFFFF, 0, 0, SAMPLE)
        with self.assertRaises(ValueError):
            p.begin(1, 1, 1, SAMPLE)
        p.expire(15)
        with self.assertRaises(ValueError):
            p.begin(0, 20, 20, SAMPLE)

    def test_slave_session_pin_and_replays(self):
        gate = protocol.RequestGate(PEER)
        request = protocol.request(SESSION, 7, 999999)
        self.assertIsNone(gate.accept(OTHER, request))
        self.assertIsNone(gate.accept(PEER, b"bad"))
        self.assertEqual(gate.accept(PEER, request), (SESSION, 7))
        self.assertIsNone(gate.accept(PEER, request))
        self.assertIsNone(gate.accept(PEER, protocol.request(SESSION, 6, 0)))
        self.assertIsNone(gate.accept(PEER, protocol.request(OLD_SESSION, 8, 0)))
        self.assertIsNone(gate.accept(PEER, protocol.response(SESSION, 8, 0, SAMPLE)))
        self.assertEqual(gate.accept(PEER, protocol.request(SESSION, 9, 0)), (SESSION, 9))
        self.assertEqual(gate.counts, dict(accepted=2, wrong_sender=1, invalid=2,
                                         stale_session=1, replay=2,
                                         sensor_error=0, send_error=0))


class RuntimeTests(unittest.TestCase):
    def test_dropout_produces_timestamp_gap(self):
        clock = FakeClock()
        radio = FakeRadio(clock, dropped=(1,))
        store = FakeStore()
        counts = master.run_session(Mock(read=Mock(return_value=SAMPLE)), radio,
                                    store, clock, SESSION, lambda: clock.t >= 65)
        self.assertEqual([r[0] for r in store.rows], [0, 40, 60])
        self.assertEqual([s[3] for s in radio.sent], [0, 1, 2, 3])
        self.assertEqual(counts["paired"], 3)
        self.assertEqual(counts["timeout"], 1)
        self.assertEqual(store.final[1], counts)

    def test_storage_stall_rebases_schedule_without_catchup_burst(self):
        clock = FakeClock()
        radio = FakeRadio(clock)
        store = FakeStore()
        original_service = store.service

        def service(now, counts):
            original_service(now, counts)
            if len(store.services) == 1:
                clock.t += 33

        store.service = service
        master.run_session(Mock(read=Mock(return_value=SAMPLE)), radio,
                           store, clock, SESSION, lambda: clock.t >= 62)
        times = [r[0] for r in store.rows]
        self.assertEqual(times, [0, 36, 56])
        self.assertTrue(all(b - a >= 20 for a, b in zip(times, times[1:])))

    def test_blocked_ack_never_creates_late_rows(self):
        clock = FakeClock()
        radio = FakeRadio(clock, send_time=45)
        store = FakeStore()
        counts = master.run_session(Mock(read=Mock(return_value=SAMPLE)), radio,
                                    store, clock, SESSION, lambda: clock.t >= 95)
        self.assertEqual(store.rows, [])
        self.assertGreaterEqual(counts["timeout"], 2)
        self.assertGreaterEqual(counts["skipped_slots"], 2)
        self.assertTrue(all(b[0] - a[0] >= 20 for a, b in zip(radio.sent, radio.sent[1:])))

    def test_slow_sensor_expires_without_radio_send(self):
        clock = FakeClock()
        radio = FakeRadio(clock)
        store = FakeStore()

        def read():
            clock.t += 16
            return SAMPLE

        counts = master.run_session(Mock(read=read), radio, store, clock, SESSION,
                                    lambda: clock.t >= 17)
        self.assertEqual(radio.sent, [])
        self.assertEqual(store.rows, [])
        self.assertEqual(counts["timeout"], 1)

    def test_sensor_error_creates_gap_then_recovers(self):
        clock = FakeClock()
        radio = FakeRadio(clock)
        store = FakeStore()
        imu = Mock()
        imu.read.side_effect = [OSError("I2C"), SAMPLE]
        counts = master.run_session(imu, radio, store, clock, SESSION,
                                    lambda: clock.t >= 25)
        self.assertEqual([row[0] for row in store.rows], [20])
        self.assertEqual(counts["sensor_error"], 1)
        self.assertEqual(radio.sent[0][3], 1)

    def test_send_error_has_timeout_not_synthetic_row(self):
        clock = FakeClock()
        radio = FakeRadio(clock)
        radio.send = Mock(side_effect=OSError("radio"))
        store = FakeStore()
        counts = master.run_session(Mock(read=Mock(return_value=SAMPLE)), radio,
                                    store, clock, SESSION, lambda: clock.t >= 19)
        self.assertEqual(store.rows, [])
        self.assertEqual(counts["send_error"], 1)
        self.assertEqual(counts["timeout"], 1)

    def test_flood_draining_bounded_for_both_roles(self):
        for module in (master, slave):
            clock = FakeClock()
            radio = FakeRadio(clock)
            radio.receive = Mock(return_value=(OTHER, b"invalid"))
            imu = Mock(read=Mock(return_value=SAMPLE))
            if module is master:
                master.run_session(imu, radio, FakeStore(), clock, SESSION,
                                   lambda: clock.t >= 3)
            else:
                slave.run_session(imu, radio, clock, lambda: clock.t >= 3)
            self.assertEqual(radio.receive.call_count, 3 * module.MAX_DRAIN)

    def test_slave_reads_only_valid_new_requests(self):
        clock = FakeClock()
        radio = FakeRadio(clock)
        radio.queue = [(0, PEER, protocol.request(SESSION, 0, 5)),
                       (0, PEER, protocol.request(SESSION, 0, 5)),
                       (0, OTHER, protocol.request(SESSION, 1, 5)),
                       (0, PEER, protocol.request(OLD_SESSION, 1, 5)),
                       (0, PEER, protocol.request(SESSION, 2, 5))]
        imu = Mock(read=Mock(return_value=SAMPLE))
        counts = slave.run_session(imu, radio, clock, lambda: clock.t >= 2)
        self.assertEqual(imu.read.call_count, 2)
        self.assertEqual([s[3] for s in radio.sent], [0, 2])
        self.assertEqual([s[4] for s in radio.sent], [10000, 10000])
        self.assertEqual(counts["replay"], 1)
        self.assertEqual(counts["stale_session"], 1)
        self.assertEqual(counts["wrong_sender"], 1)

    def test_clock_extends_tick_wrap(self):
        times = iter([250, 254, 3, 10])
        fake = types.SimpleNamespace(ticks_ms=lambda: next(times),
                                     ticks_diff=lambda a, b: ((a - b + 128) % 256) - 128)
        with patch.dict(sys.modules, {"time": fake}):
            clock = Clock()
            self.assertEqual([clock.now_ms() for _ in range(3)], [4, 9, 16])

    def test_cleanup_attempts_every_resource(self):
        first, second = Mock(), Mock()
        first.close.side_effect = OSError("card removed")
        error = close_all(first, None, second)
        self.assertIsInstance(error, OSError)
        second.close.assert_called_once_with()

    def test_master_cleans_up_after_runtime_failure(self):
        with patch("app.master.Sensor") as sensor, patch("app.radio.Radio") as radio, \
                patch("app.storage.SDLogger") as storage, \
                patch("app.master.Clock", return_value=FakeClock()), \
                patch("app.master.run_session", side_effect=RuntimeError("stop")):
            with self.assertRaisesRegex(RuntimeError, "stop"):
                master.run(config())
            sensor.return_value.close.assert_called_once_with()
            radio.return_value.close.assert_called_once_with()
            storage.return_value.close.assert_called_once_with()

    def test_slave_cleans_up_after_runtime_failure(self):
        with patch("app.slave.Sensor") as sensor, patch("app.radio.Radio") as radio, \
                patch("app.slave.Clock", return_value=FakeClock()), \
                patch("app.slave.run_session", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                slave.run(config("slave"))
            sensor.return_value.close.assert_called_once_with()
            radio.return_value.close.assert_called_once_with()


class ConfigTests(unittest.TestCase):
    def test_v1_optional_none_and_v2_keys(self):
        cfg = config()
        self.assertEqual(validate(cfg, "master"), (PEER, None, None))
        cfg.update(iteration=2, pmk="01" * 16, lmk="02" * 16)
        self.assertEqual(validate(cfg, "master"), (PEER, b"\x01" * 16, b"\x02" * 16))

    def test_fail_closed_invalid_config(self):
        bad = [("role", "slave"), ("iteration", 3), ("channel", 0),
               ("channel", 12), ("channel", True), ("sample_rate_hz", 100),
               ("peer_mac", "ff" * 6), ("peer_mac", "00" * 6),
               ("peer_mac", "xx" * 6), ("pmk", "wrong"),
               ("imu_address", 105), ("pins", dict(DEFAULT_PINS, sd_miso=2))]
        for field, value in bad:
            cfg = config()
            cfg[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate(cfg, "master")
        with self.assertRaises(ValueError):
            validate(config(iteration=2), "master")


class HardwareAdapterTests(unittest.TestCase):
    def radio_modules(self):
        wlan, esp = Mock(), Mock()
        network = types.SimpleNamespace(STA_IF=0, WLAN=Mock(return_value=wlan))
        espnow = types.SimpleNamespace(ESPNow=Mock(return_value=esp))
        return network, espnow, wlan, esp

    def test_official_encrypted_unicast_and_nonblocking_receive(self):
        from app.radio import Radio
        network, espnow, wlan, esp = self.radio_modules()
        cfg = config(iteration=2)
        cfg.update(pmk="01" * 16, lmk="02" * 16)
        with patch.dict(sys.modules, dict(network=network, espnow=espnow)):
            radio = Radio(cfg)
            wlan.active.assert_called_once_with(True)
            wlan.disconnect.assert_called_once_with()
            wlan.config.assert_called_once_with(channel=6)
            esp.set_pmk.assert_called_once_with(b"\x01" * 16)
            esp.add_peer.assert_called_once_with(PEER, lmk=b"\x02" * 16,
                                                  channel=6, encrypt=True)
            radio.send(b"packet")
            esp.send.assert_called_once_with(PEER, b"packet", True)
            esp.irecv.return_value = (None, None)
            self.assertIsNone(radio.receive())
            esp.irecv.assert_called_once_with(0)
            radio.close()
            self.assertEqual(esp.active.call_args.args, (False,))
            self.assertEqual(wlan.active.call_args.args, (False,))

    def test_v1_is_explicitly_plaintext_with_none_keys(self):
        from app.radio import Radio
        network, espnow, wlan, esp = self.radio_modules()
        with patch.dict(sys.modules, dict(network=network, espnow=espnow)):
            radio = Radio(config())
            esp.set_pmk.assert_not_called()
            esp.add_peer.assert_called_once_with(PEER, channel=6, encrypt=False)
            radio.close()

    def test_encryption_failure_never_falls_back(self):
        from app.radio import Radio
        network, espnow, wlan, esp = self.radio_modules()
        esp.set_pmk.side_effect = OSError("unsupported")
        cfg = config(iteration=2)
        cfg.update(pmk="01" * 16, lmk="02" * 16)
        with patch.dict(sys.modules, dict(network=network, espnow=espnow)):
            with self.assertRaises(OSError):
                Radio(cfg)
            esp.add_peer.assert_not_called()
            self.assertEqual(wlan.active.call_args.args, (False,))

    def fake_i2c(self, identity=0x68):
        registers = {0x75: identity}
        bus = Mock()

        def read(address, register, length):
            if register == 0x3B:
                return struct.pack(">7h", 8192, -8192, 16384, 30000, 655, -655, 0)
            return bytes((registers.get(register, 0),))

        def write(address, register, value):
            registers[register] = value[0]

        bus.readfrom_mem.side_effect = read
        bus.writeto_mem.side_effect = write
        return bus, registers

    def test_mpu_identity_ranges_divider_and_si_conversion(self):
        bus, registers = self.fake_i2c()
        imu = MPU6050(bus, sleep_ms=lambda _: None)
        self.assertEqual([registers[r] for r in (0x6B, 0x1A, 0x19, 0x1B, 0x1C)],
                         [1, 3, 19, 8, 8])
        values = imu.read()
        expected = (9.80665, -9.80665, 19.6133,
                    10 * math.pi / 180, -10 * math.pi / 180, 0)
        for actual, value in zip(values, expected):
            self.assertAlmostEqual(actual, value)
        bus.readfrom_mem.assert_called_with(104, 0x3B, 14)
        imu.close()
        self.assertEqual(registers[0x6B], 0x40)

    def test_mpu_bad_identity_and_short_read_fail(self):
        bus, _ = self.fake_i2c(identity=0x70)
        with self.assertRaisesRegex(ValueError, "WHO_AM_I"):
            MPU6050(bus, sleep_ms=lambda _: None)
        bus, _ = self.fake_i2c()
        imu = MPU6050(bus, sleep_ms=lambda _: None)
        bus.readfrom_mem.return_value = b"short"
        bus.readfrom_mem.side_effect = None
        with self.assertRaisesRegex(OSError, "short"):
            imu.read()

    def test_sd_spi_variant_mount_and_cleanup(self):
        machine = types.SimpleNamespace(SDCard=Mock(), Pin=lambda n: n)
        with patch.dict(sys.modules, dict(machine=machine)), \
                patch("app.storage.os.mount", create=True) as mount, \
                patch("app.storage.os.umount", create=True) as unmount, \
                patch("app.storage.CSVLogger") as csv_logger:
            logger = SDLogger(config(), {"session": "test"})
            machine.SDCard.assert_called_once_with(
                slot=2, sck=6, mosi=7, miso=3, cs=10, freq=10000000)
            mount.assert_called_once_with(machine.SDCard.return_value, "/sd")
            csv_logger.assert_called_once_with("/sd", {"session": "test"})
            logger.close()
            unmount.assert_called_once_with("/sd")
            machine.SDCard.return_value.deinit.assert_called_once_with()

    def test_sd_unsupported_build_has_actionable_failure(self):
        with patch.dict(sys.modules, dict(machine=types.SimpleNamespace())):
            with self.assertRaisesRegex(RuntimeError, "SPI slot=2"):
                SDLogger(config(), {})
        machine = types.SimpleNamespace(SDCard=Mock(side_effect=TypeError), Pin=lambda n: n)
        with patch.dict(sys.modules, dict(machine=machine)):
            with self.assertRaisesRegex(RuntimeError, "verify C3"):
                SDLogger(config(), {})


class StorageTests(unittest.TestCase):
    def test_exact_schema_actual_timestamp_gaps_and_exclusive_names(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "log000001.csv"
            existing.write_text("do not overwrite")
            (Path(directory) / "log000002.health.jsonl").write_text("reserved")
            logger = CSVLogger(directory, {"session": "test"})
            self.assertTrue(logger.path.endswith("log000003.csv"))
            logger.append((100,) + SAMPLE + SAMPLE)
            logger.append((160,) + SAMPLE + SAMPLE)
            logger.finish(170, {"paired": 2, "timeout": 2})
            logger.close()
            with open(logger.path) as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], list(COLUMNS))
            self.assertEqual(rows[0], ["time_ms", "thigh_ax", "thigh_ay", "thigh_az",
                                       "thigh_gx", "thigh_gy", "thigh_gz", "shank_ax",
                                       "shank_ay", "shank_az", "shank_gx", "shank_gy",
                                       "shank_gz"])
            self.assertEqual([r[0] for r in rows[1:]], ["100", "160"])
            self.assertEqual(existing.read_text(), "do not overwrite")
            records = [json.loads(line) for line in Path(
                logger.path.replace(".csv", ".health.jsonl")).read_text().splitlines()]
            self.assertEqual(records[-1]["counters"], {"paired": 2, "timeout": 2})
            self.assertEqual(records[-1]["metadata"]["session"], "test")
            self.assertEqual(records[-1]["time_ms"], 170)

    def test_flush_small_buffer_and_rate_limited_health(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = CSVLogger(directory, {})
            logger.append((0,) + SAMPLE + SAMPLE)
            logger.service(999, {})
            self.assertEqual(len(logger.buffer), 1)
            logger.service(1000, {})
            self.assertEqual(logger.buffer, [])
            self.assertEqual(len(Path(logger.path).read_text().splitlines()), 2)
            for now in (1001, 4999, 5000, 5001, 9999, 10000):
                logger.service(now, {"paired": 1})
            logger.finish(10000, {"paired": 1})
            logger.close()
            records = [json.loads(line) for line in Path(
                logger.path.replace(".csv", ".health.jsonl")).read_text().splitlines()]
            self.assertEqual([r["event"] for r in records],
                             ["start", "running", "running", "stop"])

    def test_invalid_or_nonincreasing_rows_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = CSVLogger(directory, {})
            logger.append((20,) + SAMPLE + SAMPLE)
            for row in ((20,) + SAMPLE + SAMPLE, (0,) + SAMPLE + SAMPLE,
                        (40,) + SAMPLE, (40,) + (math.nan,) * 12):
                with self.assertRaises(ValueError):
                    logger.append(row)
            logger.close()

    def test_partial_write_is_not_retried_on_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = CSVLogger(directory, {}, buffer_rows=1)
            real_csv = logger.csv
            fake = Mock()
            fake.write.return_value = 3
            logger.csv = fake
            try:
                with self.assertRaisesRegex(OSError, "short SD"):
                    logger.append((0,) + SAMPLE + SAMPLE)
                logger.close()
                fake.write.assert_called_once()
            finally:
                real_csv.close()

    def test_app_has_no_print_calls(self):
        for path in (IOT / "app").glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                self.assertFalse(isinstance(node, ast.Call) and
                                 isinstance(node.func, ast.Name) and
                                 node.func.id == "print", str(path))


if __name__ == "__main__":
    unittest.main()
