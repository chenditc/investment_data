import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def freshness_response():
    return {
        "data": {
            "sqlSelect": {
                "queryExecutionStatus": "Success",
                "columns": [{"name": "source_max_date"}, {"name": "target_trade_date"}],
                "rows": [{"columnValues": [
                    {"displayValue": "2026-11-06"}, {"displayValue": "2026-11-06"},
                ]}],
            }
        }
    }


class ReleaseGateTest(unittest.TestCase):
    def run_query(self, response, curl_status=0, tag="2026-11-08"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            response_file = root / "response.json"
            response_file.write_text(response, encoding="utf-8")
            arguments_file = root / "arguments"
            curl = root / "curl"
            curl.write_text(
                '#!/usr/bin/env bash\n'
                'printf "%s\\n" "$@" >"$CURL_ARGUMENTS"\n'
                'cat "$CURL_RESPONSE"\n'
                'exit "$CURL_STATUS"\n',
                encoding="utf-8",
            )
            curl.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"])
            env.update(
                CURL_ARGUMENTS=str(arguments_file), CURL_RESPONSE=str(response_file),
                CURL_STATUS=str(curl_status), GITHUB_TOKEN="never-send-this-to-dolthub",
            )
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; query_freshness "$2"',
                 "bash", str(ROOT / "release_gate.sh"), tag],
                env=env, text=True, capture_output=True,
            )
            arguments = arguments_file.read_text().splitlines() if arguments_file.exists() else []
            return result, arguments

    def test_graphql_query_preserves_dates_and_does_not_forward_github_token(self):
        result, arguments = self.run_query(json.dumps(freshness_response()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "2026-11-06\t2026-11-06\n")
        self.assertEqual(arguments[-1], "https://www.dolthub.com/graphql")
        payload = json.loads(arguments[arguments.index("--data-binary") + 1])
        self.assertIn("sqlSelect", payload["query"])
        self.assertIn("date <= '2026-11-08'", payload["variables"]["sql"])
        self.assertNotIn("never-send-this-to-dolthub", " ".join(arguments))
        self.assertIn("--connect-timeout", arguments)
        self.assertIn("--max-time", arguments)
        self.assertIn("--retry-max-time", arguments)

    def test_invalid_or_partial_graphql_responses_are_rejected(self):
        valid = freshness_response()
        error = copy.deepcopy(valid)
        error["errors"] = [{"message": "partial SQL failure"}]
        failed = copy.deepcopy(valid)
        failed["data"]["sqlSelect"]["queryExecutionStatus"] = "Error"
        swapped = copy.deepcopy(valid)
        swapped["data"]["sqlSelect"]["columns"].reverse()
        no_rows = copy.deepcopy(valid)
        no_rows["data"]["sqlSelect"]["rows"] = []
        extra_rows = copy.deepcopy(valid)
        extra_rows["data"]["sqlSelect"]["rows"] *= 2
        null_date = copy.deepcopy(valid)
        null_date["data"]["sqlSelect"]["rows"][0]["columnValues"][0]["displayValue"] = None
        bad_date = copy.deepcopy(valid)
        bad_date["data"]["sqlSelect"]["rows"][0]["columnValues"][0]["displayValue"] = "2026-02-30"
        for response in [error, failed, swapped, no_rows, extra_rows, null_date, bad_date, {}]:
            with self.subTest(response=response):
                result, _ = self.run_query(json.dumps(response))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        result, _ = self.run_query("upstream request timeout", curl_status=22)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_invalid_query_date_is_rejected_before_network(self):
        result, arguments = self.run_query("", tag="2026-02-30")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(arguments, [])

    def test_online_failure_defers_to_local_validation_instead_of_skipping(self):
        result = subprocess.run(
            ["bash", "-c", r'''
                source "$1"
                github_get_release_by_tag_optional() { return 4; }
                query_freshness() { return 1; }
                publication_mode
            ''', "bash", str(ROOT / "release_gate.sh")],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "publish\n")
        self.assertIn("mandatory local snapshot and archive validation", result.stderr)

    def test_local_validation_failure_after_online_failure_prevents_release_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["bash", "-c", r'''
                    source "$1"
                    github_get_release_by_tag_optional() { return 4; }
                    query_freshness() { return 1; }
                    [[ "$(publication_mode)" == publish ]]
                    source "$2"
                    WORK_DIR=$3
                    select_normal_dolt_commit() { printf '%s\n' '9vtplc2tar9ver7p6s1bus2oiedjvtqo'; }
                    build_pair() { return 1; }
                    get_or_create_release() { touch "$WORK_DIR/mutation"; }
                    publish_current
                ''', "bash", str(ROOT / "release_gate.sh"),
                 str(ROOT / "upload_release.sh"), temporary],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(temporary) / "mutation").exists())

    def test_export_rejects_an_index_only_latest_date_before_creating_archive(self):
        from test_release_safety import StaticSafetyContractTest

        fixture = StaticSafetyContractTest()
        try:
            for count in ("0", "6", "1000", "invalid"):
                with self.subTest(rows=count):
                    evidence = fixture._run_stubbed_standalone_dump(price_count=count)
                    self.assertNotEqual(evidence["result"].returncode, 0)
                    self.assertIn("expected more than 1000", evidence["result"].stderr)
                    self.assertFalse((evidence["investment_data"] / "qlib_bin.tar.gz").exists())
        finally:
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
