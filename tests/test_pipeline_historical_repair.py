import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


class HistoricalRepairGuardTest(unittest.TestCase):
    def run_case(self, *, status=0, event='workflow_dispatch', source=None):
        original=(Path(__file__).resolve().parents[1]/'ops/repair_release_20261108.sh').read_text()
        expected=re.search(r'^expected_source=([0-9a-v]{32})$',original,re.M).group(1)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            publisher=root/'publisher.sh'
            publisher.write_text('''
ARCHIVE_NAME=qlib_bin.tar.gz
MANIFEST_NAME=qlib_bin.manifest.json
preflight() { return "$PREFLIGHT_STATUS"; }
die() { printf '%s\\n' "$*" >&2; return 1; }
select_normal_dolt_commit() { printf '%s\\n' "$SOURCE_COMMIT"; }
build_pair() { touch "$RESULT/built"; }
get_existing_release() { RELEASE_ID=438076075; }
list_assets() { printf '%s\\n' '[{"id":999,"name":"qlib_bin.tar.gz","digest":"sha256:eef9e283af7fa0747462fb8c9a1304bc04b66d6a83d420ef6f2c06d605ce36cc","state":"uploaded"},{"id":998,"name":"qlib_bin.manifest.json","state":"uploaded"}]'; }
slot_json() { jq -c --arg name "$2" '.[]|select(.name==$name)' <<<"$1"; }
github_delete_asset() { touch "$RESULT/deleted"; }
''')
            script=root/'repair.sh'
            script.write_text(original.replace('/investment_data/upload_release.sh',str(publisher)))
            result=subprocess.run(['bash',str(script)],capture_output=True,text=True,env={**os.environ,'RESULT':str(root),'PREFLIGHT_STATUS':str(status),'GITHUB_EVENT_NAME':event,'SOURCE_COMMIT':source or expected})
            return result,(root/'built').exists(),(root/'deleted').exists()

    def test_missing_publisher_authority_prevents_work(self):
        result,built,deleted=self.run_case(status=1)
        self.assertNotEqual(result.returncode,0);self.assertFalse(built);self.assertFalse(deleted)

    def test_schedule_cannot_invoke_historical_repair(self):
        result,built,deleted=self.run_case(event='schedule')
        self.assertNotEqual(result.returncode,0);self.assertFalse(built);self.assertFalse(deleted)

    def test_changed_source_prevents_work(self):
        result,built,deleted=self.run_case(source='a'*32)
        self.assertNotEqual(result.returncode,0);self.assertFalse(built);self.assertFalse(deleted)

    def test_changed_original_asset_identity_is_never_deleted(self):
        result,built,deleted=self.run_case()
        self.assertNotEqual(result.returncode,0);self.assertTrue(built);self.assertFalse(deleted)


if __name__=='__main__':unittest.main()
