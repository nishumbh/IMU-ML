"""Defensive app boundary; immutable system remains the primary validator.

SD MISO is GPIO3 here, NOT the design draft's GPIO2 strapping pin. Each board
has its own MPU6050 at 0x68: master/thigh and slave/shank, never two IMUs on a
single board. v1 wrappers pass their hardcoded config; v2 provisioning and
key storage belong exclusively to system/.
"""
import binascii

DEFAULT_PINS = dict(i2c_sda=4, i2c_scl=5, sd_sck=6, sd_mosi=7,
                    sd_miso=3, sd_cs=10)


def _hex(value, size, name):
    if not isinstance(value, str) or len(value) != size * 2:
        raise ValueError("invalid " + name)
    try:
        result = binascii.unhexlify(value)
    except (ValueError, TypeError):
        raise ValueError("invalid " + name)
    return result


def validate(config, role):
    if config.get("role") != role or role not in ("master", "slave"):
        raise ValueError("incorrect role")
    iteration = config.get("iteration")
    channel = config.get("channel")
    if type(iteration) is not int or iteration not in (1, 2):
        raise ValueError("iteration must be 1 or 2")
    if type(channel) is not int or not 1 <= channel <= 11:
        raise ValueError("channel must be 1..11")
    if config.get("sample_rate_hz") != 50:
        raise ValueError("only 50 Hz supported")
    if config.get("imu_address") != 104:
        raise ValueError("MPU6050 must use address 104 (AD0 low)")
    pins = config.get("pins")
    if not isinstance(pins, dict):
        raise ValueError("pins dictionary required")
    for key, pin in DEFAULT_PINS.items():
        if type(pins.get(key)) is not int or pins[key] != pin:
            raise ValueError("v1/v2 wiring requires " + key + "=" + str(pin))
    peer = _hex(config.get("peer_mac"), 6, "peer_mac")
    if peer[0] & 1 or peer == b"\x00" * 6:
        raise ValueError("peer must be a nonzero unicast MAC")
    keys = []
    for name in ("pmk", "lmk"):
        value = config.get(name)
        if iteration == 2 or value is not None:
            keys.append(_hex(value, 16, name))
        else:
            keys.append(None)
    return peer, keys[0], keys[1]
