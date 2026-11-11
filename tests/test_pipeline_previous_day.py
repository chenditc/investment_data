import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PreviousDayPublicationTest(unittest.TestCase):
    def run_shell(self, body, *, today='2026-11-12', event='workflow_dispatch'):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools = root / 'bin'
            tools.mkdir()
            date = tools / 'date'
            date.write_text('''#!/usr/bin/env bash
set -eu
touch "$RESULT/date-called"
if [[ "$*" == '+%F' ]]; then
    printf '%s\\n' "$TODAY"
else
    exec /usr/bin/date "$@"
fi
''')
            date.chmod(0o755)
            result = subprocess.run(
                ['bash', '-c', 'source "$PUBLISHER"; ' + body],
                capture_output=True, text=True,
                env={**os.environ, 'PATH':str(tools)+os.pathsep+os.environ['PATH'],
                     'RESULT':str(root), 'TODAY':today, 'GITHUB_EVENT_NAME':event,
                     'PUBLISHER':str(ROOT/'upload_release.sh')})
            return result, {p.name:p.read_text() for p in root.iterdir() if p.is_file()}

    def test_previous_calendar_day_across_midnight_month_year_and_leap_day(self):
        for today, expected in [('2026-11-12','2026-11-11'),
                                ('2027-01-01','2026-12-31'),
                                ('2026-03-01','2026-02-28'),
                                ('2028-03-01','2028-02-29'),
                                ('2026-11-16','2026-11-15')]:
            with self.subTest(today=today):
                result, _ = self.run_shell(
                    'publish_for_tag() { printf "%s\\n" "$1"; }; publish_previous_day',
                    today=today)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),expected)

    def test_normal_publication_still_uses_current_calendar_day(self):
        result, _ = self.run_shell(
            'publish_for_tag() { printf "%s\\n" "$1"; }; publish_current')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'2026-11-12')

    def test_previous_day_rejects_nonmanual_events_before_work(self):
        for event in ('schedule','push'):
            with self.subTest(event=event):
                result, files = self.run_shell(
                    'publish_for_tag() { touch "$RESULT/published"; }; publish_previous_day',
                    event=event)
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('date-called',files)
                self.assertNotIn('published',files)

    def test_preflight_also_rejects_scheduled_previous_day_operation(self):
        result, files = self.run_shell('preflight publish-previous-day',event='schedule')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('requires workflow_dispatch',result.stderr)
        self.assertNotIn('date-called',files)

    def test_local_build_rejection_prevents_any_release_access(self):
        result, files = self.run_shell('''
WORK_DIR="$RESULT/work"
mkdir -p "$WORK_DIR"
select_normal_dolt_commit() { printf '%s\\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }
build_pair() { printf '%s\\n' "$1" >"$RESULT/build-tag"; return 17; }
get_or_create_release() { touch "$RESULT/release-accessed"; }
publish_previous_day
''')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(files['build-tag'].strip(),'2026-11-11')
        self.assertNotIn('release-accessed',files)

    def test_custom_date_argument_is_rejected(self):
        result = subprocess.run(
            ['bash',str(ROOT/'upload_release.sh'),'publish-previous-day','2026-11-01'],
            capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stdout,'')


if __name__ == '__main__':
    unittest.main()
