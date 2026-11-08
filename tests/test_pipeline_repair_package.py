import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class RepairPackageTest(unittest.TestCase):
    def run_case(self,status):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);publisher=root/'publisher.sh'
            publisher.write_text('''
preflight() { [[ "$1" == publish ]] || return 91; return "$PREFLIGHT_STATUS"; }
select_normal_dolt_commit() { printf '%s\\n' 'kl67hfmqtpko9f0kjnu0hqdutjbvvkqs'; }
build_pair() { [[ "$2" == kl67hfmqtpko9f0kjnu0hqdutjbvvkqs && "$3" == /output ]] || return 92; touch "$RESULT/built"; }
publish_current() { touch "$RESULT/published"; }
''')
            source=Path(__file__).resolve().parents[1]/'ops/build_source_repair_package.sh'
            script=root/'build.sh';script.write_text(source.read_text().replace('/investment_data/upload_release.sh',str(publisher)))
            result=subprocess.run(['bash',str(script)],env={**os.environ,'RESULT':str(root),'PREFLIGHT_STATUS':str(status)},capture_output=True,text=True)
            return result.returncode,(root/'built').exists(),(root/'published').exists()

    def test_artifact_build_uses_publisher_authority_and_does_not_publish(self):
        self.assertEqual(self.run_case(0),(0,True,False))

    def test_preflight_failure_prevents_build_and_publication(self):
        code,built,published=self.run_case(1)
        self.assertNotEqual(code,0);self.assertFalse(built);self.assertFalse(published)

if __name__=='__main__':unittest.main()
