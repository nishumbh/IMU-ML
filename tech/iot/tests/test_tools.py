import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('iot_manage', ROOT / 'tools/manage.py')
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class ToolsTests(unittest.TestCase):
    def test_mac_validation(self):
        self.assertEqual(manage.mac('AA:BB:CC:DD:EE:10'), 'aabbccddee10')
        for value in ('000000000000', 'ffffffffffff', '010000000002', 'bad'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                manage.mac(value)

    def test_private_pair_unique_secrets_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            for package in ('app', 'system'):
                (source / package).mkdir(parents=True)
                (source / package / '__init__.py').write_text('')
            for filename in ('bootloader.py', 'main.py'):
                (source / filename).write_text('# fixture\n')
            args = argparse.Namespace(out=root/'private', master_mac='020000000001',
                                      slave_mac='020000000002', iteration=2, channel=6)
            with patch.object(manage, 'ROOT', source):
                manage.factory_pair(args)
                with self.assertRaises(ValueError):
                    manage.factory_pair(args)
            master, slave = args.out/'master', args.out/'slave'
            a = json.loads((master/'config/device.json').read_text())
            b = json.loads((slave/'config/device.json').read_text())
            self.assertEqual(a['peer_mac'], args.slave_mac)
            self.assertEqual(b['peer_mac'], args.master_mac)
            self.assertEqual(a['lmk'], b['lmk'])
            self.assertNotEqual(a['pmk'], a['lmk'])
            self.assertNotEqual((master/'config/ota.key').read_bytes(),
                                (slave/'config/ota.key').read_bytes())
            self.assertEqual(len((master/'config/ota.key').read_bytes()), 32)
            self.assertFalse((master/'master_PRIVATE.json').exists())
            self.assertTrue((args.out/'master_PRIVATE.json').exists())


if __name__ == '__main__':
    unittest.main()
