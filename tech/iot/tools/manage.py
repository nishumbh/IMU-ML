"""Host-only factory kits and authenticated application bundles; never run on MCU.

Examples in ../README.md. Generated kits contain credentials: keep them private.
"""
import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)
    path.chmod(0o600)


def json_bytes(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def mac(value):
    value = value.replace(':', '').lower()
    if not re.fullmatch('[0-9a-f]{12}', value) or int(value[:2], 16) & 1 or value == '000000000000':
        raise argparse.ArgumentTypeError('Expected nonzero unicast STA MAC (12 hex digits)')
    return value


def factory_pair(args):
    if args.master_mac == args.slave_mac:
        raise ValueError('Each device must have its own STA MAC')
    if args.out.exists():
        raise ValueError('Output exists; never overwrite device credentials')
    args.out.mkdir(parents=True, mode=0o700)
    pmk, lmk = secrets.token_hex(16), secrets.token_hex(16)
    for role, peer in (('master', args.slave_mac), ('slave', args.master_mac)):
        directory = args.out / role
        directory.mkdir(mode=0o700)
        (directory / 'slots' / 'a').mkdir(parents=True)
        shutil.copytree(ROOT / 'app', directory / 'slots' / 'a' / 'app',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copytree(ROOT / 'system', directory / 'system',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for filename in ('main.py', 'bootloader.py'):
            shutil.copyfile(ROOT / filename, directory / filename)
        config = {'role': role, 'iteration': args.iteration, 'channel': args.channel,
                  'peer_mac': peer, 'pmk': pmk if args.iteration == 2 else None,
                  'lmk': lmk if args.iteration == 2 else None, 'sample_rate_hz': 50,
                  'imu_address': 104, 'maintenance_pin': None,
                  'pins': {'i2c_sda': 4, 'i2c_scl': 5, 'sd_sck': 6,
                           'sd_mosi': 7, 'sd_miso': 3, 'sd_cs': 10}}
        setup_key, ota_key = secrets.token_bytes(32), secrets.token_bytes(32)
        ap = {'ssid': 'KneeSetup-' + secrets.token_hex(3),
              'password': secrets.token_urlsafe(15)}
        write_private(directory / 'config/device.json', json_bytes(config))
        write_private(directory / 'config/setup.key', setup_key)
        write_private(directory / 'config/ota.key', ota_key)
        write_private(directory / 'config/setup_ap.json', json_bytes(ap))
        # Outside the deployed root: human-readable secrets for the authorized owner.
        write_private(args.out / (role + '_PRIVATE.json'), json_bytes({
            'role': role, 'ap': ap, 'setup_token_hex': setup_key.hex(),
            'note': 'SECRET. Do not copy this file to SD, git, screenshots or logs.'}))
    # No secrets printed, including through process arguments.


def build_ota(args):
    from system.canonical import dumps
    key = args.key.read_bytes()
    if len(key) != 32 or args.version < 1:
        raise ValueError('Require a 32-byte raw device OTA key and positive version')
    files = []
    for path in sorted(args.app_dir.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        data = path.read_bytes()
        relative = 'app/' + path.relative_to(args.app_dir).as_posix()
        files.append({'path': relative, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                      'content_b64': base64.b64encode(data).decode('ascii')})
    if not files or len(files) > 32 or sum(f['size'] for f in files) > 192 * 1024:
        raise ValueError('Bundle requires 1..32 Python files and at most 192 KiB decoded')
    manifest = {'version': args.version, 'files': files}
    digest = hmac.new(key, dumps(manifest).encode('utf-8'), hashlib.sha256).hexdigest()
    body = json_bytes({'manifest': manifest, 'hmac_sha256': digest})
    if len(body) > 256 * 1024:
        raise ValueError('Encoded bundle exceeds 256 KiB')
    write_private(args.output, body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    factory = commands.add_parser('factory-pair')
    factory.add_argument('--master-mac', required=True, type=mac)
    factory.add_argument('--slave-mac', required=True, type=mac)
    factory.add_argument('--iteration', type=int, choices=(1, 2), default=2)
    factory.add_argument('--channel', type=int, choices=range(1, 12), default=6)
    factory.add_argument('--out', type=Path, required=True)
    factory.set_defaults(func=factory_pair)
    bundle = commands.add_parser('build-ota')
    bundle.add_argument('--app-dir', type=Path, default=ROOT / 'app')
    bundle.add_argument('--key', type=Path, required=True)
    bundle.add_argument('--version', type=int, required=True)
    bundle.add_argument('--output', type=Path, required=True)
    bundle.set_defaults(func=build_ota)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
