"""Authenticated application-only OTA. Both slots are mutable, core is not.

The HMAC covers the entire canonical manifest, INCLUDING base64 file contents.
This is symmetric authentication with per-device secrets, not secure boot.
"""
import os
import json
import binascii
from system import bootstate, storage
from system.canonical import dumps
from system.crypto import constant_time_equal, hex_bytes, hex_digest, hmac_sha256
from system.transport import MAX_BUNDLE

MAX_FILES = 32
MAX_TOTAL = 192 * 1024


def _identifier(value):
    return (bool(value) and value[0] in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_'
            and all(c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for c in value))


def validate_path(value):
    if not isinstance(value, str) or len(value) > 160:
        raise ValueError('invalid application path')
    parts = value.split('/')
    if len(parts) < 2 or len(parts) > 10 or parts[0] != 'app' or not parts[-1].endswith('.py'):
        raise ValueError('only app Python files are allowed')
    if not all(_identifier(part) for part in parts[1:-1]) or not _identifier(parts[-1][:-3]):
        raise ValueError('unsafe application path')
    return value


def _decode(value):
    if not isinstance(value, str) or len(value) > ((MAX_TOTAL + 2) // 3) * 4:
        raise ValueError('invalid base64 content')
    if any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in value):
        raise ValueError('invalid base64 character')
    try:
        decoded = binascii.a2b_base64(value)
        canonical = binascii.b2a_base64(decoded).strip().decode('ascii')
    except (ValueError, TypeError):
        raise ValueError('invalid base64')
    if canonical != value:
        raise ValueError('noncanonical base64')
    return decoded


def verify_bundle(payload, key):
    """Pure validation; returns (version, [(relative_path, bytes), ...])."""
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_BUNDLE:
        raise ValueError('invalid bundle length')
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError('OTA key must contain 32 bytes')
    bundle = json.loads(payload.decode('utf-8'))
    if not isinstance(bundle, dict) or set(bundle) != {'manifest', 'hmac_sha256'}:
        raise ValueError('invalid bundle envelope')
    manifest = bundle['manifest']
    if not isinstance(manifest, dict) or set(manifest) != {'version', 'files'}:
        raise ValueError('invalid manifest fields')
    expected = hmac_sha256(key, dumps(manifest).encode('utf-8'))
    supplied = hex_bytes(bundle['hmac_sha256'], 32)
    if not constant_time_equal(expected, supplied):
        raise ValueError('OTA authentication failed')
    version = manifest['version']
    if type(version) is not int or version <= 0:
        raise ValueError('version must be a positive integer')
    files = manifest['files']
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('invalid file count')
    seen = set()
    total = 0
    verified = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path', 'size', 'sha256', 'content_b64'}:
            raise ValueError('invalid file record')
        filename = validate_path(item['path'])
        if filename in seen:
            raise ValueError('duplicate application path')
        seen.add(filename)
        size = item['size']
        if type(size) is not int or size < 0 or size > MAX_TOTAL:
            raise ValueError('invalid file size')
        total += size
        if total > MAX_TOTAL:
            raise ValueError('application is too large')
        hex_bytes(item['sha256'], 32)
        content = _decode(item['content_b64'])
        if len(content) != size or not constant_time_equal(hex_digest(content), item['sha256'].lower()):
            raise ValueError('application size/hash mismatch')
        verified.append((filename, content))
    # No file may also be the parent directory of another file.
    for filename in seen:
        if any(other.startswith(filename + '/') for other in seen):
            raise ValueError('conflicting application paths')
    return version, verified


def _controlled_files(directory, relative='app'):
    """Inventory first. Never traverse links or remove non-application files."""
    if not storage.exists(directory):
        return []
    mode = getattr(os, 'lstat', os.stat)(directory)[0] & 0xf000
    if mode != 0x4000:
        raise ValueError('slot application is not a directory')
    result = []
    for name in os.listdir(directory):
        full = directory + '/' + name
        rel = relative + '/' + name
        mode = getattr(os, 'lstat', os.stat)(full)[0] & 0xf000
        if mode == 0x4000 and _identifier(name):
            result.extend(_controlled_files(full, rel))
        elif mode == 0x8000:
            validate_path(rel[:-4] if rel.endswith('.tmp') else rel)
            result.append(full)
        else:
            raise ValueError('uncontrolled inactive slot content')
    return result


def install_bundle(payload, root='/'):
    """Authenticate fully, replace ONLY inactive app files, then stage journal.

Returns pending state, does not reboot or confirm. Requires a complete app
package including app/__init__.py and the configured role's entry module.
"""
    version, files = verify_bundle(payload, storage.secret(root, 'ota.key'))
    state = bootstate.load(root)
    if state['pending'] is not None or version <= state['highwater']:
        raise ValueError('pending update or non-monotonic version')
    from system.config import load_device
    device = load_device(root)
    filenames = {name for name, content in files}
    if 'app/__init__.py' not in filenames or 'app/' + device['role'] + '.py' not in filenames:
        raise ValueError('bundle does not include the configured entry package')
    slot = 'b' if state['confirmed']['slot'] == 'a' else 'a'
    directory = storage.path(root, 'slots/' + slot)
    storage.mkdirs(directory)
    old_files = _controlled_files(directory + '/app')
    for filename in old_files:
        os.remove(filename)
    for name, content in files:
        filename = directory + '/' + name
        storage.mkdirs(filename.rsplit('/', 1)[0])
        storage.atomic_bytes(filename, content)
    # Journal remains unchanged until every byte is present and verified.
    for name, content in files:
        actual = storage.read_bytes(directory + '/' + name, len(content))
        if not constant_time_equal(hex_digest(actual), hex_digest(content)):
            raise ValueError('staged file verification failed')
    storage.sync()
    return bootstate.stage(slot, version, root)


def download_and_stage(root='/', fetch=None, timeout=30):
    """Use ONLY persisted URL/credentials, never fields from a network request."""
    from system.config import load_device, load_wifi
    from system.transport import get
    device = load_device(root)
    wifi = load_wifi(root, device['iteration'])
    if fetch is None:
        fetch = get
    payload = fetch(wifi['ota_url'], iteration=device['iteration'], root=root,
                    limit=MAX_BUNDLE, timeout=timeout)
    return install_bundle(payload, root)
