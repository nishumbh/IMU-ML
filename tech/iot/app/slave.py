"""Right-shank node: acquire only on a new authorized-peer session request.

No SD, console output, inference, or independent free-running transmit loop.
Restart this node when restarting the master: the first session is pinned
until restart to prevent replayed session changes. v1 is not authenticated;
v2 relies on the system-provisioned ESP-NOW PMK/LMK and encrypted unicast.
"""
from .config import validate
from .protocol import RequestGate, response
from .runtime import Clock, Sensor, close_all

MAX_DRAIN = 8


def run_session(imu, radio, clock, stop=None):
    gate = RequestGate(radio.peer)
    while stop is None or not stop():
        for _ in range(MAX_DRAIN):
            received = radio.receive()
            if received is None:
                break
            accepted = gate.accept(received[0], received[1])
            if accepted is None:
                continue
            session, seq = accepted
            ticks = clock.raw_ticks()
            try:
                packet = response(session, seq, ticks, imu.read())
            except (OSError, ValueError):
                gate.counts["sensor_error"] += 1
                continue
            try:
                if not radio.send(packet):
                    gate.counts["send_error"] += 1
            except OSError:
                gate.counts["send_error"] += 1
        clock.idle()
    return gate.counts


def run(config):
    """Entrypoint called by system/ or v1 wrapper with SLAVE_CONFIG."""
    from .radio import Radio
    validate(config, "slave")
    imu = radio = None
    try:
        imu = Sensor(config)
        radio = Radio(config)
        run_session(imu, radio, Clock())
    except BaseException:
        close_all(radio, imu)
        raise
    else:
        error = close_all(radio, imu)
        if error is not None:
            raise error
