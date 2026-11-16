"""Optional /main.py wrapper for a provisioned master; prefer common factory main."""
import json
with open('/config/device.json') as stream:
    _config = json.load(stream)
if _config.get('role') != 'master' or _config.get('iteration') != 2:
    raise ValueError('Expected a provisioned iteration-2 master')
import bootloader
bootloader.run()
