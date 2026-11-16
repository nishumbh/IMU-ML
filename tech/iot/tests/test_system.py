"""CPython logic/mocked I/O tests, not physical ESP32 or TLS qualification."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

IOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(IOT))
from system import bootstate, canonical, config, crypto, maintenance, ota
from system import provisioning, storage, transport
import bootloader

KEY = bytes(range(32))
SETUP = bytes(range(32, 64))
DEVICE = {'role': 'master', 'iteration': 1, 'channel': 1, 'peer_mac': '020000000002'}
WIFI = {'ssid': 'bench', 'password': 'a-password', 'ota_url': 'http://updates.example/bundle.json'}


def bundle(version=1, files=None, key=KEY, mutate=None):
    if files is None:
        files = {'app/__init__.py': b'', 'app/master.py': b'def run(config):\n    pass\n'}
    manifest = {'version': version, 'files': [
        {'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
         'content_b64': base64.b64encode(data).decode('ascii')}
        for name, data in sorted(files.items())]}
    if mutate:
        mutate(manifest)
    message = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return json.dumps({'manifest': manifest,
                      'hmac_sha256': hmac.new(key, message, hashlib.sha256).hexdigest()}).encode()


class Socket:
    def __init__(self, incoming=b''):
        self.incoming, self.outgoing, self.closed = incoming, b'', False

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, count):
        result, self.incoming = self.incoming[:count], self.incoming[count:]
        return result

    def send(self, data):
        size = min(79, len(data))
        self.outgoing += data[:size]
        return size

    def connect(self, address):
        self.address = address

    def close(self):
        self.closed = True


def sockets(sock):
    return types.SimpleNamespace(SOCK_STREAM=1,
                                 getaddrinfo=lambda *a: [(2, 1, 6, '', ('127.0.0.1', 80))],
                                 socket=lambda *a: sock)


def post(endpoint, body):
    data = json.dumps(body).encode()
    return (b'POST ' + endpoint.encode() + b' HTTP/1.1\r\nContent-Type: application/json\r\n'
            b'Content-Length: ' + str(len(data)).encode() + b'\r\n\r\n' + data)


class RootTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.directory = Path(self.root)
        (self.directory / 'config').mkdir()
        self.write_json('config/device.json', DEVICE)
        self.write_json('config/wifi.json', WIFI)
        (self.directory / 'config/ota.key').write_bytes(KEY)
        (self.directory / 'config/setup.key').write_bytes(SETUP)
        self.write_json('config/setup_ap.json', {'ssid': 'setup-random', 'password': '7H3uZ2Aa9vBb4K5c'})

    def write_json(self, name, value):
        (self.directory / name).write_text(json.dumps(value))


class CanonicalTests(unittest.TestCase):
    def test_host_parity(self):
        value = {'z': [True, False, None, 0, -12, 'é\U0001f642\u007f'],
                 'a': '\x00\b\t\n\f\r/\\"', 'nested': {'b': 2, 'a': 1}}
        self.assertEqual(canonical.dumps(value), json.dumps(
            value, sort_keys=True, separators=(',', ':'), ensure_ascii=True))

    def test_reject_types(self):
        for value in [1.0, float('nan'), {1: 'a'}, (1, 2), b'a']:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                canonical.dumps(value)

    def test_hmac_vectors(self):
        for key in (b'k', KEY, b'long' * 40):
            self.assertEqual(crypto.hmac_sha256(key, b'payload'),
                             hmac.new(key, b'payload', hashlib.sha256).digest())
        self.assertTrue(crypto.constant_time_equal(b'abc', b'abc'))
        self.assertFalse(crypto.constant_time_equal(b'abc', b'ab'))
        self.assertFalse(crypto.constant_time_equal(b'abc', b'abd'))


class ConfigTests(RootTest):
    def test_defaults(self):
        value = config.load_device(self.root)
        self.assertEqual(value['pins'], config.PIN_DEFAULTS)
        self.assertEqual(value['sample_rate_hz'], 50)
        self.assertEqual(value['imu_address'], 104)
        self.assertEqual(config.load_wifi(self.root), WIFI)

    def test_invalid_device(self):
        cases = [dict(DEVICE, role='admin'), dict(DEVICE, iteration=True),
                 dict(DEVICE, channel=12), dict(DEVICE, channel=0),
                 dict(DEVICE, peer_mac='ff0000000000'), dict(DEVICE, peer_mac='x' * 12),
                 dict(DEVICE, sample_rate_hz=100), dict(DEVICE, sample_rate_hz=True),
                 dict(DEVICE, imu_address=True), dict(DEVICE, maintenance_pin=4),
                 dict(DEVICE, maintenance_pin=True), dict(DEVICE, maintenance_pin=22),
                 dict(DEVICE, pins={'sd_cs': 4}), dict(DEVICE, pins={'bad': 1})]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.validate_device(value)

    def test_iteration_two(self):
        value = dict(DEVICE, iteration=2)
        for changes in ({}, {'pmk': '11' * 16}, {'pmk': '11' * 16, 'lmk': '11' * 16}):
            with self.assertRaises(ValueError):
                config.validate_device(dict(value, **changes))
        config.validate_device(dict(value, pmk='AA' * 16, lmk='bb' * 16))
        with self.assertRaises(ValueError):
            config.validate_wifi(WIFI, iteration=2)
        config.validate_wifi(dict(WIFI, ota_url='https://updates.example/ota'), iteration=2)


class BundleTests(RootTest):
    def test_valid_host_manifest(self):
        self.assertEqual(ota.verify_bundle(bundle(), KEY)[0], 1)

    def test_tampering(self):
        data = json.loads(bundle())
        data['manifest']['version'] = 9
        for payload, key in ((json.dumps(data).encode(), KEY), (bundle(), b'x' * 32)):
            with self.assertRaises(ValueError):
                ota.verify_bundle(payload, key)

    def test_paths(self):
        for name in ('../app/a.py', '/app/a.py', 'app/../system/a.py', 'system/a.py',
                     'config/device.json', 'main.py', 'app/a.py/evil.py', 'app//x.py',
                     'app\\a.py', 'app/a.txt', 'app/a-b.py', 'app/./a.py', 'app/é.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                ota.verify_bundle(bundle(files={name: b''}), KEY)
        ota.validate_path('app/nested/module_1.py')

    def test_size_hash_base64_and_duplicates(self):
        mutators = [lambda m: m['files'][0].update(size=1),
                    lambda m: m['files'][0].update(size=True),
                    lambda m: m['files'][0].update(sha256='00' * 32),
                    lambda m: m['files'][0].update(content_b64='!!!'),
                    lambda m: m['files'][0].update(content_b64='YQ==\n'),
                    lambda m: m['files'].append(dict(m['files'][0])),
                    lambda m: m.update(version=0), lambda m: m.update(version=True),
                    lambda m: m['files'][0].update(size=ota.MAX_TOTAL + 1)]
        for mutate in mutators:
            with self.assertRaises(ValueError):
                ota.verify_bundle(bundle(mutate=mutate), KEY)

    def test_bounds(self):
        with self.assertRaises(ValueError):
            ota.verify_bundle(b' ' * (transport.MAX_BUNDLE + 1), KEY)
        with self.assertRaises(ValueError):
            ota.verify_bundle(bundle(files={'app/mod%d.py' % i: b'' for i in range(33)}), KEY)

    def test_inactive_only(self):
        active = self.directory / 'slots/a/app'
        active.mkdir(parents=True)
        (active / 'master.py').write_bytes(b'active sentinel')
        old = self.directory / 'slots/b/app/nested'
        old.mkdir(parents=True)
        (old / 'stale.py').write_bytes(b'old')
        (old / 'partial.py.tmp').write_bytes(b'interrupted')
        state = ota.install_bundle(bundle(), self.root)
        self.assertEqual(state['pending'], {'slot': 'b', 'version': 1, 'attempted': False})
        self.assertEqual((active / 'master.py').read_bytes(), b'active sentinel')
        self.assertFalse((old / 'stale.py').exists())
        self.assertFalse((old / 'partial.py.tmp').exists())
        self.assertEqual(bootstate.load(self.root)['highwater'], 0)

    def test_bad_auth_does_not_write(self):
        with self.assertRaises(ValueError):
            ota.install_bundle(bundle(key=b'x' * 32), self.root)
        self.assertFalse((self.directory / 'slots').exists())

    def test_foreign_files_and_symlinks(self):
        inactive = self.directory / 'slots/b/app'
        inactive.mkdir(parents=True)
        extra = inactive / 'secret.txt'
        extra.write_bytes(b'leave me')
        with self.assertRaises(ValueError):
            ota.install_bundle(bundle(), self.root)
        self.assertEqual(extra.read_bytes(), b'leave me')
        extra.unlink()
        target = self.directory / 'config/device.json'
        (inactive / 'master.py').symlink_to(target)
        before = target.read_bytes()
        with self.assertRaises(ValueError):
            ota.install_bundle(bundle(), self.root)
        self.assertEqual(target.read_bytes(), before)

    def test_incomplete_package(self):
        with self.assertRaises(ValueError):
            ota.install_bundle(bundle(files={'app/slave.py': b''}), self.root)

    def test_interrupted_write(self):
        bootstate.load(self.root)
        with mock.patch.object(storage, 'atomic_bytes', side_effect=OSError('power loss')):
            with self.assertRaises(OSError):
                ota.install_bundle(bundle(), self.root)
        self.assertIsNone(bootstate.load(self.root)['pending'])

class StateTests(RootTest):
    def test_attempt_then_next_reset_rollback(self):
        self.assertEqual(bootstate.select_for_boot(self.root), 'a')
        bootstate.stage('b', 1, self.root)
        self.assertEqual(bootstate.select_for_boot(self.root), 'b')
        self.assertTrue(bootstate.load(self.root)['pending']['attempted'])
        self.assertEqual(bootstate.select_for_boot(self.root), 'a')
        self.assertIsNone(bootstate.load(self.root)['pending'])
        self.assertEqual(bootstate.load(self.root)['highwater'], 0)

    def test_confirmation_monotonic(self):
        bootstate.stage('b', 2, self.root)
        with self.assertRaises(ValueError):
            bootstate.confirm_pending(self.root)
        bootstate.select_for_boot(self.root)
        self.assertEqual(bootstate.confirm_pending(self.root)['highwater'], 2)
        self.assertEqual(bootstate.select_for_boot(self.root), 'b')
        for version in (1, 2, True):
            with self.assertRaises(ValueError):
                bootstate.stage('a', version, self.root)
        bootstate.stage('a', 3, self.root)
        with self.assertRaises(ValueError):
            bootstate.stage('a', 4, self.root)

    def test_backup_recovery_never_boots_pending(self):
        bootstate.stage('b', 2, self.root)
        (self.directory / 'config/bootstate.json').write_text('{broken')
        state = bootstate.load(self.root)
        self.assertEqual(state['confirmed'], {'slot': 'a', 'version': 0})
        self.assertIsNone(state['pending'])
        self.assertEqual(bootstate.select_for_boot(self.root), 'a')

    def test_confirmed_backup_preserves_highwater(self):
        bootstate.stage('b', 5, self.root)
        bootstate.select_for_boot(self.root)
        bootstate.confirm_pending(self.root)
        (self.directory / 'config/bootstate.json').unlink()
        self.assertEqual(bootstate.load(self.root)['highwater'], 5)

    def test_corrupt_state_fails_closed(self):
        for name in ('bootstate.json', 'bootstate.json.bak'):
            (self.directory / 'config' / name).write_text('{}')
        with self.assertRaises(ValueError):
            bootstate.load(self.root)
        with self.assertRaises(ValueError):
            bootstate.validate({'schema': 1, 'confirmed': {'slot': '../x', 'version': 0},
                                'highwater': 0, 'pending': None})


class TransportTests(RootTest):
    def download(self, response, **kwargs):
        sock = Socket(response)
        result = transport.get('http://updates.example:8080/bundle?x=1', root=self.root,
                               socket_module=sockets(sock), **kwargs)
        self.assertTrue(sock.closed)
        self.assertIn(b'Host: updates.example:8080', sock.outgoing)
        return result

    def test_bounded_get(self):
        self.assertEqual(self.download(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'), b'{}')

    def test_response_policies(self):
        for response in (b'HTTP/1.1 302 Found\r\nContent-Length: 0\r\n\r\n',
                         b'HTTP/1.1 200 OK\r\n\r\n',
                         b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nx',
                         b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nTransfer-Encoding: chunked\r\n\r\n',
                         b'HTTP/1.1 200 OK\r\nContent-Length: 999999\r\n\r\n',
                         b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n'):
            with self.assertRaises(ValueError):
                self.download(response)

    def test_url_policy(self):
        for url in ('http://user:pass@host/x', 'http://host/x\r\nBad: hi',
                    'ftp://host/x', 'http://host/#fragment', 'http://host:abc/x'):
            with self.assertRaises(ValueError):
                transport.parse_url(url)
        with self.assertRaises(ValueError):
            transport.parse_url(WIFI['ota_url'], iteration=2)

    def test_tls_unsupported_fails_before_network(self):
        fake_ssl = types.SimpleNamespace(SSLContext=lambda mode: object(),
                                         PROTOCOL_TLS_CLIENT=1, CERT_REQUIRED=2)
        network = mock.Mock()
        with self.assertRaises((AttributeError, TypeError)):
            transport.get('https://updates.example/ota', root=self.root,
                          socket_module=network, ssl_module=fake_ssl)
        network.socket.assert_not_called()

    def test_tls_requires_ca_and_sni_and_does_not_fallback(self):
        (self.directory / 'config/ca.der').write_bytes(b'DER test only')
        raw = Socket()
        context = mock.Mock()
        context.wrap_socket.side_effect = OSError('certificate rejected')
        fake_ssl = types.SimpleNamespace(SSLContext=lambda mode: context,
                                         PROTOCOL_TLS_CLIENT=1, CERT_REQUIRED=2)
        with self.assertRaises(OSError):
            transport.get('https://updates.example/ota', root=self.root,
                          socket_module=sockets(raw), ssl_module=fake_ssl)
        self.assertEqual(context.verify_mode, 2)
        context.load_verify_locations.assert_called_once_with(cadata=b'DER test only')
        context.wrap_socket.assert_called_once_with(raw, server_hostname='updates.example')
        self.assertEqual(raw.outgoing, b'')
        self.assertTrue(raw.closed)


class ProvisioningTests(RootTest):
    def body(self, **kwargs):
        return dict({'token': SETUP.hex(), 'device': DEVICE, 'wifi': WIFI}, **kwargs)

    def test_authenticated_configure(self):
        provisioning.configure(self.body(), self.root)
        self.assertEqual(config.load_wifi(self.root), WIFI)
        self.assertFalse((self.directory / 'config/provisioning.incomplete').exists())
        self.assertEqual((self.directory / 'config/setup.key').read_bytes(), SETUP)

    def test_bad_tokens_and_override_fields(self):
        self.assertTrue(provisioning.authenticate(SETUP.hex().upper(), self.root))
        for token in ('', 'zz' * 32, KEY.hex(), None):
            self.assertFalse(provisioning.authenticate(token, self.root))
            with self.assertRaises(ValueError):
                provisioning.configure(self.body(token=token), self.root)
        with self.assertRaises(ValueError):
            provisioning.configure(dict(self.body(), files={'main.py': 'bad'}), self.root)

    def test_interrupted_configuration_is_blocked(self):
        original = storage.atomic_json
        def fail_device(filename, value):
            if filename.endswith('device.json'):
                raise OSError('power loss')
            original(filename, value)
        with mock.patch.object(storage, 'atomic_json', side_effect=fail_device):
            with self.assertRaises(OSError):
                provisioning.configure(self.body(), self.root)
        self.assertTrue((self.directory / 'config/provisioning.incomplete').exists())
        self.assertEqual(bootloader.run(self.root), 'configuration_failed_closed')
        provisioning.configure(self.body(), self.root)
        self.assertFalse((self.directory / 'config/provisioning.incomplete').exists())

    def test_form_contains_no_secrets(self):
        sock = Socket(b'GET / HTTP/1.1\r\nHost: device\r\n\r\n')
        self.assertEqual(provisioning.handle_client(sock, self.root), 'form')
        self.assertIn(b'200 OK', sock.outgoing)
        self.assertNotIn(SETUP.hex().encode(), sock.outgoing)
        self.assertNotIn(b'7H3uZ2Aa9vBb4K5c', sock.outgoing)

    def test_update_auth_and_no_url_override(self):
        update = mock.Mock()
        for body in ({'token': KEY.hex()}, {'token': SETUP.hex(), 'url': 'http://evil/x'}):
            sock = Socket(post('/update', body))
            self.assertNotEqual(provisioning.handle_client(sock, self.root, update), 'updated')
        update.assert_not_called()
        sock = Socket(post('/update', {'token': SETUP.hex()}))
        self.assertEqual(provisioning.handle_client(sock, self.root, update), 'updated')
        update.assert_called_once_with()
        self.assertNotIn(SETUP.hex().encode(), sock.outgoing)

    def test_request_total_limit_and_timeout(self):
        for data in (post('/configure', self.body(padding='x' * 4096)),
                     b'POST /configure HTTP/1.1\r\nContent-Length: 2\r\n\r\n{',
                     b'GET / HTTP/1.1\r\n' + b'X: ' + b'x' * 4096):
            self.assertEqual(provisioning.handle_client(Socket(data), self.root), 'refused')

    def test_usb_flag_is_consumed_even_without_device(self):
        flag = self.directory / 'config/maintenance.flag'
        flag.write_bytes(b'')
        self.assertTrue(maintenance.requested(self.root))
        self.assertFalse(flag.exists())
        self.assertFalse(maintenance.requested(self.root))

    def test_no_ap_with_missing_factory_material(self):
        (self.directory / 'config/setup.key').unlink()
        network = mock.Mock()
        with self.assertRaises(OSError):
            maintenance.run(self.root, network_module=network)
        network.WLAN.assert_not_called()

    def test_no_weak_ap_password(self):
        with self.assertRaises(ValueError):
            maintenance.validate_ap({'ssid': 'setup', 'password': 'short'})

    def test_maintenance_wpa2_only_cleanup_and_no_unsolicited_sta(self):
        station, ap, listener = mock.Mock(), mock.Mock(), mock.Mock()
        ap.ifconfig.return_value = ('192.168.4.1', '', '', '')
        listener.accept.side_effect = OSError('no client')
        network = types.SimpleNamespace(STA_IF=0, AP_IF=1, AUTH_WPA2_PSK=3,
                                        WLAN=mock.Mock(side_effect=[station, ap]))
        sock_module = types.SimpleNamespace(AF_INET=2, SOCK_STREAM=1,
                                            socket=mock.Mock(return_value=listener))
        self.assertEqual(maintenance.run(self.root, duration=1, network_module=network,
                                         socket_module=sock_module), 'maintenance_ended')
        station.connect.assert_not_called()
        ap.config.assert_called_once_with(essid='setup-random',
                                           password='7H3uZ2Aa9vBb4K5c', authmode=3)
        self.assertEqual(ap.active.call_args_list,
                         [mock.call(False), mock.call(True), mock.call(False)])
        listener.close.assert_called_once_with()
        listener.bind.assert_called_once_with(('192.168.4.1', 80))

    def test_gpio_request_requires_configured_pin_held_low(self):
        pin = mock.Mock()
        pin.value.side_effect = [0, 0, 0]
        constructor = mock.Mock(return_value=pin)
        constructor.IN, constructor.PULL_UP = 0, 2
        machine = types.SimpleNamespace(Pin=constructor)
        device = config.validate_device(dict(DEVICE, maintenance_pin=1))
        with mock.patch.object(maintenance, '_sleep_ms'):
            self.assertTrue(maintenance.requested(self.root, device, machine))
        constructor.assert_called_once_with(1, 0, 2)
        pin.value.side_effect = [0, 1]
        with mock.patch.object(maintenance, '_sleep_ms'):
            self.assertFalse(maintenance.requested(self.root, device, machine))

class BootTests(RootTest):
    def setUp(self):
        super().setUp()
        self.previous_path = list(sys.path)
        self.previous_app = {name: module for name, module in sys.modules.items()
                             if name == 'app' or name.startswith('app.')}
        self.addCleanup(self.restore_imports)

    def restore_imports(self):
        sys.path[:] = self.previous_path
        for name in list(sys.modules):
            if name == 'app' or name.startswith('app.'):
                del sys.modules[name]
        sys.modules.update(self.previous_app)

    def preload(self, slot='a', content='def run(config):\n    assert config["role"] == "master"\n'):
        directory = self.directory / 'slots' / slot / 'app'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / '__init__.py').write_text('')
        (directory / 'master.py').write_text(content)

    def test_normal_boot_no_maintenance_and_no_confirmation(self):
        self.preload()
        self.preload('b')
        bootstate.stage('b', 1, self.root)
        with mock.patch.object(maintenance, 'run') as maintain:
            self.assertEqual(bootloader.run(self.root), 'application_returned')
            maintain.assert_not_called()
        self.assertTrue(bootstate.load(self.root)['pending']['attempted'])
        self.assertEqual(bootstate.load(self.root)['highwater'], 0)
        self.assertEqual(bootloader.run(self.root), 'application_returned')
        self.assertIsNone(bootstate.load(self.root)['pending'])

    def test_missing_or_corrupt_config_never_opens_ap(self):
        (self.directory / 'config/device.json').write_text('{broken')
        with mock.patch.object(maintenance, 'run') as maintain:
            self.assertEqual(bootloader.run(self.root), 'configuration_failed_closed')
            maintain.assert_not_called()

    def test_explicit_flag_runs_only_maintenance(self):
        (self.directory / 'config/maintenance.flag').write_bytes(b'')
        with mock.patch.object(maintenance, 'run', return_value='maintenance_ended') as maintain:
            self.assertEqual(bootloader.run(self.root), 'maintenance_ended')
            maintain.assert_called_once_with(self.root)
        self.assertFalse((self.directory / 'config/bootstate.json').exists())

    def test_failed_pending_resets_once_then_confirmed_failure_stops(self):
        self.preload(content='def run(config):\n    raise ValueError("bench")\n')
        self.preload('b', content='def run(config):\n    raise ValueError("pending")\n')
        bootstate.stage('b', 1, self.root)
        machine = mock.Mock()
        self.assertEqual(bootloader.run(self.root, machine), 'application_failed_closed')
        machine.reset.assert_called_once_with()
        machine.reset.reset_mock()
        self.assertEqual(bootloader.run(self.root, machine), 'application_failed_closed')
        machine.reset.assert_not_called()
        self.assertIsNone(bootstate.load(self.root)['pending'])

    def test_firmware_has_no_print_calls(self):
        import ast
        files = list((IOT / 'system').glob('*.py')) + [IOT / 'main.py', IOT / 'bootloader.py']
        for filename in files:
            tree = ast.parse(filename.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, 'print', str(filename))


if __name__ == '__main__':
    unittest.main()
