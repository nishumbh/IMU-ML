"""Immutable root launcher. Slot updates cannot replace this file or system/."""
import sys
from system import bootstate, storage
from system.config import load_device
from system import maintenance


def run(root='/', machine_module=None):
    """Select a slot and call app.master/slave.run(config); never enable Wi-Fi
    on the normal path. On any failure, remain closed for manual USB recovery.
    Only an explicit GPIO/USB request can enter the maintenance HTTP server.
    """
    try:
        device = load_device(root)
    except Exception:
        device = None
    try:
        if maintenance.requested(root, device, machine_module):
            return maintenance.run(root)
    except Exception:
        return 'maintenance_failed_closed'
    if device is None or storage.exists(storage.path(root, 'config/provisioning.incomplete')):
        return 'configuration_failed_closed'
    selected = None
    try:
        selected = bootstate.select_for_boot(root)
        slot_path = storage.path(root, 'slots/' + selected)
        # A full package must be preloaded in slot a for factory version zero.
        for filename in ('app/__init__.py', 'app/' + device['role'] + '.py'):
            if not storage.exists(slot_path + '/' + filename):
                raise ValueError('selected application package missing')
        # On a real reset there is no cached app. Clear it for USB soft calls
        # too, and avoid accumulating slot search paths across repeated calls.
        for name in list(sys.modules):
            if name == 'app' or name.startswith('app.'):
                del sys.modules[name]
        for slot in ('a', 'b'):
            old = storage.path(root, 'slots/' + slot)
            while old in sys.path:
                sys.path.remove(old)
        sys.path.insert(0, slot_path)
        module = __import__('app.' + device['role'], None, None, ('run',))
        module.run(device)
        # Successful return is NOT a health confirmation or a reboot request.
        return 'application_returned'
    except Exception:
        # A failed pending image gets one reset, after which its persisted
        # attempt is rolled back. Confirmed failures never cause reset loops.
        try:
            state = bootstate.load(root)
            pending = state['pending']
            if pending is not None and pending['attempted'] and pending['slot'] == selected:
                if machine_module is None:
                    import machine as machine_module
                machine_module.reset()
        except Exception:
            pass
        return 'application_failed_closed'
