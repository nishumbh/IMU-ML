"""KBX1 secure log tests: format rules from docs/SD_LOG_FORMAT.md."""
import contextlib
import io
import json
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from knee_rehab import secure_log as kbx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kbx"
PASSPHRASE = "correct horse battery staple"
SALT = bytes(range(16))
PREFIX = bytes.fromhex("a55ac33c")
HEADER_LEN = kbx.HEADER_LEN


def write_session(path, passphrase=PASSPHRASE, *, samples=120, rate=50, extra=None):
    """Write a deterministic session; returns the list of IMU seq values written."""
    with kbx.KBXWriter(path, passphrase, device_id="TEST-POD", session_number=7,
                       sample_rate_hz=rate, firmware_version="unit test",
                       wall_clock_unix_s=1767225600, salt=SALT, nonce_prefix=PREFIX,
                       fsync=False) as writer:
        writer.write_event(0)
        for seq in range(samples):
            channels = [((seq * 13 + i * 97) % 3000) - 1500 for i in range(12)]
            writer.write_imu(seq, channels, 0)
            if seq == 60:
                writer.write_infer(60, 2, (0.2, 0.7, 0.1), 1)
        if extra:
            extra(writer)
        writer.write_event(1, 3800, samples)
    return list(range(samples))


def chunk_starts(data):
    starts, pos = [], HEADER_LEN
    while pos < len(data):
        starts.append(pos)
        (ct_len,) = struct.unpack_from("<I", data, pos)
        pos += 32 + ct_len
    return starts


class SecureLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.dir = Path(cls.temp.name)
        cls.path = cls.dir / "S0007.kbx"
        cls.seqs = write_session(cls.path)
        cls.data = cls.path.read_bytes()
        cls.session = kbx.read_kbx(cls.path, PASSPHRASE)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def tampered(self, offset, mask=0x01):
        data = bytearray(self.data)
        data[offset] ^= mask
        return bytes(data)

    # --- round trip and records

    def test_round_trip_records_and_header(self):
        session = self.session
        self.assertTrue(session.verified)
        self.assertFalse(session.truncated)
        self.assertEqual(session.header.device_id, "TEST-POD")
        self.assertEqual(session.header.session_number, 7)
        self.assertEqual(session.header.sample_rate_hz, 50)
        self.assertEqual(session.header.wall_clock_unix_s, 1767225600)
        self.assertEqual(session.header.firmware_version, "unit test")
        self.assertEqual([r.seq for r in session.imu], self.seqs)
        infer = [r for r in session.records if isinstance(r, kbx.INFERRecord)]
        self.assertEqual(len(infer), 1)
        self.assertEqual((infer[0].window_seq, infer[0].exercise, infer[0].label), (60, 2, 1))
        self.assertAlmostEqual(infer[0].probabilities[1], 0.7, places=6)
        events = [r for r in session.records if isinstance(r, kbx.EventRecord)]
        self.assertEqual([(e.kind_name, e.payload) for e in events], [("start", 0), ("stop", 120)])
        self.assertEqual(session.chunks, 3)  # 50 + 50 + 20 IMU records, then the tail

    def test_channel_order_and_csv_export_match_firmware_header(self):
        self.assertEqual(kbx.CSV_COLUMNS[:4], ("time_ms", "thigh_ax", "thigh_ay", "thigh_az"))
        self.assertEqual(kbx.CSV_COLUMNS[-1], "shank_gz")
        text = kbx.session_to_csv(self.session)
        lines = text.splitlines()
        self.assertEqual(lines[0], ",".join(kbx.CSV_COLUMNS))
        self.assertEqual(len(lines), 1 + len(self.seqs))
        first = lines[1].split(",")
        self.assertEqual(float(first[0]), 0.0)
        self.assertAlmostEqual(float(lines[2].split(",")[0]), 20.0)  # seq 1 at 50 Hz
        raw0 = self.session.imu[0].channels[0]
        self.assertAlmostEqual(float(first[1]), raw0 * self.session.header.accel_lsb_mps2, places=6)

    def test_streaming_reader_yields_one_chunk_at_a_time(self):
        with open(self.path, "rb") as stream:
            reader = kbx.KBXReader(stream, PASSPHRASE)
            per_chunk = [len(chunk) for chunk in reader.chunks()]
        self.assertEqual(len(per_chunk), 3)
        self.assertEqual(sum(per_chunk), len(self.session.records))

    def test_csv_to_kbx_round_trip_within_quantisation(self):
        with TemporaryDirectory() as tmp:
            csv_in = Path(tmp) / "in.csv"
            rows = [",".join(kbx.CSV_COLUMNS)]
            for seq in range(60):
                values = [seq * 20.0] + [(i - 6) * 0.05 + seq * 0.001 for i in range(12)]
                rows.append(",".join(f"{v:.6f}" for v in values))
            csv_in.write_text("\n".join(rows) + "\n")
            out = Path(tmp) / "S0001.kbx"
            kbx.csv_to_kbx(csv_in, out, PASSPHRASE, device_id="TEST-POD", session_number=1,
                           sample_rate_hz=50, firmware_version="unit test")
            session = kbx.read_kbx(out, PASSPHRASE)
            back = kbx.session_to_csv(session).splitlines()
            self.assertEqual(len(back), 61)
            self.assertEqual(float(back[11].split(",")[0]), 200.0)
            lsb = session.header.accel_lsb_mps2
            original = float(rows[11].split(",")[1])
            self.assertLessEqual(abs(float(back[11].split(",")[1]) - original), lsb / 2 + 1e-9)

    def test_csv_to_kbx_refuses_overflow_and_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            csv_in = Path(tmp) / "in.csv"
            csv_in.write_text(",".join(kbx.CSV_COLUMNS) + "\n0,1e6,0,0,0,0,0,0,0,0,0,0,0\n")
            out = Path(tmp) / "S0002.kbx"
            with self.assertRaises(kbx.KBXFormatError):
                kbx.csv_to_kbx(csv_in, out, PASSPHRASE, device_id="TEST-POD", session_number=2,
                               sample_rate_hz=50, firmware_version="unit test")
            self.assertFalse(out.exists())

    def test_writer_never_overwrites(self):
        with self.assertRaises(FileExistsError):
            kbx.KBXWriter(self.path, PASSPHRASE, device_id="TEST-POD", session_number=7,
                          sample_rate_hz=50, firmware_version="x", salt=SALT,
                          nonce_prefix=PREFIX)

    # --- passphrases

    def test_wrong_passphrase_raises_clear_auth_error(self):
        with self.assertRaises(kbx.KBXAuthError) as ctx:
            kbx.read_kbx(self.path, "correct horse battery stapl3")
        self.assertIn("wrong passphrase", str(ctx.exception))
        self.assertIsInstance(ctx.exception, ValueError)

    def test_passphrase_is_nfc_normalised(self):
        composed, decomposed = "café secret passphrase", "café secret passphrase"
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "S0003.kbx"
            with kbx.KBXWriter(path, composed, device_id="TEST-POD", session_number=3,
                               sample_rate_hz=50, firmware_version="x") as writer:
                writer.write_event(0)
            self.assertTrue(kbx.read_kbx(path, decomposed).verified)

    def test_writer_enforces_passphrase_rules(self):
        with TemporaryDirectory() as tmp:
            for bad in ("short", "", "twelve chars\n", "x" * 1025):
                with self.subTest(bad=bad[:12]):
                    with self.assertRaises(kbx.KBXPassphraseError):
                        kbx.KBXWriter(Path(tmp) / "bad.kbx", bad, device_id="TEST-POD",
                                      session_number=1, sample_rate_hz=50, firmware_version="x")
            self.assertFalse((Path(tmp) / "bad.kbx").exists())

    def test_file_with_no_chunks_is_not_verified(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "S0004.kbx"
            with kbx.KBXWriter(path, PASSPHRASE, device_id="TEST-POD", session_number=4,
                               sample_rate_hz=50, firmware_version="x", fsync=False):
                pass
            session = kbx.read_kbx(path, "any other passphrase")
            self.assertFalse(session.verified)
            self.assertEqual(session.records, [])

    # --- integrity

    def test_single_byte_tamper_in_every_chunk_fails(self):
        starts = chunk_starts(self.data)
        for index, start in enumerate(starts):
            ct_len = struct.unpack_from("<I", self.data, start)[0]
            for offset in (start + 16, start + 16 + ct_len // 2, start + 16 + ct_len + 3):
                with self.subTest(chunk=index, offset=offset):
                    with self.assertRaises(kbx.KBXAuthError) as ctx:
                        kbx.read_kbx_bytes(self.tampered(offset), PASSPHRASE)
                    self.assertIn(f"Chunk {index} failed authentication", str(ctx.exception))

    def test_no_bit_flip_in_chunk_area_yields_silent_success(self):
        for offset in range(HEADER_LEN, len(self.data)):
            with self.subTest(offset=offset):
                try:
                    session = kbx.read_kbx_bytes(self.tampered(offset), PASSPHRASE)
                except kbx.KBXError:
                    continue
                # Only a length-field change that pushes the last chunk past EOF may pass,
                # and it must then be flagged as truncated.
                self.assertTrue(session.truncated)

    def test_header_is_authenticated(self):
        # Offsets: session (24), sample rate (26), accel (28), salt (36), prefix (56),
        # wall clock (60), firmware (68), device id (8).
        for offset in (8, 24, 26, 28, 36, 56, 60, 68):
            with self.subTest(offset=offset):
                with self.assertRaises(kbx.KBXError):
                    kbx.read_kbx_bytes(self.tampered(offset), PASSPHRASE)

    def test_reserved_header_bytes_and_bad_versions_rejected(self):
        bad_cases = {"magic": (0, 0x20), "version": (4, 0x01), "flags": (5, 0x01),
                     "reserved": (92, 0x01), "iterations": (52, 0x01)}
        for name, (offset, mask) in bad_cases.items():
            with self.subTest(field=name):
                with self.assertRaises(kbx.KBXFormatError):
                    kbx.read_kbx_bytes(self.tampered(offset, mask), PASSPHRASE)

    def test_header_incomplete_is_format_error(self):
        with self.assertRaises(kbx.KBXFormatError):
            kbx.read_kbx_bytes(self.data[:50], PASSPHRASE)

    # --- truncation

    def test_truncated_final_chunk_is_recovered_with_flag(self):
        starts = chunk_starts(self.data)
        last = starts[-1]
        for cut in (last + 1, last + 10, len(self.data) - 1):
            with self.subTest(cut=cut):
                session = kbx.read_kbx_bytes(self.data[:cut], PASSPHRASE)
                self.assertTrue(session.truncated)
                self.assertTrue(session.verified)
                self.assertEqual(session.chunks, 2)
                # Recovered records are an exact prefix of the full session.
                self.assertEqual(session.records, self.session.records[:len(session.records)])
                self.assertLess(len(session.records), len(self.session.records))

    def test_cut_on_chunk_boundary_is_clean_not_truncated(self):
        last = chunk_starts(self.data)[-1]
        session = kbx.read_kbx_bytes(self.data[:last], PASSPHRASE)
        self.assertFalse(session.truncated)
        self.assertEqual(session.chunks, 2)

    def test_zero_filled_tail_is_truncation(self):
        session = kbx.read_kbx_bytes(self.data + b"\0" * 64, PASSPHRASE)
        self.assertTrue(session.truncated)
        self.assertEqual(len(session.records), len(self.session.records))

    def test_nonzero_garbage_after_chunks_is_format_error(self):
        with self.assertRaises(kbx.KBXFormatError):
            kbx.read_kbx_bytes(self.data + b"\xff" * 64, PASSPHRASE)

    # --- records

    def test_unknown_record_type_is_skipped(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "S0005.kbx"
            write_session(path, samples=10,
                          extra=lambda w: w.write_raw_record(0x70, b"\x01\x02\x03"))
            session = kbx.read_kbx(path, PASSPHRASE)
            self.assertEqual(session.unknown_records, 1)
            self.assertEqual(len(session.imu), 10)
            self.assertTrue(session.verified)

    def test_known_record_with_appended_fields_is_accepted(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "S0006.kbx"
            with kbx.KBXWriter(path, PASSPHRASE, device_id="TEST-POD", session_number=6,
                               sample_rate_hz=50, firmware_version="x", salt=SALT,
                               nonce_prefix=PREFIX, fsync=False) as writer:
                writer.write_raw_record(kbx.REC_EVENT, struct.pack("<BHI", 3, 3500, 0) + b"\xaa\xbb")
            session = kbx.read_kbx(path, PASSPHRASE)
            self.assertEqual(session.records[0].battery_mv, 3500)

    def test_duplicate_imu_sequence_is_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "S0008.kbx"
            body = struct.pack("<IB12h", 5, 0, *([0] * 12))
            with kbx.KBXWriter(path, PASSPHRASE, device_id="TEST-POD", session_number=8,
                               sample_rate_hz=50, firmware_version="x", salt=SALT,
                               nonce_prefix=PREFIX, fsync=False) as writer:
                writer.write_imu(5, [0] * 12)
                writer.write_raw_record(kbx.REC_IMU, body)
            with self.assertRaises(kbx.KBXFormatError):
                kbx.read_kbx(path, PASSPHRASE)

    def test_infer_probabilities_must_be_finite_and_in_range(self):
        with self.assertRaises(kbx.KBXFormatError):
            with kbx.KBXWriter(self.dir / "unused.kbx", PASSPHRASE, device_id="TEST-POD",
                               session_number=9, sample_rate_hz=50, firmware_version="x",
                               salt=SALT, nonce_prefix=PREFIX) as writer:
                writer.write_infer(1, 1, (0.5, float("nan"), 0.1), 0)

    # --- fixtures and CLI

    def test_fixture_vectors_match_expected_json(self):
        expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        valid = kbx.read_kbx(FIXTURES / "valid.kbx", expected["passphrase"])
        self.assertEqual(valid.chunks, expected["valid"]["chunks"])
        self.assertEqual(valid.unknown_records, expected["valid"]["unknown_records"])
        self.assertEqual(valid.link_loss_samples, expected["valid"]["link_loss_samples"])
        self.assertEqual(valid.header.to_json(), expected["valid"]["header"])
        records = kbx._records_json(valid)
        self.assertEqual(records, expected["valid"]["records"])

        truncated = kbx.read_kbx(FIXTURES / "truncated.kbx", expected["passphrase"])
        self.assertTrue(truncated.truncated)
        self.assertEqual(truncated.chunks, expected["truncated"]["chunks"])
        self.assertEqual(len(truncated.records), expected["truncated"]["record_count"])

        with self.assertRaises(kbx.KBXAuthError):
            kbx.read_kbx(FIXTURES / "tampered.kbx", expected["passphrase"])
        self.assertEqual(expected["tampered"]["chunk"], 1)

    def test_make_fixtures_is_deterministic(self):
        with TemporaryDirectory() as tmp:
            kbx.make_fixtures(Path(tmp))
            for name in ("valid.kbx", "truncated.kbx", "tampered.kbx", "expected.json"):
                with self.subTest(name=name):
                    self.assertEqual((Path(tmp) / name).read_bytes(), (FIXTURES / name).read_bytes())

    def test_cli_decrypt_round_trip_refuses_overwrite_and_reads_env(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "decoded.csv"
            old = os.environ.get(kbx.ENV_PASSPHRASE)
            os.environ[kbx.ENV_PASSPHRASE] = PASSPHRASE
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(kbx.main(["decrypt", str(FIXTURES / "valid.kbx"),
                                               "--output", str(out)]), 0)
                    self.assertEqual(kbx.main(["decrypt", str(FIXTURES / "valid.kbx"),
                                               "--output", str(out)]), 1)
                self.assertEqual(out.read_text().splitlines()[0], ",".join(kbx.CSV_COLUMNS))
                with contextlib.redirect_stdout(io.StringIO()) as buffer:
                    self.assertEqual(kbx.main(["inspect", str(FIXTURES / "valid.kbx")]), 0)
                self.assertEqual(json.loads(buffer.getvalue())["record_counts"]["imu"], 150)
            finally:
                if old is None:
                    os.environ.pop(kbx.ENV_PASSPHRASE, None)
                else:
                    os.environ[kbx.ENV_PASSPHRASE] = old


class KBXManifestTests(unittest.TestCase):
    """data_loader reads kbx records through passphrase_env, never a stored passphrase."""

    ENV = "KBX_TEST_PASSPHRASE_FOR_LOADER"

    def spec(self, path):
        return {"path": path, "format": "kbx", "passphrase_env": self.ENV,
                "dataset": "local-kbx", "subject_id": "local", "recording_id": "fixture",
                "label": "Unassessed", "sample_rate_hz": 50,
                "timestamps": {"column": "time_ms", "unit": "ms"},
                "channels": [
                    {"name": f"thigh_accelerometer_{a}", "column": f"thigh_a{a}",
                     "kind": "accelerometer", "unit": "m/s^2"} for a in "xyz"
                ] + [
                    {"name": f"thigh_gyroscope_{a}", "column": f"thigh_g{a}",
                     "kind": "gyroscope", "unit": "rad/s"} for a in "xyz"
                ]}

    def load(self, path):
        from knee_rehab.data_loader import load_recording
        return load_recording(self.spec(path), FIXTURES)

    def test_manifest_kbx_record_loads_and_reports_truncation(self):
        old = os.environ.get(self.ENV)
        os.environ[self.ENV] = PASSPHRASE
        try:
            valid = self.load("valid.kbx")
            self.assertEqual(valid.data.shape, (150, 6))
            self.assertFalse(valid.metadata["kbx_truncated"])
            self.assertEqual(valid.metadata["kbx_link_loss_samples"], 1)
            truncated = self.load("truncated.kbx")
            self.assertTrue(truncated.metadata["kbx_truncated"])
            self.assertEqual(truncated.data.shape[0], 150)  # STOP lost; IMU samples all kept
            self.assertNotIn(PASSPHRASE, json.dumps(self.spec("valid.kbx")))
        finally:
            if old is None:
                os.environ.pop(self.ENV, None)
            else:
                os.environ[self.ENV] = old

    def test_manifest_kbx_needs_env_and_rejects_wrong_passphrase(self):
        os.environ.pop(self.ENV, None)
        with self.assertRaisesRegex(ValueError, "not set"):
            self.load("valid.kbx")
        os.environ[self.ENV] = "not the right passphrase"
        try:
            with self.assertRaises(kbx.KBXAuthError):
                self.load("valid.kbx")
        finally:
            os.environ.pop(self.ENV, None)


if __name__ == "__main__":
    unittest.main()
