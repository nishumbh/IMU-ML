"""Right-thigh master: 50 Hz requests, local IMU, matching shank response, SD.

Rows are request-paired, NOT simultaneous hardware samples. There is no
shared clock, hardware trigger, model, diagnosis, or clinical inference.
Both nodes must be restarted together after restarting the master (the slave
pins its session until restart). Stop via the system supervisor/reset or a
KeyboardInterrupt during development. A soft stop runs finally cleanup; a
reset/power cut cannot flush buffered data. CSV/health files are at SD root
/sd/logNNNNNN.csv and /sd/logNNNNNN.health.jsonl, NOT inside the OTA slot.
"""
from .config import validate
from .protocol import Pairer, MAX_SEQUENCE
from .runtime import Clock, Sensor, close_all

PERIOD_MS = 20
RESPONSE_DEADLINE_MS = 15
MAX_DRAIN = 8


def run_session(imu, radio, storage, clock, session, stop=None):
    """Injectable runtime. stop is a host-test/supervisor cooperative hook.

    SD service happens with no request outstanding. Its latency, the sensor
    read, firmware ACK retries and scheduling jitter can cause missed slots.
    Missed slots are skipped, never burst-replayed or filled with old values.
    """
    pairer = Pairer(radio.peer, session, RESPONSE_DEADLINE_MS)
    sequence = 0
    next_due = 0
    try:
        while stop is None or not stop():
            now = clock.now_ms()
            pairer.expire(now)
            if pairer.pending is None and now >= next_due:
                skipped = (now - next_due) // PERIOD_MS
                pairer.counts["skipped_slots"] += skipped
                sequence += skipped
                if sequence > MAX_SEQUENCE:
                    raise RuntimeError("session sequence exhausted; restart both nodes")
                # Rebase after lateness: preserving the old phase could send
                # again only 1-2 ms later after an SD/driver stall. Count whole
                # missed periods, then keep at least 20 ms between read starts.
                next_due = now + PERIOD_MS
                current_sequence = sequence
                sequence += 1
                timestamp = clock.now_ms()
                ticks = clock.raw_ticks()
                try:
                    sample = imu.read()
                    packet = pairer.begin(current_sequence, timestamp, ticks, sample)
                except (OSError, ValueError):
                    pairer.counts["sensor_error"] += 1
                else:
                    # Do not transmit a request whose local acquisition is old.
                    if not pairer.expire(clock.now_ms()):
                        try:
                            if not radio.send(packet):
                                pairer.counts["send_error"] += 1
                        except OSError:
                            pairer.counts["send_error"] += 1
            for _ in range(MAX_DRAIN):
                received = radio.receive()
                if received is None:
                    break
                row = pairer.accept(received[0], received[1], clock.now_ms())
                if row is not None:
                    storage.append(row)
            now = clock.now_ms()
            pairer.expire(now)
            if pairer.pending is None:
                storage.service(now, pairer.counts)
            clock.idle()
    finally:
        storage.finish(clock.now_ms(), pairer.counts)
    return pairer.counts


def run(config):
    """Entrypoint called by system/ or v1 wrapper with MASTER_CONFIG."""
    import os
    import binascii
    from .radio import Radio
    from .storage import SDLogger
    validate(config, "master")
    imu = radio = storage = None
    try:
        session = os.urandom(8)
        imu = Sensor(config)
        storage = SDLogger(config, dict(
            session=binascii.hexlify(session).decode(), iteration=config["iteration"],
            sample_rate_hz=50, placement="right thigh / right shank dataset proxy",
            pairing="request-paired; not simultaneous hardware synchronization",
            time_basis="master elapsed ms at local I2C read start",
            accel_units="m/s^2", gyro_units="rad/s"))
        radio = Radio(config)
        run_session(imu, radio, storage, Clock(), session)
    except BaseException:
        close_all(storage, radio, imu)
        raise
    else:
        error = close_all(storage, radio, imu)
        if error is not None:
            raise error
