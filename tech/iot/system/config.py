"""Validated device and separate Wi-Fi configuration. No logging of secrets.

GPIO 3/4/5/6/7/10 defaults need board-specific review (strapping/JTAG/flash
functions differ by board); these prototype defaults are not a wiring approval.
"""
from system.crypto import hex_bytes
from system.storage import path, read_json

PIN_DEFAULTS = {'i2c_sda': 4, 'i2c_scl': 5, 'sd_sck': 6, 'sd_mosi': 7,
                'sd_miso': 3, 'sd_cs': 10}


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate_device(value):
    if not isinstance(value, dict):
        raise ValueError('device configuration must be an object')
    allowed = {'role', 'iteration', 'channel', 'peer_mac', 'pmk', 'lmk',
               'sample_rate_hz', 'pins', 'imu_address', 'maintenance_pin'}
    if set(value) - allowed:
        raise ValueError('unknown device configuration field')
    result = dict(value)
    if result.get('role') not in ('master', 'slave'):
        raise ValueError('invalid role')
    if not _integer(result.get('iteration'), 1, 2):
        raise ValueError('invalid iteration')
    if not _integer(result.get('channel'), 1, 11):
        raise ValueError('invalid channel')
    peer = hex_bytes(result.get('peer_mac'), 6)
    if peer == b'\x00' * 6 or peer[0] & 1:
        raise ValueError('peer must be a unicast MAC')
    result['peer_mac'] = result['peer_mac'].lower()
    for name in ('pmk', 'lmk'):
        if result.get(name) is not None:
            hex_bytes(result[name], 16)
            result[name] = result[name].lower()
        elif result['iteration'] == 2:
            raise ValueError('iteration 2 requires PMK and LMK')
    if result['iteration'] == 2 and result['pmk'] == result['lmk']:
        raise ValueError('PMK and LMK must differ')
    if result.get('sample_rate_hz', 50) != 50 or type(result.get('sample_rate_hz', 50)) is not int:
        raise ValueError('prototype sample rate must be 50 Hz')
    result['sample_rate_hz'] = 50
    if not _integer(result.get('imu_address', 104), 8, 119):
        raise ValueError('invalid I2C address')
    result.setdefault('imu_address', 104)
    supplied = result.get('pins', {})
    if not isinstance(supplied, dict) or set(supplied) - set(PIN_DEFAULTS):
        raise ValueError('invalid pin fields')
    pins = dict(PIN_DEFAULTS)
    pins.update(supplied)
    used = list(pins.values())
    if result.get('maintenance_pin') is not None:
        used.append(result['maintenance_pin'])
    if any(not _integer(pin, 0, 21) for pin in used) or len(set(used)) != len(used):
        raise ValueError('invalid or duplicate pins')
    result['pins'] = pins
    return result


def validate_wifi(value, iteration=1):
    if not isinstance(value, dict) or set(value) != {'ssid', 'password', 'ota_url'}:
        raise ValueError('invalid Wi-Fi configuration fields')
    if not isinstance(value['ssid'], str) or not 1 <= len(value['ssid'].encode('utf-8')) <= 32:
        raise ValueError('invalid SSID')
    if not isinstance(value['password'], str) or len(value['password'].encode('utf-8')) > 63:
        raise ValueError('invalid Wi-Fi password')
    if any(c in value['ssid'] + value['password'] for c in '\x00\r\n'):
        raise ValueError('invalid Wi-Fi text')
    from system.transport import parse_url
    parse_url(value['ota_url'], iteration)
    return dict(value)


def load_device(root='/'):
    return validate_device(read_json(path(root, 'config/device.json')))


def load_wifi(root='/', iteration=1):
    return validate_wifi(read_json(path(root, 'config/wifi.json')), iteration)
