import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DumpStatusTest(unittest.TestCase):
    def run_status(self, status, dirty_rows=0):
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
    print("Your branch is behind 'origin/master' by 1 commit.")
    sys.exit(0)
if sys.argv[1] == 'sql' and 'dolt_status' in ' '.join(sys.argv):
    print('dirty_rows')
    print(os.environ['DIRTY_ROWS'])
    sys.exit(int(os.environ['STATUS_EXIT']))
if sys.argv[1] == 'fetch':
    Path(os.environ['FETCH_MARKER']).touch()
    sys.exit(37)
sys.exit(98)
''')
            fake.chmod(0o755)
            marker=root/'fetched'
            result=subprocess.run(['bash',str(ROOT/'dump_qlib_bin.sh'),str(root)],capture_output=True,text=True,env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'STATUS_EXIT':str(status),'DIRTY_ROWS':str(dirty_rows),'FETCH_MARKER':str(marker)})
            return result,marker.exists()

    def test_clean_checkout_is_accepted_when_cli_omits_clean_marker(self):
        result,fetched=self.run_status(0)
        self.assertTrue(fetched,result.stderr)
        self.assertEqual(result.returncode,37)
        self.assertNotIn('checkout is not clean',result.stderr)

    def test_failed_status_query_stops_even_if_it_printed_zero(self):
        result,fetched=self.run_status(17)
        self.assertFalse(fetched)
        self.assertNotEqual(result.returncode,0)

    def test_uncommitted_table_is_rejected(self):
        result,fetched=self.run_status(0,dirty_rows=1)
        self.assertFalse(fetched)
        self.assertNotEqual(result.returncode,0)

    @unittest.skipUnless(os.environ.get('DOLT_BIN') or shutil.which('dolt'),'Dolt engine unavailable')
    def test_real_dolt_behind_remote_is_clean_but_staged_and_unstaged_changes_are_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);upstream=root/'upstream';upstream.mkdir()
            binary=os.environ.get('DOLT_BIN') or shutil.which('dolt')
            def run(cwd,*args):
                return subprocess.check_output([binary,*args],cwd=cwd,text=True,stderr=subprocess.STDOUT)
            run(upstream,'init','--name','Test','--email','test@localhost')
            initial=run(upstream,'branch','--show-current').strip()
            if initial != 'master':run(upstream,'branch','-m',initial,'master')
            run(upstream,'sql','-q','CREATE TABLE sample (id INT PRIMARY KEY)')
            run(upstream,'add','-A');run(upstream,'commit','-m','base')
            remote=(root/'remote').as_uri()
            run(upstream,'remote','add','origin',remote)
            run(upstream,'push','--set-upstream','origin','master')
            dolt_root=root/'dolt';dolt_root.mkdir()
            run(dolt_root,'clone',remote,'investment_data')
            run(upstream,'sql','-q','INSERT INTO sample VALUES (1)')
            run(upstream,'add','-A');run(upstream,'commit','-m','next');run(upstream,'push','origin','master')
            checkout=dolt_root/'investment_data'
            run(checkout,'fetch','origin','master')
            status=run(checkout,'status')
            self.assertIn('behind',status)
            self.assertNotIn('working tree clean',status)
            bin_dir=root/'bin';bin_dir.mkdir();(bin_dir/'dolt').symlink_to(binary)
            body=r'''
source "$PUBLISHER"
eval "$(declare -f select_normal_dolt_commit | sed 's#local dolt_dir=/dolt checkout=/dolt/investment_data commit#local dolt_dir="$TEST_DOLT_ROOT" checkout="$TEST_DOLT_ROOT/investment_data" commit#')"
select_normal_dolt_commit
'''
            env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'PUBLISHER':str(ROOT/'upload_release.sh'),'TEST_DOLT_ROOT':str(dolt_root)}
            result=subprocess.run(['bash','-c',body],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            expected=run(upstream,'sql','-r','csv','-q',"SELECT DOLT_HASHOF('HEAD') value").strip().splitlines()[-1]
            self.assertEqual(result.stdout.strip(),expected)
            run(checkout,'sql','-q','INSERT INTO sample VALUES (2)')
            for staged in [False,True]:
                if staged:run(checkout,'add','-A')
                result=subprocess.run(['bash','-c',body],env=env,capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('not clean',result.stderr)
                self.assertEqual(run(checkout,'sql','-r','csv','-q','SELECT id FROM sample').strip().splitlines(),['id','2'])


if __name__=='__main__':unittest.main()
