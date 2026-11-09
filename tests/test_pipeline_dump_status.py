import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DumpStatusTest(unittest.TestCase):
    def run_status(self, status):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'investment_data/.git').mkdir(parents=True)
            (root/'dolt/investment_data/.dolt').mkdir(parents=True)
            bin_dir = root/'bin'; bin_dir.mkdir()
            fake = bin_dir/'dolt'
            fake.write_text('''#!/usr/bin/env python3
import os,signal,sys
from pathlib import Path
signal.signal(signal.SIGPIPE,signal.SIG_DFL)
if sys.argv[1] == 'status':
    print('nothing to commit, working tree clean',flush=True)
    sys.stdout.write('status diagnostic\\n' * 20000)
    sys.stdout.flush()
    sys.exit(int(os.environ['STATUS_EXIT']))
if sys.argv[1] == 'fetch':
    Path(os.environ['FETCH_MARKER']).touch()
    sys.exit(37)
sys.exit(98)
''')
            fake.chmod(0o755)
            marker=root/'fetched'
            result=subprocess.run(['bash',str(ROOT/'dump_qlib_bin.sh'),str(root)],capture_output=True,text=True,env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'STATUS_EXIT':str(status),'FETCH_MARKER':str(marker)})
            return result,marker.exists()

    def test_clean_status_output_is_fully_read_before_testing_cleanliness(self):
        result,fetched=self.run_status(0)
        self.assertTrue(fetched,result.stderr)
        self.assertEqual(result.returncode,37)
        self.assertNotIn('checkout is not clean',result.stderr)

    def test_failed_status_command_stops_even_if_it_printed_clean(self):
        result,fetched=self.run_status(17)
        self.assertFalse(fetched)
        self.assertNotEqual(result.returncode,0)


if __name__=='__main__':unittest.main()
