import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

path=Path(__file__).resolve().parents[1]/'ops/download_dolt.py'
spec=importlib.util.spec_from_file_location('tested_dolt_download',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class DoltDownloadTest(unittest.TestCase):
    def test_verified_asset_can_be_downloaded(self):
        data=b'pinned release';expected=hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'dolt.tar.gz'
            with mock.patch.dict(module.RELEASES,{'test':('https://example.test/asset',expected)}),mock.patch.object(module.urllib.request,'urlopen',return_value=io.BytesIO(data)):
                module.download('test',target)
            self.assertEqual(target.read_bytes(),data)

    def test_wrong_release_bytes_are_rejected_and_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'dolt.tar.gz'
            with mock.patch.dict(module.RELEASES,{'test':('https://example.test/asset','0'*64)}),mock.patch.object(module.urllib.request,'urlopen',side_effect=lambda *a,**k:io.BytesIO(b'wrong')),mock.patch.object(module.time,'sleep'):
                with self.assertRaisesRegex(ValueError,'checksum mismatch'):module.download('test',target)
            self.assertFalse(target.exists())

if __name__=='__main__':unittest.main()
