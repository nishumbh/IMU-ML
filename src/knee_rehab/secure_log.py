"""KBX1 encrypted SD session logs: reference writer, reader and CLI.

The byte-level contract is docs/SD_LOG_FORMAT.md, which is the source of truth.
Research logging only; not a medical device.

    python -m knee_rehab.secure_log encrypt --csv IN.csv --output S0001.kbx ...
    python -m knee_rehab.secure_log decrypt S0001.kbx --output OUT.csv
    python -m knee_rehab.secure_log inspect S0001.kbx [--header-only]
    python -m knee_rehab.secure_log make-fixtures [--out-dir DIR]

The passphrase is read from the KBX_PASSPHRASE environment variable or an
interactive prompt. It is never accepted as a command-line argument.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from functools import lru_cache
import getpass
import io
import json
import math
import os
from pathlib import Path
import struct
import sys
import unicodedata
from typing import BinaryIO, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"KBX1"
FORMAT_VERSION = 1
HEADER_LEN = 96
PBKDF2_ITERATIONS = 600_000
TAG_LEN = 16
MAX_CHUNK_CIPHERTEXT = 65_536
CHUNK_FLUSH_BYTES = 32 * 1024
MIN_PASSPHRASE_CHARS = 12
MAX_PASSPHRASE_BYTES = 1024
ENV_PASSPHRASE = "KBX_PASSPHRASE"
REJECT_LABEL = 255
REC_IMU, REC_INFER, REC_EVENT = 0x01, 0x02, 0x03
IMU_BODY_LEN, INFER_BODY_LEN, EVENT_BODY_LEN = 29, 18, 7
EVENT_KINDS = {0: "start", 1: "stop", 2: "exercise_change", 3: "low_battery"}
EXERCISES = {0: "none", 1: "squat", 2: "seated_leg_extension", 3: "gait"}
DEFAULT_ACCEL_LSB = 9.80665 / 8192.0
DEFAULT_GYRO_LSB = (math.pi / 180.0) / 65.536
CSV_COLUMNS = ("time_ms",) + tuple(
    f"{sensor}_{kind}{axis}" for sensor in ("thigh", "shank")
    for kind in ("a", "g") for axis in "xyz")

# Header: offsets 0..95, little-endian, no padding (see docs/SD_LOG_FORMAT.md section 4).
_HEADER = struct.Struct("<4sBBH16sHHff16sI4sQ24s4s")
assert _HEADER.size == HEADER_LEN


class KBXError(ValueError):
    """Base error. A ValueError so HTTP and loader callers can report it as bad input."""


class KBXFormatError(KBXError):
    """The file does not follow the KBX1 layout."""


class KBXAuthError(KBXError):
    """A chunk failed its GCM tag: wrong passphrase, or tampered/corrupted data."""


class KBXPassphraseError(KBXError):
    """The passphrase violates the rules in docs/SD_LOG_FORMAT.md section 2."""


# ---------------------------------------------------------------- passphrases

def normalize_passphrase(passphrase: str) -> bytes:
    """Reader-side normalisation: NFC, UTF-8. No length rule (see spec section 2)."""
    if not isinstance(passphrase, str):
        raise KBXPassphraseError("Passphrase must be text.")
    text = unicodedata.normalize("NFC", passphrase)
    if not text:
        raise KBXPassphraseError("Passphrase must not be empty.")
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise KBXPassphraseError("Passphrase is not valid Unicode text.") from exc


def validate_passphrase(passphrase: str) -> bytes:
    """Tool-side rules for creating files: NFC, at least 12 characters, control-free."""
    data = normalize_passphrase(passphrase)
    text = data.decode("utf-8")
    if len(text) < MIN_PASSPHRASE_CHARS:
        raise KBXPassphraseError(
            f"Passphrase must be at least {MIN_PASSPHRASE_CHARS} characters.")
    if len(data) > MAX_PASSPHRASE_BYTES:
        raise KBXPassphraseError(f"Passphrase exceeds {MAX_PASSPHRASE_BYTES} UTF-8 bytes.")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise KBXPassphraseError("Passphrase must not contain control characters.")
    return data


@lru_cache(maxsize=4)
def _derive_key(passphrase_bytes: bytes, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(passphrase_bytes)


# ---------------------------------------------------------------- header

@dataclass(frozen=True)
class Header:
    device_id: str
    session_number: int
    sample_rate_hz: int
    accel_lsb_mps2: float
    gyro_lsb_radps: float
    salt: bytes
    pbkdf2_iterations: int
    nonce_prefix: bytes
    wall_clock_unix_s: int
    firmware_version: str
    raw: bytes = field(repr=False, compare=False)

    def to_json(self) -> dict:
        return {
            "magic": MAGIC.decode("ascii"), "format_version": FORMAT_VERSION,
            "device_id": self.device_id, "session_number": self.session_number,
            "sample_rate_hz": self.sample_rate_hz,
            "accel_lsb_mps2": self.accel_lsb_mps2, "gyro_lsb_radps": self.gyro_lsb_radps,
            "pbkdf2_salt": self.salt.hex(), "pbkdf2_iterations": self.pbkdf2_iterations,
            "nonce_prefix": self.nonce_prefix.hex(),
            "wall_clock_unix_s": self.wall_clock_unix_s,
            "firmware_version": self.firmware_version,
        }


def _pack_header(*, device_id, session_number, sample_rate_hz, accel_lsb, gyro_lsb,
                 salt, nonce_prefix, firmware_version, wall_clock_unix_s=0,
                 iterations=PBKDF2_ITERATIONS) -> bytes:
    if not isinstance(device_id, str) or not 1 <= len(device_id) <= 16 or not all(
            ch.isascii() and (ch.isalnum() or ch in "._-") for ch in device_id):
        raise KBXFormatError("device_id must be 1-16 ASCII characters from [A-Za-z0-9._-].")
    if not isinstance(session_number, int) or not 0 <= session_number <= 9999:
        raise KBXFormatError("session_number must be 0-9999.")
    if not isinstance(sample_rate_hz, int) or not 1 <= sample_rate_hz <= 1000:
        raise KBXFormatError("sample_rate_hz must be an integer 1-1000.")
    for name, value in (("accel_lsb", accel_lsb), ("gyro_lsb", gyro_lsb)):
        if not math.isfinite(value) or value <= 0:
            raise KBXFormatError(f"{name} must be finite and positive.")
    if len(salt) != 16 or len(nonce_prefix) != 4:
        raise KBXFormatError("salt must be 16 bytes and nonce_prefix 4 bytes.")
    if not isinstance(wall_clock_unix_s, int) or not 0 <= wall_clock_unix_s < 2**64:
        raise KBXFormatError("wall_clock_unix_s must be a u64 integer (0 = unknown).")
    firmware = firmware_version.encode("ascii", errors="strict")
    if not 1 <= len(firmware) <= 24 or not all(0x20 <= b < 0x7F for b in firmware):
        raise KBXFormatError("firmware_version must be 1-24 printable ASCII bytes.")
    return _HEADER.pack(MAGIC, FORMAT_VERSION, 0, HEADER_LEN,
                        device_id.encode("ascii").ljust(16, b"\0"), session_number,
                        sample_rate_hz, accel_lsb, gyro_lsb, salt, iterations,
                        nonce_prefix, wall_clock_unix_s, firmware.ljust(24, b"\0"),
                        b"\0" * 4)


def parse_header(data: bytes) -> Header:
    """Parse and check the 96-byte header. Does not derive a key."""
    if len(data) < HEADER_LEN:
        raise KBXFormatError("Header incomplete: not a usable KBX session.")
    raw = bytes(data[:HEADER_LEN])
    (magic, version, flags, length, device, session, rate, accel, gyro, salt, iterations,
     prefix, wall, firmware, reserved) = _HEADER.unpack(raw)
    if magic != MAGIC:
        raise KBXFormatError("Not a KBX file (bad magic).")
    if version != FORMAT_VERSION:
        raise KBXFormatError(f"Unsupported KBX format version {version}; this reader supports 1.")
    if flags != 0 or length != HEADER_LEN or reserved != b"\0" * 4:
        raise KBXFormatError("Header reserved fields are not zero or header_length is wrong.")
    if iterations != PBKDF2_ITERATIONS:
        raise KBXFormatError(f"Unsupported PBKDF2 iteration count {iterations}.")
    if not 1 <= rate <= 1000 or not session <= 9999:
        raise KBXFormatError("Header sample rate or session number out of range.")
    if not (math.isfinite(accel) and accel > 0 and math.isfinite(gyro) and gyro > 0):
        raise KBXFormatError("Header scale factors must be finite and positive.")
    device_text = device.rstrip(b"\0")
    firmware_text = firmware.rstrip(b"\0")
    if (not device_text or b"\0" in device_text or not all(
            0x21 <= b < 0x7F and (chr(b).isalnum() or chr(b) in "._-") for b in device_text)
            or device[len(device_text):] != b"\0" * (16 - len(device_text))):
        raise KBXFormatError("Header device_id is not valid.")
    if (not firmware_text or not all(0x20 <= b < 0x7F for b in firmware_text)
            or firmware[len(firmware_text):] != b"\0" * (24 - len(firmware_text))):
        raise KBXFormatError("Header firmware_version is not valid.")
    return Header(device_id=device_text.decode("ascii"), session_number=session,
                  sample_rate_hz=rate, accel_lsb_mps2=accel, gyro_lsb_radps=gyro,
                  salt=bytes(salt), pbkdf2_iterations=iterations,
                  nonce_prefix=bytes(prefix), wall_clock_unix_s=wall,
                  firmware_version=firmware_text.decode("ascii"), raw=raw)


# ---------------------------------------------------------------- records

@dataclass(frozen=True)
class IMURecord:
    seq: int
    flags: int
    channels: tuple[int, ...]  # 12 raw int16 counts, firmware CSV order

    @property
    def link_loss(self) -> bool:
        return bool(self.flags & 0x01)


@dataclass(frozen=True)
class INFERRecord:
    window_seq: int
    exercise: int
    probabilities: tuple[float, float, float]
    label: int  # 0, 1, 2, or REJECT_LABEL (255)


@dataclass(frozen=True)
class EventRecord:
    kind: int
    battery_mv: int
    payload: int

    @property
    def kind_name(self) -> str:
        return EVENT_KINDS.get(self.kind, "unknown")


@dataclass
class Session:
    header: Header
    records: list = field(default_factory=list)
    truncated: bool = False
    chunks: int = 0
    unknown_records: int = 0
    verified: bool = False  # True once at least one chunk authenticated

    @property
    def imu(self) -> list[IMURecord]:
        return [r for r in self.records if isinstance(r, IMURecord)]

    @property
    def link_loss_samples(self) -> int:
        return sum(1 for r in self.imu if r.link_loss)


def _encode_record(rtype: int, body: bytes) -> bytes:
    return bytes([rtype]) + struct.pack("<H", len(body)) + body


def _check_probabilities(values) -> None:
    for value in values:
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise KBXFormatError("INFER probabilities must be finite and within [0, 1].")


def _parse_records(plain: bytes, state: "KBXReader") -> list:
    records, pos, end = [], 0, len(plain)
    while pos < end:
        if end - pos < 3:
            raise KBXFormatError("Record header is cut short inside an authenticated chunk.")
        rtype = plain[pos]
        (length,) = struct.unpack_from("<H", plain, pos + 1)
        start = pos + 3
        if start + length > end:
            raise KBXFormatError("Record body runs past the end of its chunk.")
        body = plain[start:start + length]
        pos = start + length
        if rtype == REC_IMU:
            if length < IMU_BODY_LEN:
                raise KBXFormatError("IMU record body too short.")
            seq, flags, *channels = struct.unpack_from("<IB12h", body, 0)
            if state.last_seq is not None and seq <= state.last_seq:
                raise KBXFormatError(f"IMU seq {seq} is not strictly increasing.")
            state.last_seq = seq
            records.append(IMURecord(seq, flags, tuple(channels)))
        elif rtype == REC_INFER:
            if length < INFER_BODY_LEN:
                raise KBXFormatError("INFER record body too short.")
            window_seq, exercise, p0, p1, p2, label = struct.unpack_from("<IBfffB", body, 0)
            _check_probabilities((p0, p1, p2))
            if exercise not in EXERCISES or label not in (0, 1, 2, REJECT_LABEL):
                raise KBXFormatError("INFER exercise or label out of range.")
            records.append(INFERRecord(window_seq, exercise, (p0, p1, p2), label))
        elif rtype == REC_EVENT:
            if length < EVENT_BODY_LEN:
                raise KBXFormatError("EVENT record body too short.")
            kind, battery, payload = struct.unpack_from("<BHI", body, 0)
            records.append(EventRecord(kind, battery, payload))
        else:
            state.unknown_records += 1
    return records


# ---------------------------------------------------------------- reading

class KBXReader:
    """Chunk-by-chunk decryption over a binary stream (spec section 6)."""

    def __init__(self, stream: BinaryIO, passphrase: str):
        self._stream = stream
        self.header = parse_header(stream.read(HEADER_LEN))
        self._passphrase = normalize_passphrase(passphrase)
        self._aead = AESGCM(_derive_key(self._passphrase, self.header.salt,
                                        self.header.pbkdf2_iterations))
        self._counter = 0
        self.last_seq: int | None = None
        self.unknown_records = 0
        self.truncated = False
        self.verified = False

    def chunks(self) -> Iterator[list]:
        """Yield the records of each authenticated chunk, in file order."""
        stream = self._stream
        while True:
            length_bytes = stream.read(4)
            if not length_bytes:
                return
            if len(length_bytes) < 4:
                self.truncated = True
                return
            (ct_len,) = struct.unpack("<I", length_bytes)
            if ct_len == 0 or ct_len > MAX_CHUNK_CIPHERTEXT:
                # A zero-filled tail (power cut) is truncation; anything else is corrupt.
                rest = stream.read()
                if ct_len == 0 and not any(rest):
                    self.truncated = True
                    return
                raise KBXFormatError(f"Chunk {self._counter}: ciphertext length {ct_len} out of range.")
            body = stream.read(12 + ct_len + TAG_LEN)
            if len(body) < 12 + ct_len + TAG_LEN:
                self.truncated = True
                return
            nonce, sealed = body[:12], body[12:]
            aad = self.header.raw + self._counter.to_bytes(8, "little")
            try:
                plain = self._aead.decrypt(nonce, sealed, aad)
            except InvalidTag as exc:
                if self._counter == 0:
                    raise KBXAuthError(
                        "Chunk 0 failed authentication: wrong passphrase, or the file is "
                        "tampered with. No data returned.") from exc
                raise KBXAuthError(
                    f"Chunk {self._counter} failed authentication: the file is corrupted or "
                    "tampered with. Data from this chunk on is not trusted.") from exc
            records = _parse_records(plain, self)
            self.verified = True
            self._counter += 1
            yield records


def read_kbx_stream(stream: BinaryIO, passphrase: str) -> Session:
    reader = KBXReader(stream, passphrase)
    records, chunks = [], 0
    for chunk in reader.chunks():
        records.extend(chunk)
        chunks += 1
    return Session(header=reader.header, records=records, truncated=reader.truncated,
                   chunks=chunks, unknown_records=reader.unknown_records,
                   verified=reader.verified)


def read_kbx(path: str | os.PathLike, passphrase: str) -> Session:
    with open(path, "rb") as stream:
        return read_kbx_stream(stream, passphrase)


def read_kbx_bytes(data: bytes, passphrase: str) -> Session:
    return read_kbx_stream(io.BytesIO(data), passphrase)


def session_to_csv(session: Session) -> str:
    """Firmware CSV (time_ms, thigh_*, shank_*) in SI units. Only IMU records.

    time_ms = seq * 1000 / sample_rate_hz. Link-loss rows contain zero-filled
    shank channels; the CSV has no flag column, so callers must check
    session.link_loss_samples.
    """
    header = session.header
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for record in session.imu:
        time_ms = record.seq * 1000.0 / header.sample_rate_hz
        values = [f"{time_ms:.6f}"]
        for index, raw in enumerate(record.channels):
            lsb = header.accel_lsb_mps2 if index % 6 < 3 else header.gyro_lsb_radps
            values.append(f"{raw * lsb:.9g}")
        writer.writerow(values)
    return out.getvalue()


# ---------------------------------------------------------------- writing

class KBXWriter:
    """Append-only KBX1 writer. Create once per session with exclusive create.

    ``salt`` and ``nonce_prefix`` default to random bytes. Pass fixed values only
    for test vectors.
    """

    def __init__(self, path: str | os.PathLike, passphrase: str, *, device_id: str,
                 session_number: int, sample_rate_hz: int, accel_lsb: float = DEFAULT_ACCEL_LSB,
                 gyro_lsb: float = DEFAULT_GYRO_LSB, firmware_version: str,
                 wall_clock_unix_s: int = 0, salt: bytes | None = None,
                 nonce_prefix: bytes | None = None, fsync: bool = True):
        key_material = validate_passphrase(passphrase)
        salt = os.urandom(16) if salt is None else bytes(salt)
        nonce_prefix = os.urandom(4) if nonce_prefix is None else bytes(nonce_prefix)
        header_bytes = _pack_header(device_id=device_id, session_number=session_number,
                                    sample_rate_hz=sample_rate_hz, accel_lsb=accel_lsb,
                                    gyro_lsb=gyro_lsb, salt=salt, nonce_prefix=nonce_prefix,
                                    firmware_version=firmware_version,
                                    wall_clock_unix_s=wall_clock_unix_s)
        self.header = parse_header(header_bytes)
        self._aead = AESGCM(_derive_key(key_material, salt, PBKDF2_ITERATIONS))
        self._fsync = fsync
        self._file = open(path, "xb")
        self._pending = bytearray()
        self._pending_imu = 0
        self._counter = 0
        self._last_seq: int | None = None
        self._write(header_bytes)

    def __enter__(self) -> "KBXWriter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _write(self, data: bytes) -> None:
        self._file.write(data)
        self._file.flush()
        if self._fsync:
            os.fsync(self._file.fileno())

    def write_imu(self, seq: int, channels, flags: int = 0) -> None:
        if not isinstance(seq, int) or not 0 <= seq < 2**32:
            raise KBXFormatError("IMU seq must be a u32 integer.")
        if self._last_seq is not None and seq <= self._last_seq:
            raise KBXFormatError("IMU seq must be strictly increasing.")
        channels = tuple(channels)
        if len(channels) != 12 or not all(
                isinstance(v, int) and -32768 <= v <= 32767 for v in channels):
            raise KBXFormatError("IMU needs 12 int16 raw counts.")
        if not 0 <= flags <= 255:
            raise KBXFormatError("IMU flags must fit in one byte.")
        self._last_seq = seq
        self._pending += _encode_record(REC_IMU, struct.pack("<IB12h", seq, flags, *channels))
        self._pending_imu += 1
        if self._pending_imu >= self.header.sample_rate_hz or len(self._pending) >= CHUNK_FLUSH_BYTES:
            self.flush()

    def write_infer(self, window_seq: int, exercise: int, probabilities, label: int) -> None:
        probabilities = tuple(float(p) for p in probabilities)
        if len(probabilities) != 3:
            raise KBXFormatError("INFER needs exactly 3 probabilities.")
        _check_probabilities(probabilities)
        if exercise not in EXERCISES or label not in (0, 1, 2, REJECT_LABEL):
            raise KBXFormatError("INFER exercise or label out of range.")
        body = struct.pack("<IBfffB", window_seq, exercise, *probabilities, label)
        self._pending += _encode_record(REC_INFER, body)

    def write_event(self, kind: int, battery_mv: int = 0, payload: int = 0) -> None:
        if kind not in EVENT_KINDS or not 0 <= battery_mv <= 65535 or not 0 <= payload < 2**32:
            raise KBXFormatError("EVENT kind, battery_mv or payload out of range.")
        self._pending += _encode_record(REC_EVENT, struct.pack("<BHI", kind, battery_mv, payload))

    def write_raw_record(self, rtype: int, body: bytes) -> None:
        """Append an arbitrary record (for forward-compatibility tests and experiments)."""
        if not 0 <= rtype <= 255 or len(body) > 65535:
            raise KBXFormatError("Record type or body length out of range.")
        self._pending += _encode_record(rtype, bytes(body))

    def flush(self) -> None:
        if not self._pending:
            return
        counter = self._counter.to_bytes(8, "little")
        nonce = self.header.nonce_prefix + counter
        sealed = self._aead.encrypt(nonce, bytes(self._pending), self.header.raw + counter)
        ciphertext_length = len(sealed) - TAG_LEN
        self._write(struct.pack("<I", ciphertext_length) + nonce + sealed)
        self._counter += 1
        self._pending = bytearray()
        self._pending_imu = 0

    def close(self) -> None:
        if self._file.closed:
            return
        try:
            self.flush()
        finally:
            self._file.close()


# ---------------------------------------------------------------- CSV in/out

def csv_to_kbx(csv_path: str | os.PathLike, output: str | os.PathLike, passphrase: str, *,
               device_id: str, session_number: int, sample_rate_hz: int,
               firmware_version: str, wall_clock_unix_s: int = 0,
               accel_lsb: float = DEFAULT_ACCEL_LSB, gyro_lsb: float = DEFAULT_GYRO_LSB) -> dict:
    """Quantise a firmware-format CSV (SI units) to raw counts and write a KBX file."""
    text = Path(csv_path).read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    if next(reader, None) != list(CSV_COLUMNS):
        raise KBXFormatError("CSV header must be exactly: " + ",".join(CSV_COLUMNS))
    rows = []
    for number, row in enumerate(reader, 2):
        if len(row) != len(CSV_COLUMNS):
            raise KBXFormatError(f"CSV row {number}: expected {len(CSV_COLUMNS)} values.")
        values = [float(cell) for cell in row]
        if not all(math.isfinite(v) for v in values):
            raise KBXFormatError(f"CSV row {number}: NaN/Inf rejected.")
        rows.append(values)
    if not rows:
        raise KBXFormatError("CSV has no samples.")
    # Validate and quantise everything before the output file exists.
    t0 = rows[0][0]
    quantised = []
    for number, values in enumerate(rows, 2):
        raws = []
        for index, value in enumerate(values[1:]):
            lsb = accel_lsb if index % 6 < 3 else gyro_lsb
            raw = round(value / lsb)
            if not -32768 <= raw <= 32767:
                raise KBXFormatError(f"CSV row {number}: value exceeds the int16 range; "
                                     "check units (SI) and the configured full scale.")
            raws.append(raw)
        quantised.append((round((values[0] - t0) * sample_rate_hz / 1000.0), raws))
    with KBXWriter(output, passphrase, device_id=device_id, session_number=session_number,
                   sample_rate_hz=sample_rate_hz, accel_lsb=accel_lsb, gyro_lsb=gyro_lsb,
                   firmware_version=firmware_version,
                   wall_clock_unix_s=wall_clock_unix_s) as writer:
        for seq, raws in quantised:
            writer.write_imu(seq, raws)
    return {"samples": len(rows), "output": str(output)}


# ---------------------------------------------------------------- CLI helpers

def _passphrase_for_cli(confirm: bool = False) -> str:
    value = os.environ.get(ENV_PASSPHRASE)
    if value is not None:
        return value
    if not sys.stdin.isatty():
        raise SystemExit(f"Set {ENV_PASSPHRASE} or run interactively to enter the passphrase.")
    value = getpass.getpass("KBX passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != value:
        raise SystemExit("Passphrases do not match.")
    return value


def _summary(session: Session) -> dict:
    counts = {"imu": 0, "infer": 0, "event": 0}
    for record in session.records:
        if isinstance(record, IMURecord):
            counts["imu"] += 1
        elif isinstance(record, INFERRecord):
            counts["infer"] += 1
        elif isinstance(record, EventRecord):
            counts["event"] += 1
    return {"header": session.header.to_json(), "chunks": session.chunks,
            "truncated": session.truncated, "verified": session.verified,
            "record_counts": counts, "unknown_records": session.unknown_records,
            "link_loss_samples": session.link_loss_samples}


def _records_json(session: Session) -> list:
    out = []
    for record in session.records:
        if isinstance(record, IMURecord):
            out.append({"type": "imu", "seq": record.seq, "flags": record.flags,
                        "channels": list(record.channels)})
        elif isinstance(record, INFERRecord):
            out.append({"type": "infer", "window_seq": record.window_seq,
                        "exercise": record.exercise,
                        "probabilities": list(record.probabilities), "label": record.label})
        elif isinstance(record, EventRecord):
            out.append({"type": "event", "kind": record.kind, "kind_name": record.kind_name,
                        "battery_mv": record.battery_mv, "payload": record.payload})
    return out


def _build_fixture_file(path: Path, passphrase: str) -> None:
    """Deterministic 3 s session: see docs/SD_LOG_FORMAT.md section 11."""
    with KBXWriter(path, passphrase, device_id="KBX-TEST-01", session_number=1,
                   sample_rate_hz=50, firmware_version="fixture 1.0.0",
                   wall_clock_unix_s=1767225600, salt=bytes(range(16)),
                   nonce_prefix=bytes.fromhex("a55ac33c"), fsync=False) as writer:
        writer.write_event(0)  # START
        for seq in range(150):
            channels = [((seq * 37 + i * 1009) % 4000) - 2000 for i in range(12)]
            flags = 0
            if seq == 120:
                flags = 0x01
                channels[6:] = [0] * 6
            writer.write_imu(seq, channels, flags)
            if seq == 99:
                writer.write_infer(99, 1, (0.6, 0.25, 0.15), 0)
        writer.write_raw_record(0x70, b"\x01\x02\x03")  # unknown type, must be skipped
        writer.write_event(1, 0, 150)  # STOP


def _chunk_offsets(data: bytes) -> list[tuple[int, int]]:
    """(start, ciphertext_offset) for each chunk of a well-formed file."""
    offsets, pos = [], HEADER_LEN
    while pos < len(data):
        (ct_len,) = struct.unpack_from("<I", data, pos)
        offsets.append((pos, pos + 16))
        pos += 32 + ct_len
    return offsets


def make_fixtures(out_dir: Path) -> dict:
    passphrase = "correct horse battery staple"
    out_dir.mkdir(parents=True, exist_ok=True)
    names = ("valid.kbx", "truncated.kbx", "tampered.kbx", "expected.json")
    for name in names:
        (out_dir / name).unlink(missing_ok=True)
    valid_path = out_dir / "valid.kbx"
    _build_fixture_file(valid_path, passphrase)
    valid_bytes = valid_path.read_bytes()

    session = read_kbx(valid_path, passphrase)
    truncated_bytes = valid_bytes[:-10]
    (out_dir / "truncated.kbx").write_bytes(truncated_bytes)
    truncated = read_kbx_bytes(truncated_bytes, passphrase)

    offsets = _chunk_offsets(valid_bytes)
    flip_at = offsets[1][1] + 5  # one ciphertext byte inside the middle chunk
    tampered_bytes = bytearray(valid_bytes)
    tampered_bytes[flip_at] ^= 0x01
    (out_dir / "tampered.kbx").write_bytes(bytes(tampered_bytes))
    try:
        read_kbx_bytes(bytes(tampered_bytes), passphrase)
        tamper_result = {"error": None}
    except KBXAuthError as exc:
        tamper_result = {"error": "KBXAuthError", "chunk": 1, "message": str(exc)}

    expected = {
        "description": "Public test vectors for KBX1 (docs/SD_LOG_FORMAT.md section 11). "
                       "The passphrase is a public test value, not a secret.",
        "passphrase": passphrase,
        "valid": {"header": session.header.to_json(), "chunks": session.chunks,
                  "truncated": session.truncated, "verified": session.verified,
                  "unknown_records": session.unknown_records,
                  "link_loss_samples": session.link_loss_samples,
                  "records": _records_json(session)},
        "truncated": {"chunks": truncated.chunks, "truncated": truncated.truncated,
                      "verified": truncated.verified,
                      "record_count": len(truncated.records),
                      "last_record": _records_json(truncated)[-1]},
        "tampered": tamper_result,
    }
    (out_dir / "expected.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {name: str(out_dir / name) for name in names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KBX1 encrypted session log tools (research only).")
    sub = parser.add_subparsers(dest="command", required=True)

    enc = sub.add_parser("encrypt", help="firmware-format CSV (SI) -> .kbx")
    enc.add_argument("--csv", type=Path, required=True)
    enc.add_argument("--output", type=Path, required=True)
    enc.add_argument("--device-id", required=True)
    enc.add_argument("--session", type=int, required=True)
    enc.add_argument("--sample-rate", type=int, required=True)
    enc.add_argument("--firmware-version", required=True)
    enc.add_argument("--wall-clock", type=int, default=0, help="Unix seconds; 0 = unknown")

    dec = sub.add_parser("decrypt", help=".kbx -> firmware-format CSV (SI)")
    dec.add_argument("input", type=Path)
    dec.add_argument("--output", type=Path, required=True)

    ins = sub.add_parser("inspect", help="print header and record summary as JSON")
    ins.add_argument("input", type=Path)
    ins.add_argument("--header-only", action="store_true")

    fix = sub.add_parser("make-fixtures", help="write deterministic test vectors")
    fix.add_argument("--out-dir", type=Path,
                     default=Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "kbx")

    args = parser.parse_args(argv)
    try:
        if args.command == "encrypt":
            passphrase = _passphrase_for_cli(confirm=True)
            validate_passphrase(passphrase)
            result = csv_to_kbx(args.csv, args.output, passphrase, device_id=args.device_id,
                                session_number=args.session, sample_rate_hz=args.sample_rate,
                                firmware_version=args.firmware_version,
                                wall_clock_unix_s=args.wall_clock)
            print(json.dumps(result))
        elif args.command == "decrypt":
            session = read_kbx(args.input, _passphrase_for_cli())
            text = session_to_csv(session)
            with open(args.output, "x", encoding="utf-8", newline="") as stream:
                stream.write(text)
            print(f"Wrote {len(session.imu)} IMU rows to {args.output}", file=sys.stderr)
            if session.truncated:
                print("WARNING: file is truncated; the last partial chunk was discarded.", file=sys.stderr)
            if session.link_loss_samples:
                print(f"WARNING: {session.link_loss_samples} link-loss rows have zero-filled shank channels.",
                      file=sys.stderr)
        elif args.command == "inspect":
            if args.header_only:
                with open(args.input, "rb") as stream:
                    header = parse_header(stream.read(HEADER_LEN))
                print(json.dumps(header.to_json(), indent=2))
            else:
                print(json.dumps(_summary(read_kbx(args.input, _passphrase_for_cli())), indent=2))
        elif args.command == "make-fixtures":
            for name, path in make_fixtures(args.out_dir).items():
                print(path)
    except (KBXError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
