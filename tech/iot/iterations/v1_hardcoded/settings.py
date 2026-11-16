"""BENCH ONLY. Edit peer STA MACs and GPIOs; no real patient recordings."""
PINS = {'i2c_sda': 4, 'i2c_scl': 5, 'sd_sck': 6, 'sd_mosi': 7,
        'sd_miso': 3, 'sd_cs': 10}
BASE = {'iteration': 1, 'channel': 6, 'pmk': None, 'lmk': None,
        'sample_rate_hz': 50, 'imu_address': 0x68, 'pins': PINS,
        'maintenance_pin': None}
MASTER_CONFIG = dict(BASE, role='master', peer_mac='020000000002')
SLAVE_CONFIG = dict(BASE, role='slave', peer_mac='020000000001')
