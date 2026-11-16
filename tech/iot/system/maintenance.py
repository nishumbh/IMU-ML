"""Explicit, bounded maintenance; never auto-open an AP for a broken config."""
import os
import time
from system import storage
from system.transport import Deadline


def _sleep_ms(milliseconds):
    if hasattr(time, 'sleep_ms'):
        time.sleep_ms(milliseconds)
    else:
        time.sleep(milliseconds / 1000)


def requested(root='/', device=None, machine_module=None):
    """Consume a USB-created flag, or sample the configured active-low GPIO."""
    flag = storage.path(root, 'config/maintenance.flag')
    if storage.exists(flag):
        os.remove(flag)
        storage.sync()
        return True
    if device is None or device.get('maintenance_pin') is None:
        return False
    if machine_module is None:
        import machine as machine_module
    pin = machine_module.Pin(device['maintenance_pin'], machine_module.Pin.IN,
                             machine_module.Pin.PULL_UP)
    for index in range(3):
        if pin.value() != 0:
            return False
        if index != 2:
            _sleep_ms(20)
    return True


def validate_ap(value):
    if not isinstance(value, dict) or set(value) != {'ssid', 'password'}:
        raise ValueError('factory AP configuration required')
    ssid, password = value['ssid'], value['password']
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode('utf-8')) <= 32:
        raise ValueError('invalid setup AP SSID')
    if not isinstance(password, str) or not 12 <= len(password) <= 63:
        raise ValueError('setup AP requires unique factory password >=12 characters')
    if any(ord(c) < 33 or ord(c) > 126 for c in password) or any(c in ssid for c in '\x00\r\n'):
        raise ValueError('invalid AP credential character')
    return value


def _connect_station(network, wifi, deadline):
    station = network.WLAN(network.STA_IF)
    try:
        station.active(True)
        station.connect(wifi['ssid'], wifi['password'])
        while not station.isconnected():
            deadline.remaining()
            _sleep_ms(100)
        return station
    except Exception:
        station.active(False)
        raise


def run(root='/', duration=180, network_module=None, socket_module=None):
    """Run AP HTTP only after caller established physical/USB authorization.

Returns to USB REPL after <=duration of ordinary timed socket operations,
without running the application or automatically resetting. Port DNS/driver
calls can block outside Python's control; no hard-real-time bound is claimed.
No STA connection occurs until a correctly authenticated /update request.
"""
    from system.provisioning import handle_client
    from system.config import load_device, load_wifi
    from system.ota import download_and_stage
    # Check all factory material BEFORE activating any interface.
    storage.secret(root, 'setup.key')
    storage.secret(root, 'ota.key')
    ap_config = validate_ap(storage.read_json(storage.path(root, 'config/setup_ap.json')))
    if type(duration) is not int or not 1 <= duration <= 300:
        raise ValueError('invalid maintenance duration')
    if network_module is None:
        import network as network_module
    if socket_module is None:
        import socket as socket_module
    deadline = Deadline(duration)
    access_point = None
    station = None
    listener = None

    def update():
        nonlocal station
        device = load_device(root)
        wifi = load_wifi(root, device['iteration'])
        # Reserve a single total budget across Wi-Fi association and download.
        update_deadline = Deadline(min(40, deadline.remaining()))
        station = _connect_station(network_module, wifi, update_deadline)
        try:
            return download_and_stage(root, timeout=min(30, update_deadline.remaining()))
        finally:
            try:
                station.disconnect()
            finally:
                station.active(False)
                station = None

    try:
        station = network_module.WLAN(network_module.STA_IF)
        station.active(False)
        station = None
        access_point = network_module.WLAN(network_module.AP_IF)
        access_point.active(False)
        # Configure WPA2 BEFORE activation. No open AP fallback for port API
        # incompatibility. Unsupported authmode is a manual USB repair issue.
        access_point.config(essid=ap_config['ssid'], password=ap_config['password'],
                            authmode=network_module.AUTH_WPA2_PSK)
        access_point.active(True)
        address = access_point.ifconfig()[0]
        listener = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
        listener.bind((address, 80))
        listener.listen(1)
        # Bound clients as well as time; repeated failures never extend session.
        for attempt in range(24):
            listener.settimeout(min(5, deadline.remaining()))
            try:
                client, remote = listener.accept()
            except OSError:
                deadline.remaining()
                continue
            try:
                handle_client(client, root, update, timeout=min(5, deadline.remaining()),
                              session_deadline=deadline)
            finally:
                client.close()
    except OSError:
        return 'maintenance_ended'
    finally:
        if listener is not None:
            listener.close()
        if station is not None:
            station.active(False)
        if access_point is not None:
            access_point.active(False)
    return 'maintenance_ended'
