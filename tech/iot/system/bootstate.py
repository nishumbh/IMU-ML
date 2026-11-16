"""Two-slot boot journal, recovered conservatively from the last valid backup.

Public USB API: confirm_pending(root='/'). Call only after a pending image has
booted and been bench-tested. Merely returning from app.run never confirms it.
No watchdog is created here: a hung image rolls back only AFTER a reset.
"""
from system import storage

_STATE = 'config/bootstate.json'


def _initial():
    return {'schema': 1, 'confirmed': {'slot': 'a', 'version': 0},
            'highwater': 0, 'pending': None}


def _record(value, pending=False):
    keys = {'slot', 'version', 'attempted'} if pending else {'slot', 'version'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('invalid slot record')
    if value['slot'] not in ('a', 'b') or type(value['version']) is not int or value['version'] < 0:
        raise ValueError('invalid slot/version')
    if pending and type(value['attempted']) is not bool:
        raise ValueError('invalid boot attempt')


def validate(value):
    if not isinstance(value, dict) or set(value) != {'schema', 'confirmed', 'highwater', 'pending'}:
        raise ValueError('invalid boot state fields')
    if type(value['schema']) is not int or value['schema'] != 1:
        raise ValueError('invalid boot state schema')
    _record(value['confirmed'])
    if type(value['highwater']) is not int or value['highwater'] != value['confirmed']['version']:
        raise ValueError('invalid high-water mark')
    if value['pending'] is not None:
        _record(value['pending'], True)
        if (value['pending']['slot'] == value['confirmed']['slot'] or
                value['pending']['version'] <= value['highwater']):
            raise ValueError('invalid pending state')
    return value


def _save(root, state):
    validate(state)
    filename = storage.path(root, _STATE)
    storage.mkdirs(storage.path(root, 'config'))
    # The backup is deliberately a known-confirmed recovery point, never a
    # route to boot an image whose attempt status might have been lost.
    if storage.exists(filename):
        try:
            previous = validate(storage.read_json(filename))
        except (ValueError, OSError, TypeError):
            previous = None
        if previous is not None:
            previous['pending'] = None
            storage.atomic_json(filename + '.bak', previous)
    storage.atomic_json(filename, state)
    # Refresh the backup on confirmation too, so the usual corruption recovery
    # preserves the confirmed high-water mark rather than a stale version.
    if state['pending'] is None:
        storage.atomic_json(filename + '.bak', state)


def load(root='/'):
    filename = storage.path(root, _STATE)
    found = False
    for candidate in (filename, filename + '.bak'):
        if not storage.exists(candidate):
            continue
        found = True
        try:
            state = validate(storage.read_json(candidate))
        except (ValueError, OSError, TypeError):
            continue
        if candidate != filename:
            state['pending'] = None
            _save(root, state)
        return state
    if found:
        # Do not reset a corrupt high-water mark to factory version zero.
        raise ValueError('boot journal corrupt; manual USB recovery required')
    state = _initial()
    _save(root, state)
    return state


def stage(slot, version, root='/'):
    state = load(root)
    if state['pending'] is not None:
        raise ValueError('an update is already pending')
    if slot not in ('a', 'b') or slot == state['confirmed']['slot']:
        raise ValueError('not the inactive slot')
    if type(version) is not int or version <= state['highwater']:
        raise ValueError('version must exceed confirmed high-water mark')
    state['pending'] = {'slot': slot, 'version': version, 'attempted': False}
    _save(root, state)
    return state


def select_for_boot(root='/'):
    """Persist attempt BEFORE import. On the next call, discard that attempt."""
    state = load(root)
    pending = state['pending']
    if pending is not None:
        if pending['attempted']:
            state['pending'] = None
            _save(root, state)
        else:
            pending['attempted'] = True
            _save(root, state)
            return pending['slot']
    return state['confirmed']['slot']


def confirm_pending(root='/'):
    """USB/operator health confirmation; returns the committed state."""
    state = load(root)
    pending = state['pending']
    if pending is None or not pending['attempted']:
        raise ValueError('no attempted pending image to confirm')
    state['confirmed'] = {'slot': pending['slot'], 'version': pending['version']}
    state['highwater'] = pending['version']
    state['pending'] = None
    _save(root, state)
    return state


def discard_pending(root='/'):
    """Explicit USB recovery; retains the confirmed image and high-water mark."""
    state = load(root)
    state['pending'] = None
    _save(root, state)
    return state
