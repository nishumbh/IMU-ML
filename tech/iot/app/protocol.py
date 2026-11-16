"""Small, strictly validated ESP-NOW packets and host-testable pairing state.

Wire v1 (independent of deployment iteration): little endian version u8,
kind u8, random session 8 bytes, sequence u32, acquisition ticks_ms u32;
responses append ax,ay,az,gx,gy,gz float32 in m/s^2 and rad/s. Requests are
18 bytes; responses 42, below even the legacy ESP-NOW 250-byte limit.
Slave ticks are diagnostic ONLY: clocks are not synchronized.
"""
import math
import struct

VERSION = 1
REQUEST = 1
RESPONSE = 2
HEADER = "<BB8sII"
SAMPLE = "<6f"
HEADER_SIZE = struct.calcsize(HEADER)
RESPONSE_SIZE = HEADER_SIZE + struct.calcsize(SAMPLE)
MAX_SEQUENCE = 0xFFFFFFFF
ACCEL_LIMIT = 4 * 9.80665 * 1.001
GYRO_LIMIT = 500 * math.pi / 180 * 1.001


def valid_sample(values):
    if len(values) != 6:
        raise ValueError("six IMU values required")
    for i, value in enumerate(values):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("non-finite IMU value")
        if abs(value) > (ACCEL_LIMIT if i < 3 else GYRO_LIMIT):
            raise ValueError("IMU value outside configured range")
    return tuple(values)


def _header(kind, session, sequence, ticks):
    if not isinstance(session, bytes) or len(session) != 8:
        raise ValueError("eight-byte session required")
    if type(sequence) is not int or not 0 <= sequence <= MAX_SEQUENCE:
        raise ValueError("invalid sequence")
    if type(ticks) is not int or not 0 <= ticks <= 0xFFFFFFFF:
        raise ValueError("invalid acquisition ticks")
    return struct.pack(HEADER, VERSION, kind, session, sequence, ticks)


def request(session, sequence, ticks):
    return _header(REQUEST, session, sequence, ticks)


def response(session, sequence, ticks, values):
    return _header(RESPONSE, session, sequence, ticks) + struct.pack(
        SAMPLE, *valid_sample(values))


def decode(data):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("packet must be bytes")
    if len(data) not in (HEADER_SIZE, RESPONSE_SIZE):
        raise ValueError("invalid packet length")
    version, kind, session, seq, ticks = struct.unpack(HEADER, data[:HEADER_SIZE])
    if version != VERSION or kind not in (REQUEST, RESPONSE):
        raise ValueError("unsupported packet")
    expected = HEADER_SIZE if kind == REQUEST else RESPONSE_SIZE
    if len(data) != expected:
        raise ValueError("invalid packet shape")
    values = None
    if kind == RESPONSE:
        values = valid_sample(struct.unpack(SAMPLE, data[HEADER_SIZE:]))
    return kind, session, seq, ticks, values


class Pairer:
    """One outstanding request, one row at most; times are master elapsed ms."""

    def __init__(self, peer, session, deadline_ms=15):
        _header(REQUEST, session, 0, 0)
        if not 0 < deadline_ms < 20:
            raise ValueError("deadline must be shorter than the 50 Hz period")
        self.peer = bytes(peer)
        self.session = session
        self.deadline_ms = deadline_ms
        self.pending = None
        self.last_sequence = -1
        self.counts = dict(paired=0, timeout=0, wrong_sender=0, invalid=0,
                           stale_session=0, unmatched=0, send_error=0,
                           sensor_error=0, skipped_slots=0)

    def begin(self, sequence, timestamp_ms, ticks, values):
        if self.pending is not None:
            raise ValueError("request still outstanding")
        if sequence <= self.last_sequence:
            raise ValueError("sequence must increase without wrapping")
        packet = request(self.session, sequence, ticks)
        values = valid_sample(values)
        self.last_sequence = sequence
        self.pending = (sequence, timestamp_ms, values)
        return packet

    def expire(self, now_ms):
        if self.pending is not None:
            if now_ms >= self.pending[1] + self.deadline_ms:
                self.pending = None
                self.counts["timeout"] += 1
                return True
        return False

    def accept(self, sender, data, now_ms):
        # Expire before inspecting the packet, including a late matching packet.
        self.expire(now_ms)
        if sender != self.peer:
            self.counts["wrong_sender"] += 1
            return None
        try:
            kind, session, seq, ticks, values = decode(data)
        except (ValueError, TypeError):
            self.counts["invalid"] += 1
            return None
        if session != self.session:
            self.counts["stale_session"] += 1
            return None
        if kind != RESPONSE or self.pending is None or seq != self.pending[0]:
            self.counts["unmatched"] += 1
            return None
        _, timestamp, thigh = self.pending
        self.pending = None
        self.counts["paired"] += 1
        return (timestamp,) + thigh + values


class RequestGate:
    """Pin the first session until restart, then accept strictly increasing seq.

    Restart BOTH nodes after a master restart. No radio-triggered session reset:
    otherwise an old session could displace the current session. Iteration 1
    is explicitly unauthenticated and cannot prevent active spoofing/racing.
    """

    def __init__(self, peer):
        self.peer = bytes(peer)
        self.session = None
        self.last_sequence = -1
        self.counts = dict(accepted=0, wrong_sender=0, invalid=0,
                           stale_session=0, replay=0, sensor_error=0,
                           send_error=0)

    def accept(self, sender, data):
        if sender != self.peer:
            self.counts["wrong_sender"] += 1
            return None
        try:
            kind, session, seq, ticks, values = decode(data)
        except (ValueError, TypeError):
            self.counts["invalid"] += 1
            return None
        if kind != REQUEST:
            self.counts["invalid"] += 1
            return None
        if self.session is not None and self.session != session:
            self.counts["stale_session"] += 1
            return None
        if seq <= self.last_sequence:
            self.counts["replay"] += 1
            return None
        self.session = session
        self.last_sequence = seq
        self.counts["accepted"] += 1
        return session, seq
