"""Exclusive CSV sessions on SPI SD, without an external sdcard dependency.

Requires machine.SDCard(slot=2, sck=Pin(...), mosi=Pin(...), miso=Pin(...),
cs=Pin(...), freq=...) and os.mount on the actual ESP32-C3 firmware. Some C3
builds do not provide this SPI variant. In that case install/build firmware
with SPI SDCard support and verify slot/pin support; do NOT use SDMMC or
silently substitute an unreviewed sdcard.py. No fallback to internal flash.
The VFS must support exclusive-create open mode 'x'; there is deliberately no
'w' fallback on firmware lacking that mode, since it could overwrite a log.

SD writes and flushes are synchronous and not hard-real-time. Up to one flush
interval plus filesystem caching may be lost on power failure; a failed write
can leave a truncated final CSV line or health record, which must be discarded.
Missing pairs
are omitted, never interpolated. time_ms is master session-elapsed read-start
time; thigh then shank are raw sensor axes in SI units (no frame calibration).
"""
import errno
import json
import os

from .protocol import valid_sample

COLUMNS = ("time_ms", "thigh_ax", "thigh_ay", "thigh_az", "thigh_gx",
           "thigh_gy", "thigh_gz", "shank_ax", "shank_ay", "shank_az",
           "shank_gx", "shank_gy", "shank_gz")
HEADER = ",".join(COLUMNS) + "\n"


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError as exc:
        if exc.args[0] != errno.ENOENT:
            raise
        return False


class CSVLogger:
    """Inject a host directory for tests; files always use exclusive-create."""

    def __init__(self, directory, metadata, buffer_rows=25, flush_ms=1000):
        if not 1 <= buffer_rows <= 50 or flush_ms <= 0:
            raise ValueError("invalid buffering policy")
        self.csv = None
        self.health = None
        self.buffer = []
        self.buffer_rows = buffer_rows
        self.flush_ms = flush_ms
        self.last_flush = 0
        self.last_health = 0
        self.last_timestamp = -1
        self.counts = {}
        self.closed = False
        self.write_failed = False
        self.metadata = metadata
        try:
            for index in range(1, 1000000):
                base = directory + "/log%06d" % index
                if _exists(base + ".csv") or _exists(base + ".health.jsonl"):
                    continue
                try:
                    self.csv = open(base + ".csv", "x")
                except OSError as exc:
                    if exc.args[0] == errno.EEXIST:
                        continue
                    raise
                # No other process should write this directory on a node.
                self.health = open(base + ".health.jsonl", "x")
                self.path = base + ".csv"
                break
            else:
                raise OSError("SD session names exhausted")
            self.csv.write(HEADER)
            self.csv.flush()
            self._health(0, {}, "start")
        except BaseException:
            if self.csv is not None:
                self.csv.close()
            if self.health is not None:
                self.health.close()
            raise

    def append(self, row):
        if self.closed or self.write_failed:
            raise ValueError("logger closed or failed")
        if len(row) != 13 or type(row[0]) is not int or row[0] < 0:
            raise ValueError("invalid CSV row")
        if row[0] <= self.last_timestamp:
            raise ValueError("CSV time must increase")
        valid_sample(row[1:7])
        valid_sample(row[7:13])
        self.buffer.append(str(row[0]) + "," +
                           ",".join("%.7g" % value for value in row[1:]) + "\n")
        self.last_timestamp = row[0]
        if len(self.buffer) >= self.buffer_rows:
            self._write_buffer()

    def _write_buffer(self):
        if self.buffer:
            data = "".join(self.buffer)
            # Never retry an uncertain partial write during finally cleanup.
            # A power/card failure can leave a truncated final line, not a row
            # that should be treated as a valid paired measurement.
            try:
                written = self.csv.write(data)
                if written is not None and written != len(data):
                    raise OSError("short SD CSV write")
            except BaseException:
                self.write_failed = True
                self.buffer = []
                raise
            self.buffer = []

    def _health(self, now_ms, counts, event):
        record = dict(metadata=self.metadata, time_ms=now_ms,
                      counters=dict(counts), event=event)
        self.health.write(json.dumps(record) + "\n")
        self.health.flush()
        self.last_health = now_ms

    def service(self, now_ms, counts):
        self.counts = dict(counts)
        if now_ms - self.last_flush >= self.flush_ms:
            self._write_buffer()
            self.csv.flush()
            self.last_flush = now_ms
        # Separate health stream, at most once per five seconds (plus start/end).
        if now_ms - self.last_health >= 5000:
            self._health(now_ms, counts, "running")

    def finish(self, now_ms, counts):
        self.counts = dict(counts)
        self.last_flush = now_ms

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if not self.write_failed:
                self._write_buffer()
                self.csv.flush()
                self._health(self.last_flush, self.counts, "stop")
        finally:
            try:
                self.csv.close()
            finally:
                self.health.close()


class SDLogger:
    def __init__(self, config, metadata):
        import machine
        try:
            SDCard = machine.SDCard
            Pin = machine.Pin
        except AttributeError:
            raise RuntimeError("C3 firmware must provide machine.SDCard with SPI slot=2 support")
        self.card = None
        self.logger = None
        self.mounted = False
        self.mount = "/sd"
        pins = config["pins"]
        try:
            try:
                self.card = SDCard(slot=2, sck=Pin(pins["sd_sck"]),
                                   mosi=Pin(pins["sd_mosi"]),
                                   miso=Pin(pins["sd_miso"]),
                                   cs=Pin(pins["sd_cs"]), freq=10000000)
            except (TypeError, ValueError, OSError) as exc:
                raise RuntimeError("SPI SDCard(slot=2) unavailable: verify C3 "
                                   "firmware SPI SD support, pins, card and wiring") from exc
            os.mount(self.card, self.mount)
            self.mounted = True
            self.logger = CSVLogger(self.mount, metadata)
        except BaseException:
            self.close()
            raise

    def append(self, row):
        self.logger.append(row)

    def service(self, now_ms, counts):
        self.logger.service(now_ms, counts)

    def finish(self, now_ms, counts):
        self.logger.finish(now_ms, counts)

    def close(self):
        try:
            if self.logger is not None:
                self.logger.close()
        finally:
            try:
                if self.mounted:
                    os.umount(self.mount)
                    self.mounted = False
            finally:
                if self.card is not None:
                    self.card.deinit()
