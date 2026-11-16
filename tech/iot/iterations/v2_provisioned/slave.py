"""Optional /main.py wrapper for a provisioned slave; prefer common factory main."""
import json
with open('/config/device.json') as stream:
    _config = json.load(stream)
if _config.get('role') != 'slave' or _config.get('iteration') != 2:
    raise ValueError('Expected a provisioned iteration-2 slave')
import bootloader
bootloader.run()
