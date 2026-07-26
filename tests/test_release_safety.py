import gzip
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
from typing import Optional
import unittest
from unittest import mock

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "qlib/validate_archive.py"
QLIB_COMMIT = "b87a2c294d364a33fb739359886acffe8ec907d1"
COMPATIBILITY_LINK_NAME = "investment-data-project-monitor"
COMPATIBILITY_LINK_TARGET = "/localhome/local-dichen/.claude/skills/investment-data-project-monitor"
REQUIRED = {
    "qlib_bin/calendars/day.txt": "2026-07-17\n2026-07-20\n",
    "qlib_bin/calendars/day_future.txt": "2026-07-17\n2026-07-20\n2026-07-21\n2026-12-31\n",
    "qlib_bin/instruments/all.txt": "sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csi300.txt": "sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csi500.txt": "sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csi800.txt": "sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csi1000.txt": "sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csiall.txt": "sh600000\t2026-07-17\t2026-07-20\n",
}


def manifest_payload(archive, image_digest="sha256:" + "a" * 64):
    return {
        "release_tag": "2026-07-20",
        "target_trade_date": "2026-07-20",
        "future_start_date": "2026-07-21",
        "future_end_date": "2026-12-31",
        "dolt_commit": "9vtplc2tar9ver7p6s1bus2oiedjvtqo",
        "investment_data_commit": "0" * 40,
        "qlib_commit": QLIB_COMMIT,
        "image_digest": image_digest,
        "archive_size_bytes": archive.stat().st_size,
        "archive_sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
    }


def write_manifest(path, payload):
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def build_archive(path, members=None, extras=()):
    members = dict(REQUIRED if members is None else members)
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(members):
            data = members[name].encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.mtime = 1784505600
            archive.addfile(info, io.BytesIO(data))
        for info, data in extras:
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    with path.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=output, mtime=0) as zipped:
            zipped.write(tar_buffer.getvalue())
    return path


def build_pair(root, members=None, extras=(), image_digest="sha256:" + "a" * 64):
    archive = build_archive(root / "qlib_bin.tar.gz", members, extras)
    manifest = root / "qlib_bin.manifest.json"
    write_manifest(manifest, manifest_payload(archive, image_digest))
    return archive, manifest


def run_validator(archive, manifest, *extra):
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--archive",
            str(archive),
            "--manifest",
            str(manifest),
            "--expected-tag",
            "2026-07-20",
            *extra,
        ],
        text=True,
        capture_output=True,
    )


def load_fire_module(relative, name):
    fire = types.ModuleType("fire")
    fire.Fire = lambda *_args, **_kwargs: None
    with mock.patch.dict(sys.modules, {"fire": fire}):
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class ArchiveValidatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def assert_failure(self, archive, manifest, token, *extra):
        result = run_validator(archive, manifest, *extra)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, json.dumps({"ok": False, "error": token}, separators=(",", ":")) + "\n")

    def test_success_reports_both_complete_file_digests(self):
        archive, manifest = build_pair(self.root)
        result = run_validator(archive, manifest, "--require-publishable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith("\n"))
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {
                "ok": True,
                "result": {
                    "archive_sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "manifest_sha256": "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    "archive_size_bytes": archive.stat().st_size,
                    "target_trade_date": "2026-07-20",
                    "future_start_date": "2026-07-21",
                    "future_end_date": "2026-12-31",
                },
            },
        )

    def test_null_image_digest_is_diagnostic_only(self):
        archive, manifest = build_pair(self.root, image_digest=None)
        self.assertEqual(run_validator(archive, manifest).returncode, 0)
        self.assert_failure(archive, manifest, "invalid-manifest", "--require-publishable")

    def test_manifest_requires_exact_fields_order_types_and_identity(self):
        archive, manifest = build_pair(self.root)
        base = manifest_payload(archive)
        mutations = []
        missing = dict(base)
        missing.pop("future_end_date")
        mutations.append(json.dumps(missing, separators=(",", ":")) + "\n")
        extra = dict(base)
        extra["extra"] = True
        mutations.append(json.dumps(extra, separators=(",", ":")) + "\n")
        reversed_items = dict(reversed(list(base.items())))
        mutations.append(json.dumps(reversed_items, separators=(",", ":")) + "\n")
        duplicate = json.dumps(base, separators=(",", ":"))
        duplicate = duplicate[:-1] + ',"release_tag":"2026-07-20"}\n'
        mutations.append(duplicate)
        wrong_type = dict(base)
        wrong_type["archive_size_bytes"] = True
        mutations.append(json.dumps(wrong_type, separators=(",", ":")) + "\n")
        wrong_qlib = dict(base)
        wrong_qlib["qlib_commit"] = "1" * 40
        mutations.append(json.dumps(wrong_qlib, separators=(",", ":")) + "\n")
        uppercase = dict(base)
        uppercase["archive_sha256"] = "sha256:" + "A" * 64
        mutations.append(json.dumps(uppercase, separators=(",", ":")) + "\n")
        for index, text in enumerate(mutations):
            with self.subTest(index=index):
                manifest.write_text(text, encoding="utf-8")
                self.assert_failure(archive, manifest, "invalid-manifest")

    def test_every_manifest_field_type_and_canonical_form_is_enforced(self):
        archive, manifest = build_pair(self.root)
        base = manifest_payload(archive)
        cases = {
            "release-tag-type": ("release_tag", None),
            "release-tag-form": ("release_tag", "2026-7-20"),
            "target-date": ("target_trade_date", "2026-02-30"),
            "future-start": ("future_start_date", "2026-07-21T00:00:00Z"),
            "future-end": ("future_end_date", "2026-13-31"),
            "dolt-uppercase": ("dolt_commit", "Z" * 32),
            "dolt-short": ("dolt_commit", "a" * 31),
            "investment-uppercase": ("investment_data_commit", "A" * 40),
            "investment-short": ("investment_data_commit", "a" * 39),
            "qlib-type": ("qlib_commit", None),
            "qlib-wrong": ("qlib_commit", "0" * 40),
            "image-prefix": ("image_digest", "SHA256:" + "a" * 64),
            "image-uppercase": ("image_digest", "sha256:" + "A" * 64),
            "image-short": ("image_digest", "sha256:" + "a" * 63),
            "size-zero": ("archive_size_bytes", 0),
            "size-negative": ("archive_size_bytes", -1),
            "size-float": ("archive_size_bytes", 1.0),
            "size-string": ("archive_size_bytes", "1"),
            "archive-prefix": ("archive_sha256", "SHA256:" + "a" * 64),
            "archive-uppercase": ("archive_sha256", "sha256:" + "A" * 64),
            "archive-type": ("archive_sha256", None),
        }
        for label, (field, value) in cases.items():
            with self.subTest(label=label):
                payload = dict(base)
                payload[field] = value
                write_manifest(manifest, payload)
                self.assert_failure(archive, manifest, "invalid-manifest")

    def test_each_required_current_member_must_end_at_target(self):
        for name in [REQUIRED.keys().__iter__().__next__(), *list(REQUIRED)[2:]]:
            with self.subTest(name=name):
                members = dict(REQUIRED)
                if name.endswith("day.txt"):
                    members[name] = "2026-07-17\n"
                else:
                    members[name] = "sh600000\t2026-07-17\t2026-07-17\n"
                archive, manifest = build_pair(self.root, members)
                self.assert_failure(archive, manifest, "target-mismatch")

    def test_missing_and_malformed_required_members(self):
        members = dict(REQUIRED)
        members.pop("qlib_bin/instruments/csi300.txt")
        archive, manifest = build_pair(self.root, members)
        self.assert_failure(archive, manifest, "missing-required-member")

        members = dict(REQUIRED)
        members["qlib_bin/instruments/all.txt"] = "sh600000 2026-07-17 2026-07-20\n"
        archive, manifest = build_pair(self.root, members)
        self.assert_failure(archive, manifest, "malformed-required-member")

    def test_every_required_member_missing_duplicate_nonregular_and_malformed(self):
        for name in REQUIRED:
            with self.subTest(name=name, condition="missing"):
                members = dict(REQUIRED)
                members.pop(name)
                archive, manifest = build_pair(self.root, members)
                self.assert_failure(archive, manifest, "missing-required-member")

            with self.subTest(name=name, condition="duplicate"):
                duplicate = tarfile.TarInfo(name)
                duplicate.size = 1
                archive, manifest = build_pair(
                    self.root, extras=((duplicate, b"x"),)
                )
                self.assert_failure(archive, manifest, "unsafe-archive-member")

            with self.subTest(name=name, condition="nonregular"):
                members = dict(REQUIRED)
                members.pop(name)
                directory = tarfile.TarInfo(name)
                directory.type = tarfile.DIRTYPE
                archive, manifest = build_pair(
                    self.root, members, extras=((directory, None),)
                )
                self.assert_failure(archive, manifest, "missing-required-member")

            with self.subTest(name=name, condition="malformed"):
                members = dict(REQUIRED)
                if "/calendars/" in name:
                    members[name] = "not-a-date\n"
                else:
                    members[name] = "SH600000 2026-07-17 2026-07-20\n"
                archive, manifest = build_pair(self.root, members)
                self.assert_failure(archive, manifest, "malformed-required-member")

    def test_future_and_prefix_mismatches_have_stable_tokens(self):
        members = dict(REQUIRED)
        members["qlib_bin/calendars/day_future.txt"] = "2026-07-17\n2026-07-20\n2026-07-22\n2026-12-31\n"
        archive, manifest = build_pair(self.root, members)
        self.assert_failure(archive, manifest, "future-mismatch")

        members = dict(REQUIRED)
        members["qlib_bin/calendars/day_future.txt"] = "2026-07-18\n2026-07-20\n2026-07-21\n2026-12-31\n"
        archive, manifest = build_pair(self.root, members)
        self.assert_failure(archive, manifest, "prefix-mismatch")

    def test_archive_identity_and_open_failures(self):
        archive, manifest = build_pair(self.root)
        payload = manifest_payload(archive)
        payload["archive_size_bytes"] += 1
        write_manifest(manifest, payload)
        self.assert_failure(archive, manifest, "archive-identity-mismatch")

        archive.write_bytes(b"not a gzip archive")
        write_manifest(manifest, manifest_payload(archive))
        self.assert_failure(archive, manifest, "archive-open-failed")

        self.assert_failure(self.root / "absent.tar.gz", manifest, "io-error")

    def test_full_tar_scan_rejects_unsafe_paths_duplicates_and_types(self):
        unsafe = []
        for name in (
            "",
            ".",
            "./qlib_bin/escape",
            "/absolute",
            "../escape",
            "qlib_bin/../escape",
            "qlib_bin/nested/../../escape",
            "qlib_bin//double",
            "qlib_bin/back\\slash",
            "outside/file",
        ):
            info = tarfile.TarInfo(name)
            info.size = 1
            unsafe.append((name, info, b"x"))
        for type_name, type_value in (
            ("symlink", tarfile.SYMTYPE),
            ("hardlink", tarfile.LNKTYPE),
            ("char", tarfile.CHRTYPE),
            ("block", tarfile.BLKTYPE),
            ("fifo", tarfile.FIFOTYPE),
            ("socket", b"s"),
            ("sparse", tarfile.GNUTYPE_SPARSE),
            ("unknown", b"V"),
        ):
            info = tarfile.TarInfo(f"qlib_bin/{type_name}")
            info.type = type_value
            info.linkname = "qlib_bin/calendars/day.txt"
            unsafe.append((type_name, info, None))
        duplicate = tarfile.TarInfo("qlib_bin/calendars/day.txt")
        duplicate.size = 1
        unsafe.append(("duplicate", duplicate, b"x"))
        for label, info, data in unsafe:
            with self.subTest(label=label):
                archive, manifest = build_pair(self.root, extras=((info, data),))
                self.assert_failure(archive, manifest, "unsafe-archive-member")

    def test_validator_never_calls_any_extraction_api(self):
        archive, manifest = build_pair(self.root)
        spec = importlib.util.spec_from_file_location("validator_no_extract", VALIDATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(
            module.tarfile.TarFile,
            "extract",
            side_effect=AssertionError("extract called"),
        ), mock.patch.object(
            module.tarfile.TarFile,
            "extractall",
            side_effect=AssertionError("extractall called"),
        ):
            report = module.validate(
                archive, manifest, "2026-07-20", require_publishable=True
            )
        self.assertTrue(report["ok"])

    def test_every_handled_error_token_has_a_real_small_fixture(self):
        seen = set()

        archive, manifest = build_pair(self.root)
        manifest.write_text("{}\n", encoding="utf-8")
        self.assert_failure(archive, manifest, "invalid-manifest")
        seen.add("invalid-manifest")

        absent = self.root / "absent.tar.gz"
        self.assert_failure(absent, manifest, "invalid-manifest")
        archive, manifest = build_pair(self.root)
        self.assert_failure(absent, manifest, "io-error")
        seen.add("io-error")

        archive.write_bytes(b"broken")
        write_manifest(manifest, manifest_payload(archive))
        self.assert_failure(archive, manifest, "archive-open-failed")
        seen.add("archive-open-failed")

        info = tarfile.TarInfo("qlib_bin/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive, manifest = build_pair(self.root, extras=((info, None),))
        self.assert_failure(archive, manifest, "unsafe-archive-member")
        seen.add("unsafe-archive-member")

        members = dict(REQUIRED)
        members.pop("qlib_bin/instruments/csi300.txt")
        archive, manifest = build_pair(self.root, members)
        self.assert_failure(archive, manifest, "missing-required-member")
        seen.add("missing-required-member")

        members = dict(REQUIRED)
        members["qlib_bin/instruments/csi300.txt"] = "bad\n"
        archive, manifest = build_pair(self.root, members)
        self.assert_failure(archive, manifest, "malformed-required-member")
        seen.add("malformed-required-member")

        archive, manifest = build_pair(self.root)
        payload = manifest_payload(archive)
        payload["archive_size_bytes"] += 1
        write_manifest(manifest, payload)
        self.assert_failure(archive, manifest, "archive-identity-mismatch")
        seen.add("archive-identity-mismatch")

        members = dict(REQUIRED)
        members["qlib_bin/instruments/csi300.txt"] = (
            "SH600000\t2026-07-17\t2026-07-17\n"
        )
        archive, manifest = build_pair(self.root, members)
        self.assert_failure(archive, manifest, "target-mismatch")
        seen.add("target-mismatch")

        members = dict(REQUIRED)
        members["qlib_bin/calendars/day_future.txt"] = (
            "2026-07-17\n2026-07-20\n2026-07-22\n2026-12-31\n"
        )
        archive, manifest = build_pair(self.root, members)
        self.assert_failure(archive, manifest, "future-mismatch")
        seen.add("future-mismatch")

        members["qlib_bin/calendars/day_future.txt"] = (
            "2026-07-18\n2026-07-20\n2026-07-21\n2026-12-31\n"
        )
        archive, manifest = build_pair(self.root, members)
        self.assert_failure(archive, manifest, "prefix-mismatch")
        seen.add("prefix-mismatch")

        self.assertEqual(
            seen,
            {
                "invalid-manifest",
                "io-error",
                "archive-open-failed",
                "unsafe-archive-member",
                "missing-required-member",
                "malformed-required-member",
                "archive-identity-mismatch",
                "target-mismatch",
                "future-mismatch",
                "prefix-mismatch",
            },
        )

    def test_complete_scan_occurs_before_member_body_read(self):
        spec = importlib.util.spec_from_file_location("validator_under_test", VALIDATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class FakeArchive:
            def getmembers(self):
                members = []
                for name in module.REQUIRED_MEMBERS:
                    info = tarfile.TarInfo(name)
                    info.size = 1
                    members.append(info)
                unsafe = tarfile.TarInfo("qlib_bin/unsafe")
                unsafe.type = tarfile.SYMTYPE
                unsafe.linkname = "/etc/passwd"
                members.append(unsafe)
                return members

            def extractfile(self, _member):
                raise AssertionError("member body read before complete scan")

            def close(self):
                pass

        with mock.patch.object(module.tarfile, "open", return_value=FakeArchive()):
            with self.assertRaises(module.ValidationFailure) as raised:
                module._scan_archive("unused")
        self.assertEqual(raised.exception.token, "unsafe-archive-member")

    def test_cli_syntax_errors_exit_two_with_empty_stdout(self):
        result = subprocess.run([sys.executable, str(VALIDATOR)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "--archive", "x", "--manifest", "y", "--expected-tag", "2026-7-20"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_fixed_input_archives_and_manifests_are_byte_identical(self):
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        first_pair = build_pair(first)
        second_pair = build_pair(second)
        self.assertEqual(first_pair[0].read_bytes(), second_pair[0].read_bytes())
        self.assertEqual(first_pair[1].read_bytes(), second_pair[1].read_bytes())
        self.assertEqual(first_pair[0].read_bytes()[4:8], b"\x00\x00\x00\x00")
        with tarfile.open(first_pair[0], "r:gz") as archive:
            names = [member.name for member in archive.getmembers()]
            self.assertEqual(names, sorted(names))
            for member in archive.getmembers():
                self.assertEqual((member.uid, member.gid, member.mode, member.mtime), (0, 0, 0o644, 1784505600))


class GeneratorContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.index = load_fire_module("qlib/dump_index_weight.py", "tested_index_generator")
        self.calendar = load_fire_module(
            "tushare/dump_day_calendar.py", "tested_calendar_generator"
        )

    def tearDown(self):
        self.temp.cleanup()

    def _run_index(self, symbols, target_trade_date="2026-07-20"):
        engine = mock.Mock()
        connection = engine.raw_connection.return_value
        output = self.root / "index"
        change_dates = pd.DataFrame(
            {"change_date": pd.to_datetime(["2026-07-17"])}
        )
        constituents = pd.DataFrame({"symbol": symbols})
        with mock.patch.dict(self.index.INDEX_MAP, {"csi300": "399300.SZ"}, clear=True), mock.patch.dict(
            os.environ, {"QLIB_INDEX_DIR": str(output)}
        ), mock.patch.object(
            self.index, "create_engine", return_value=engine
        ), mock.patch.object(
            self.index.pd,
            "read_sql_query",
            side_effect=[change_dates, constituents],
        ) as read_sql:
            self.index.dump_all_to_sqlib_source(
                target_trade_date=target_trade_date
            )
        connection.close.assert_called_once_with()
        engine.dispose.assert_called_once_with()
        return output / "csi300.txt", [call.args[0] for call in read_sql.call_args_list]

    def test_all_three_fire_signatures_use_exact_optional_string_annotation(self):
        for function in (
            self.index.dump_all_to_sqlib_source,
            self.calendar.dump_calendar_to_qlib_dir,
        ):
            with self.subTest(function=function.__name__):
                parameter = inspect.signature(function).parameters["target_trade_date"]
                self.assertIsNone(parameter.default)
                self.assertEqual(parameter.annotation, Optional[str])

    def test_index_serialization_is_canonical_for_shuffled_symbols(self):
        output, queries = self._run_index(["SZ000002", "SHT00018", "SH600000"])
        self.assertEqual(
            output.read_bytes(),
            b"SH600000\t2026-07-17\t2026-07-20\n"
            b"SHT00018\t2026-07-17\t2026-07-20\n"
            b"SZ000002\t2026-07-17\t2026-07-20\n",
        )
        self.assertIn(
            "GROUP_CONCAT(stock_code ORDER BY stock_code SEPARATOR ',')", queries[0]
        )
        self.assertIn("ORDER BY stock_code", queries[1])

    def test_index_none_retains_today_bounded_diagnostic_behavior(self):
        class FixedDateTime(self.index.datetime.datetime):
            @classmethod
            def today(cls):
                return cls(2026, 7, 23)

        with mock.patch.object(self.index.datetime, "datetime", FixedDateTime):
            output, queries = self._run_index(["SH600000"], None)
        self.assertEqual(
            output.read_text(encoding="utf-8"),
            "SH600000\t2026-07-17\t2026-07-23\n",
        )
        self.assertNotIn("trade_date <=", queries[0])

    def test_index_rejects_null_empty_and_malformed_symbols_without_serializing(self):
        malformed = (
            None,
            "",
            "600000",
            "SH60000",
            "sh600000",
            "SH60000-",
            "SH600000\tbad",
            42,
            pd.NA,
        )
        for symbol in malformed:
            with self.subTest(symbol=repr(symbol)):
                output_root = self.root / "index"
                with self.assertRaises(ValueError):
                    self._run_index([symbol])
                self.assertFalse((output_root / "csi300.txt").exists())

    def test_index_rejects_duplicate_emitted_tuples(self):
        with self.assertRaisesRegex(ValueError, "Duplicate instrument rows"):
            self._run_index(["SH600000", "SH600000"])

    def test_calendar_none_retains_unbounded_future_serialization(self):
        qlib_root = self.root / "qlib"
        (qlib_root / "calendars").mkdir(parents=True)
        (qlib_root / "calendars/day.txt").write_text(
            "2026-07-17\n2026-07-20\n", encoding="utf-8"
        )
        engine = mock.Mock()
        dates = pd.DataFrame(
            {"date": pd.to_datetime(["2026-07-17", "2026-07-20", "2026-07-21"])}
        )
        with mock.patch.object(
            self.calendar, "create_engine", return_value=engine
        ), mock.patch.object(self.calendar.pd, "read_sql", return_value=dates) as read_sql:
            self.calendar.dump_calendar_to_qlib_dir(
                str(qlib_root), target_trade_date=None
            )
        self.assertEqual(
            (qlib_root / "calendars/day_future.txt").read_bytes(),
            b"2026-07-17\n2026-07-20\n2026-07-21\n",
        )
        self.assertIn("ORDER BY date", read_sql.call_args.args[0])


class StaticSafetyContractTest(unittest.TestCase):
    def test_shell_python_and_yaml_syntax(self):
        shells = [
            "dump_qlib_bin.sh",
            "upload_release.sh",
            "daily_update.sh",
            "ops/investment-data-project-monitor/collect_health.sh",
            "ops/investment-data-project-monitor/deploy.sh",
        ]
        for relative in shells:
            result = subprocess.run(["bash", "-n", str(ROOT / relative)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{relative}: {result.stderr}")
        for relative in (
            "qlib/normalize.py",
            "qlib/dump_index_weight.py",
            "qlib/validate_archive.py",
            "tushare/dump_day_calendar.py",
        ):
            compile((ROOT / relative).read_text(), relative, "exec")
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml:
            for path in (ROOT / ".github/workflows").glob("*.yml"):
                yaml.load(path.read_text(), Loader=yaml.BaseLoader)

    def test_workflow_authority_pins_and_shared_lock(self):
        upload = (ROOT / ".github/workflows/upload_release.yml").read_text()
        data = (ROOT / ".github/workflows/data_update.yml").read_text()
        image = (ROOT / ".github/workflows/docker-image.yml").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()
        concurrency_group = (
            "group: ${{ github.ref == 'refs/heads/main' && "
            "'investment-data-dolt-volume' || "
            "format('investment-data-dolt-volume-{0}', github.ref) }}"
        )
        self.assertEqual(upload.count(concurrency_group), 1)
        self.assertEqual(data.count(concurrency_group), 1)
        self.assertNotIn("CHECK_FRESHNESS", upload)
        self.assertNotIn("svenstaro/upload-release-action", upload)
        self.assertIn("permissions:\n      contents: write", upload)
        self.assertIn("GITHUB_TOKEN: ${{ secrets.GH_TOKEN }}", upload)
        self.assertIn('"chenditc/investment_data@${PUBLICATION_IMAGE_DIGEST}"', upload)
        self.assertNotIn("chenditc/investment_data:latest", upload)
        self.assertIn("sha-${{ github.sha }}", image)
        self.assertIn("INVESTMENT_DATA_REVISION=${{ github.sha }}", image)
        self.assertIn('if [[ "$GITHUB_REF" == "refs/heads/main" ]]', image)
        self.assertIn(QLIB_COMMIT, dockerfile)
        self.assertIn("org.opencontainers.image.revision", dockerfile)
        self.assertIn("/opt/investment-data/REVISION", dockerfile)
        self.assertNotIn("git pull https://github.com/chenditc/investment_data.git", dockerfile)

    def test_upload_workflow_dispatch_shape(self):
        import yaml

        document = yaml.load(
            (ROOT / ".github/workflows/upload_release.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        operation = document["on"]["workflow_dispatch"]["inputs"]["operation"]
        self.assertEqual(operation["required"], "true")
        self.assertEqual(operation["type"], "choice")
        self.assertEqual(operation["default"], "publish")
        self.assertEqual(
            operation["options"],
            ["publish", "validate", "repair-2026-07-20"],
        )

    def test_dump_and_uploader_public_grammars_fail_before_work(self):
        dump = subprocess.run(["bash", str(ROOT / "dump_qlib_bin.sh"), "a", "b", "c"], text=True, capture_output=True)
        self.assertEqual(dump.returncode, 2)
        publisher = subprocess.run(["bash", str(ROOT / "upload_release.sh"), "other"], text=True, capture_output=True)
        self.assertEqual(publisher.returncode, 2)
        self.assertEqual(publisher.stdout, "")
        collector = subprocess.run(
            ["bash", str(ROOT / "ops/investment-data-project-monitor/collect_health.sh"), "extra"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(collector.returncode, 2)
        self.assertEqual(collector.stdout, "")

    def _run_publisher_preflight_case(
        self, *, field=None, value=None, arguments=(), forbidden=None
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            side_effect_sentinel = root / "network-or-mutation-called"
            for command in ("curl", "dolt", "git"):
                executable = bin_dir / command
                executable.write_text(
                    "#!/usr/bin/env bash\n"
                    'printf called >"$SIDE_EFFECT_SENTINEL"\n'
                    "exit 99\n",
                    encoding="utf-8",
                )
                os.chmod(executable, 0o755)

            repository_revision = root / "revision"
            qlib_revision = root / "qlib-revision"
            publisher_lock = root / "publisher.lock"
            repository_revision.write_text("0" * 40 + "\n", encoding="utf-8")
            qlib_revision.write_text(QLIB_COMMIT + "\n", encoding="utf-8")
            env = {
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "SIDE_EFFECT_SENTINEL": str(side_effect_sentinel),
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REPOSITORY": "chenditc/investment_data",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_SHA": "0" * 40,
                "GITHUB_RUN_ID": "1",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_TOKEN": "unused-test-token",
                "PUBLICATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
                "PUBLICATION_BAKED_REPOSITORY_REVISION": "0" * 40,
                "PUBLICATION_BAKED_QLIB_REVISION": QLIB_COMMIT,
            }
            if field is not None:
                if value is None:
                    env.pop(field)
                else:
                    env[field] = value
            if forbidden is not None:
                env[forbidden] = ""

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        'source "$1"; shift; '
                        'REPOSITORY_REVISION_FILE=$1; QLIB_REVISION_FILE=$2; '
                        'PUBLISHER_LOCK=$3; shift 3; main "$@"'
                    ),
                    "bash",
                    str(ROOT / "upload_release.sh"),
                    str(repository_revision),
                    str(qlib_revision),
                    str(publisher_lock),
                    *arguments,
                ],
                text=True,
                capture_output=True,
                env=env,
            )
            return (
                result,
                side_effect_sentinel.exists(),
                publisher_lock.exists(),
            )

    def test_valid_publisher_grammar_rejects_bad_authority_before_network(self):
        cases = (
            ((), "GITHUB_ACTIONS", None, "publisher requires GitHub Actions authority"),
            ((), "GITHUB_ACTIONS", "false", "publisher requires GitHub Actions authority"),
            ((), "GITHUB_EVENT_NAME", "pull_request", "publish requires schedule or workflow_dispatch"),
            ((), "GITHUB_REPOSITORY", "someone/else", "unexpected GitHub repository"),
            ((), "GITHUB_REF", "refs/heads/feature", "publisher requires main branch authority"),
            ((), "GITHUB_SHA", "A" * 40, "invalid GitHub commit authority"),
            ((), "GITHUB_RUN_ID", "0", "invalid GitHub run ID"),
            ((), "GITHUB_RUN_ATTEMPT", "x", "invalid GitHub run attempt"),
            ((), "GITHUB_TOKEN", None, "GITHUB_TOKEN is required"),
            ((), "PUBLICATION_IMAGE_DIGEST", "sha256:" + "A" * 64, "invalid image digest authority"),
            ((), "PUBLICATION_BAKED_REPOSITORY_REVISION", "1" * 40, "launcher repository revision mismatch"),
            ((), "PUBLICATION_BAKED_QLIB_REVISION", "1" * 40, "launcher qlib revision mismatch"),
            (("repair-2026-07-20",), "GITHUB_EVENT_NAME", "schedule", "repair requires workflow_dispatch"),
        )
        for arguments, key, value, expected_error in cases:
            with self.subTest(arguments=arguments, key=key):
                result, side_effect_called, lock_created = self._run_publisher_preflight_case(
                    field=key, value=value, arguments=arguments
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, f"Error: {expected_error}\n")
                self.assertFalse(side_effect_called)
                self.assertFalse(lock_created)

    def test_forbidden_publisher_environment_is_rejected_before_network_even_when_empty(self):
        forbidden = (
            "QLIB_BUILD_ID",
            "QLIB_BUILD_ROOT",
            "CLEAN_QLIB_BUILD_ROOT",
            "CHECK_FRESHNESS",
            "REPO",
            "DATE",
            "UPLOAD_RELEASE_LOCK_FILE",
        )
        for variable in forbidden:
            with self.subTest(variable=variable):
                result, side_effect_called, lock_created = self._run_publisher_preflight_case(
                    forbidden=variable
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    result.stderr,
                    f"Error: {variable} is not a supported publisher input\n",
                )
                self.assertFalse(side_effect_called)
                self.assertFalse(lock_created)

    def test_publisher_lock_missing_open_and_contention_fail_before_build_or_api(self):
        for failure in ("missing-support", "open-failure", "contention"):
            with self.subTest(failure=failure):
                body = r'''
                  WORK_DIR=$1; SOURCE_FILE=$2
                  GITHUB_ACTIONS=true
                  GITHUB_EVENT_NAME=workflow_dispatch
                  GITHUB_REPOSITORY=chenditc/investment_data
                  GITHUB_REF=refs/heads/main
                  GITHUB_SHA=$(printf '0%.0s' {1..40})
                  GITHUB_RUN_ID=1
                  GITHUB_RUN_ATTEMPT=1
                  GITHUB_TOKEN=test-token
                  PUBLICATION_IMAGE_DIGEST="sha256:$(printf 'a%.0s' {1..64})"
                  PUBLICATION_BAKED_REPOSITORY_REVISION=$GITHUB_SHA
                  PUBLICATION_BAKED_QLIB_REVISION=$QLIB_COMMIT
                  REPOSITORY_REVISION_FILE="$WORK_DIR/revision"
                  QLIB_REVISION_FILE="$WORK_DIR/qlib-revision"
                  printf '%s\n' "$GITHUB_SHA" >"$REPOSITORY_REVISION_FILE"
                  printf '%s\n' "$QLIB_COMMIT" >"$QLIB_REVISION_FILE"
                  build_pair() { : >"$WORK_DIR/build-called"; }
                  github_api_get() { : >"$WORK_DIR/api-called"; }
                  case FAILURE in
                    missing-support)
                      eval "$(declare -f require_command | sed '1s/require_command/original_require_command/')"
                      require_command() { [[ "$1" != flock ]] && original_require_command "$1"; }
                      ;;
                    open-failure)
                      PUBLISHER_LOCK="$WORK_DIR/absent/lock"
                      ;;
                    contention)
                      PUBLISHER_LOCK="$WORK_DIR/publisher.lock"
                      flock() { return 1; }
                      ;;
                  esac
                  if preflight publish; then
                    build_pair
                    github_api_get
                    exit 97
                  fi
                  [[ ! -e "$WORK_DIR/build-called" && ! -e "$WORK_DIR/api-called" ]]
                '''.replace("FAILURE", failure)
                with tempfile.TemporaryDirectory() as temporary:
                    work = Path(temporary)
                    source = work / "source"
                    source.write_bytes(b"unused")
                    result = subprocess.run(
                        [
                            "bash",
                            "-c",
                            'source "$1"; shift; ' + body,
                            "bash",
                            str(ROOT / "upload_release.sh"),
                            str(work),
                            str(source),
                        ],
                        text=True,
                        capture_output=True,
                    )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_daily_update_shared_lock_controls_dolt_mutation_behaviorally(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dolt_dir = root / "dolt"
            (dolt_dir / "investment_data").mkdir(parents=True)
            script = root / "daily_update.sh"
            script.write_text(
                (ROOT / "daily_update.sh")
                .read_text(encoding="utf-8")
                .replace('DOLT_DIR="/dolt"', f'DOLT_DIR="{dolt_dir}"', 1),
                encoding="utf-8",
            )
            os.chmod(script, 0o755)

            def write_executable(directory, name, body):
                path = directory / name
                path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
                os.chmod(path, 0o755)

            def run_case(case):
                bin_dir = root / f"bin-{case}"
                bin_dir.mkdir()
                sentinel = root / f"mutation-{case}"
                if case != "missing-flock":
                    write_executable(
                        bin_dir,
                        "flock",
                        "exit 1\n" if case == "contended" else "exit 0\n",
                    )
                write_executable(
                    bin_dir,
                    "dolt",
                    (
                        'if [[ "$1" == fetch ]]; then : >"$MUTATION_SENTINEL"; fi\n'
                        'if [[ "$1" == status ]]; then '
                        "printf '%s\\n' 'nothing to commit, working tree clean'; fi\n"
                        'if [[ "$1" == sql && "$*" == *"MIN(index_max_date)"* ]]; then '
                        "printf '%s\\n' '20260721'; fi\n"
                        "exit 0\n"
                    ),
                )
                for name in ("python3", "killall", "sleep", "ls"):
                    write_executable(bin_dir, name, "exit 0\n")
                (bin_dir / "mkdir").symlink_to("/usr/bin/mkdir")
                (bin_dir / "tail").symlink_to("/usr/bin/tail")
                return subprocess.run(
                    ["/bin/bash", str(script)],
                    text=True,
                    capture_output=True,
                    env={
                        "PATH": str(bin_dir),
                        "MUTATION_SENTINEL": str(sentinel),
                    },
                ), sentinel.exists()

            acquired, acquired_mutation = run_case("acquired")
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            self.assertTrue(acquired_mutation)

            for case, expected_error in (
                ("missing-flock", "flock is required"),
                ("contended", "shared Dolt checkout is locked"),
            ):
                with self.subTest(case=case):
                    result, mutation_called = run_case(case)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected_error, result.stderr)
                    self.assertFalse(mutation_called)

    def _run_stubbed_standalone_dump(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        working = root / "working"
        investment_data = working / "investment_data"
        shared_dolt = working / "dolt/investment_data"
        qlib_checkout = working / "qlib"
        caller = root / "caller"
        supplied_repository = root / "supplied-qlib.git"
        bin_dir = root / "bin"
        for path in (
            investment_data / ".git",
            shared_dolt / ".dolt",
            qlib_checkout / ".git",
            caller,
            supplied_repository,
            bin_dir,
        ):
            path.mkdir(parents=True)
        wrong_origin = "https://example.invalid/wrong-origin.git"
        (qlib_checkout / ".git/origin-url").write_text(
            wrong_origin + "\n", encoding="utf-8"
        )
        git_log = root / "git.log"

        def write_executable(name, body):
            path = bin_dir / name
            path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
            os.chmod(path, 0o755)

        write_executable(
            "git",
            r'''printf '%s\n' "$*" >>"$QLIB_GIT_LOG"
if [[ "$1" == -C && "$3" == fetch ]]; then
  [[ "$2" == "$QLIB_CHECKOUT" ]]
  [[ "$4" == "$EXPECTED_QLIB_REPOSITORY" ]]
  [[ "$5" == "$EXPECTED_QLIB_COMMIT" ]]
  exit
fi
if [[ "$1" == -C && "$3" == rev-parse ]]; then
  if [[ "$2" == "$QLIB_CHECKOUT" ]]; then
    printf '%s\n' "$EXPECTED_QLIB_COMMIT"
  else
    printf '0%.0s' {1..40}; printf '\n'
  fi
  exit
fi
exit 0
''',
        )
        write_executable(
            "dolt",
            r'''case "$1" in
  status) printf '%s\n' 'nothing to commit, working tree clean' ;;
  sql) printf '%s\n' value '9vtplc2tar9ver7p6s1bus2oiedjvtqo' ;;
  sql-server) trap 'exit 0' TERM; while :; do /bin/sleep 1; done ;;
esac
exit 0
''',
        )
        write_executable("flock", "exit 0\n")
        write_executable(
            "python3",
            r'''if [[ -n "${DOLT_QUERY:-}" ]]; then
  case "$DOLT_QUERY" in
    *"SELECT 1"*) printf '%s\n' 1 ;;
    *"DOLT_HASHOF"*) printf '%s\n' '9vtplc2tar9ver7p6s1bus2oiedjvtqo' ;;
    *"MIN(date)"*) printf '%s\n' '2026-07-22' ;;
    *"MAX(tradedate)"*) printf '%s\n' '2026-07-21' ;;
    *"date <="*) printf '%s\n' '2026-07-21' ;;
    *"MAX(date)"*) printf '%s\n' '2026-12-31' ;;
  esac
  exit 0
fi
if [[ -n "${MANIFEST_PATH:-}" ]]; then
  printf '%s\n' '{"stub":true}' >"$MANIFEST_PATH"
  exit 0
fi
arguments="$*"
if [[ "$arguments" == *dump_bin.py* || "$arguments" == *dump_bin_sequential.py* ]]; then
  while (($#)); do
    if [[ "$1" == --qlib_dir || "$1" == --qlib-dir ]]; then
      shift
      qlib_dir=$1
      break
    fi
    shift
  done
  mkdir -p "$qlib_dir/calendars" "$qlib_dir/instruments"
  printf '%s\n' '2026-07-21' >"$qlib_dir/calendars/day.txt"
  printf '%s\n' $'SH600000\t2026-07-21\t2026-07-21' >"$qlib_dir/instruments/all.txt"
elif [[ "$arguments" == *dump_index_weight.py* ]]; then
  mkdir -p "$QLIB_INDEX_DIR"
  for name in csi300 csi500 csi800 csi1000 csiall; do
    printf '%s\n' $'SH600000\t2026-07-21\t2026-07-21' >"$QLIB_INDEX_DIR/$name.txt"
  done
elif [[ "$arguments" == *dump_day_calendar.py* ]]; then
  qlib_dir=$2
  mkdir -p "$qlib_dir/calendars"
  printf '%s\n' '2026-07-21' '2026-07-22' '2026-12-31' >"$qlib_dir/calendars/day_future.txt"
fi
exit 0
''',
        )

        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "QLIB_GIT_LOG": str(git_log),
            "QLIB_CHECKOUT": str(qlib_checkout),
            "EXPECTED_QLIB_REPOSITORY": str(supplied_repository),
            "EXPECTED_QLIB_COMMIT": QLIB_COMMIT,
        }
        result = subprocess.run(
            [
                "/bin/bash",
                str(ROOT / "dump_qlib_bin.sh"),
                str(working),
                str(supplied_repository),
            ],
            cwd=caller,
            text=True,
            capture_output=True,
            env=env,
        )
        return {
            "result": result,
            "git_log": git_log.read_text(encoding="utf-8") if git_log.exists() else "",
            "wrong_origin": wrong_origin,
            "supplied_repository": str(supplied_repository),
            "investment_data": investment_data,
            "caller": caller,
        }

    def test_dump_fetches_pin_from_supplied_repository_for_preexisting_checkout(self):
        evidence = self._run_stubbed_standalone_dump()
        result = evidence["result"]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"fetch {evidence['supplied_repository']} {QLIB_COMMIT}",
            evidence["git_log"],
        )
        self.assertNotIn(
            f"fetch origin {QLIB_COMMIT}",
            evidence["git_log"],
        )
        self.assertNotEqual(evidence["wrong_origin"], evidence["supplied_repository"])

    def test_dump_fallback_places_pair_in_repository_not_invocation_directory(self):
        evidence = self._run_stubbed_standalone_dump()
        result = evidence["result"]
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("qlib_bin.tar.gz", "qlib_bin.manifest.json"):
            with self.subTest(name=name):
                self.assertTrue((evidence["investment_data"] / name).is_file())
                self.assertFalse((evidence["caller"] / name).exists())

    def test_generator_bounds_order_and_fire_contract(self):
        index = (ROOT / "qlib/dump_index_weight.py").read_text()
        calendar = (ROOT / "tushare/dump_day_calendar.py").read_text()
        normalize = (ROOT / "qlib/normalize.py").read_text()
        dump = (ROOT / "dump_qlib_bin.sh").read_text()
        self.assertIn("target_trade_date: Optional[str] = None", index)
        self.assertIn("GROUP_CONCAT(stock_code ORDER BY stock_code SEPARATOR ',')", index)
        self.assertIn("ORDER BY stock_code", index)
        self.assertIn("rows.sort(key=lambda row: (row[0], row[1], row[2]))", index)
        self.assertIn("target_trade_date: Optional[str] = None", calendar)
        self.assertIn("ORDER BY date", calendar)
        self.assertIn('filename.open("w", encoding="utf-8", newline="\\n")', index)
        self.assertIn('filename.open("w", encoding="utf-8", newline="\\n")', calendar)
        self.assertNotIn("write_text(", index)
        self.assertNotIn("write_text(", calendar)
        self.assertIn("target_trade_date=None", normalize)
        self.assertIn("_DateFieldAwareNormalize", normalize)
        self.assertIn('--data-path "$QLIB_NORMALIZE_DIR"', dump)
        self.assertIn("dump_bin_sequential.py", dump)
        self.assertIn("--delete_source_after_success=true", dump)
        self.assertNotIn("dump_bin_help", dump)
        self.assertNotIn("--csv_path", dump)

    def test_hosted_workflows_bound_resources_and_use_shallow_snapshot_cache(self):
        update = (ROOT / ".github/workflows/data_update.yml").read_text()
        upload = (ROOT / ".github/workflows/upload_release.yml").read_text()
        daily = (ROOT / "daily_update.sh").read_text()
        publisher = (ROOT / "upload_release.sh").read_text()
        dump = (ROOT / "dump_qlib_bin.sh").read_text()
        for workflow in (update, upload):
            self.assertIn("runs-on: ubuntu-latest", workflow)
            self.assertIn("actions/cache/restore@v6", workflow)
            self.assertIn("actions/cache/save@v6", workflow)
            self.assertIn("investment-data-dolt-v2-", workflow)
            self.assertIn("steps.dolt-cache.outputs.cache-matched-key", workflow)
            self.assertIn("actions: read", workflow)
            self.assertIn("Resolve the newest cached Dolt snapshot", workflow)
            self.assertIn("steps.dolt-cache-key.outputs.key", workflow)
            self.assertIn("Check whether the verified snapshot is already cached", workflow)
            self.assertIn("steps.verified-cache.outputs.exists != 'true'", workflow)
            self.assertNotIn("restore-keys:", workflow)
            self.assertNotIn("investment-data-dolt-v1-", workflow)
            self.assertIn("--memory=12g", workflow)
            self.assertIn("chmod -R a+rX /dolt", workflow)
            self.assertNotIn("runs-on: investment-arc", workflow)
            self.assertNotIn("docker volume", workflow)
        for script in (daily, publisher, dump):
            self.assertIn("dolt clone --depth 1 --branch master", script)
        self.assertNotIn('cp -a "$SHARED_DOLT_CHECKOUT"', dump)
        self.assertIn('SNAPSHOT_DOLT_CHECKOUT="$SHARED_DOLT_CHECKOUT"', dump)

    def test_dolt_identities_use_machine_readable_hash_queries(self):
        dump = (ROOT / "dump_qlib_bin.sh").read_text()
        uploader = (ROOT / "upload_release.sh").read_text()
        self.assertIn("DOLT_HASHOF('origin/master')", dump)
        self.assertIn("DOLT_HASHOF('HEAD')", dump)
        self.assertIn("DOLT_HASHOF('origin/master')", uploader)
        self.assertNotIn("dolt log -n 1", dump)
        self.assertNotIn("dolt log -n 1", uploader)
        self.assertIn('pymysql.connect(', dump)
        self.assertIn('query_scalar "SELECT 1"', dump)
        self.assertIn('kill -0 "$DOLT_SQL_SERVER_PID"', dump)

    def test_invalid_generator_targets_fail_before_database_connection(self):
        fire = types.ModuleType("fire")
        fire.Fire = lambda *_args, **_kwargs: None
        with mock.patch.dict(sys.modules, {"fire": fire}):
            for relative, function in (
                ("qlib/dump_index_weight.py", "dump_all_to_sqlib_source"),
                ("tushare/dump_day_calendar.py", "dump_calendar_to_qlib_dir"),
            ):
                spec = importlib.util.spec_from_file_location(function + "_module", ROOT / relative)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with mock.patch.object(module, "create_engine") as create:
                    with self.assertRaises(ValueError):
                        kwargs = {"target_trade_date": "2026-7-20"}
                        if function == "dump_calendar_to_qlib_dir":
                            kwargs["qlib_dir"] = "unused"
                        getattr(module, function)(**kwargs)
                    create.assert_not_called()

    def test_publisher_fixed_repair_and_lock_are_literal_and_narrow(self):
        publisher = (ROOT / "upload_release.sh").read_text()
        self.assertIn('PUBLISHER_LOCK="/tmp/investment-data-release-publisher.lock"', publisher)
        self.assertIn('FIXED_DOLT_COMMIT="9vtplc2tar9ver7p6s1bus2oiedjvtqo"', publisher)
        self.assertIn("FIXED_RELEASE_ID=356733573", publisher)
        self.assertIn("FIXED_ORIGINAL_ASSET_ID=483488955", publisher)
        self.assertIn('BACKUP_NAME="qlib_bin.original-2026-07-20-483488955.tar.gz"', publisher)
        self.assertIn('RECEIPT_NAME="qlib-repair-2026-07-20.json"', publisher)
        self.assertIn("recover_exact_starter", publisher)
        self.assertIn("github_get_asset_by_id_optional", publisher)
        self.assertNotIn("UPLOAD_RELEASE_LOCK_FILE:-", publisher)
        self.assertNotIn("GITHUB_PAT", publisher)
        self.assertNotIn("GH_TOKEN", publisher)

    def test_documentation_uses_only_workflow_publication(self):
        command = "gh workflow run upload_release.yml --repo chenditc/investment_data --ref main -f operation=publish"
        for relative in (
            "README.md",
            "docs/README-ch.md",
            "docs/final_a_stock_eod_price.md",
            "docs/final_a_stock_eod_price.ch.md",
        ):
            text = (ROOT / relative).read_text()
            self.assertIn(command, text, relative)
            self.assertIn("qlib_bin.tar.gz", text, relative)
            self.assertIn("qlib_bin.manifest.json", text, relative)
            self.assertIn("validate_archive.py", text, relative)
            self.assertNotIn("GITHUB_PAT", text, relative)

        for relative in ("README.md", "docs/README-ch.md"):
            text = (ROOT / relative).read_text()
            self.assertNotIn("2023-10-08", text, relative)
            self.assertEqual(text.count("<release-tag>"), 3, relative)
        chinese = (ROOT / "docs/README-ch.md").read_text()
        self.assertIn(
            "tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=1",
            chinese,
        )

    def test_operator_documents_contain_no_standalone_patch_marker_paragraphs(self):
        for relative in (
            "README.md",
            "docs/README-ch.md",
            "docs/final_a_stock_eod_price.md",
            "docs/final_a_stock_eod_price.ch.md",
            "ops/investment-data-project-monitor/SKILL.md",
        ):
            with self.subTest(relative=relative):
                lines = (ROOT / relative).read_text(encoding="utf-8").splitlines()
                self.assertNotIn("+", (line.strip() for line in lines))

    def test_fail_safe_repository_rollback_is_ordered_and_impacts_are_bounded(self):
        documents = (
            "README.md",
            "docs/README-ch.md",
            "docs/final_a_stock_eod_price.md",
            "docs/final_a_stock_eod_price.ch.md",
            "ops/investment-data-project-monitor/SKILL.md",
        )
        disable = "gh workflow disable upload_release.yml --repo chenditc/investment_data"
        query = (
            "gh workflow view upload_release.yml --repo chenditc/investment_data "
            "--json state --jq .state"
        )
        for relative in documents:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                disable_at = text.index(disable)
                first_query = text.index(query, disable_at)
                first_disabled = text.index("disabled_manually", first_query)
                drain = text.index("data_update.yml", first_disabled)
                second_query = text.index(query, drain)
                second_disabled = text.index("disabled_manually", second_query)
                self.assertLess(
                    disable_at,
                    first_query,
                )
                self.assertLess(first_query, first_disabled)
                self.assertLess(first_disabled, drain)
                self.assertLess(drain, second_query)
                self.assertLess(second_query, second_disabled)
                for required in (
                    "latest",
                    "repair-2026-07-20",
                    "concurrency",
                    "backup",
                    "deploy.sh rollback",
                ):
                    self.assertIn(required, text)

        workflow_state = "active"
        events = []
        release_mutations = []
        monitor_mutations = []

        def disable_upload():
            nonlocal workflow_state
            events.append("disable")
            workflow_state = "disabled_manually"

        def require_disabled(label):
            events.append(label)
            self.assertEqual(workflow_state, "disabled_manually")

        def drain_shared_volume_jobs():
            events.append("drain-upload-and-data-update")

        def full_revert():
            events.append("full-revert")

        disable_upload()
        require_disabled("query-before-revert")
        drain_shared_volume_jobs()
        full_revert()
        require_disabled("query-after-revert")
        events.append("remain-disabled-until-safety-restored")
        self.assertEqual(
            events,
            [
                "disable",
                "query-before-revert",
                "drain-upload-and-data-update",
                "full-revert",
                "query-after-revert",
                "remain-disabled-until-safety-restored",
            ],
        )
        self.assertEqual(release_mutations, [])
        self.assertEqual(monitor_mutations, [])

    def test_collector_and_deployer_contracts_are_archive_aware_and_physical(self):
        collector = (ROOT / "ops/investment-data-project-monitor/collect_health.sh").read_text()
        deploy = (ROOT / "ops/investment-data-project-monitor/deploy.sh").read_text()
        guide = (ROOT / "ops/investment-data-project-monitor/SKILL.md").read_text()
        self.assertIn("release.archive_validation", guide)
        self.assertIn("archive_validation:$archive_validation", collector)
        self.assertIn("--require-publishable", collector)
        self.assertIn(".claude/skills-own/investment-data-project-monitor", deploy)
        self.assertIn(".investment-data-project-monitor.rollback-148", deploy)
        self.assertIn(COMPATIBILITY_LINK_TARGET, deploy)
        self.assertIn('verify_inventory "$backup" false', deploy)
        self.assertIn(
            'AUTHORIZED_OLD_SKILL_SHA256="0ec01463ab825502d6599d8c5f236bd56a9a0b2fdb76156fc5d997c04dbb9bbc"',
            deploy,
        )
        self.assertIn(
            'AUTHORIZED_OLD_COLLECTOR_SHA256="f2b6d97b18f2f5cf5b902c1a78f5043c1659ada6659ffb7105454dd84e3057b6"',
            deploy,
        )
        self.assertIn(
            'AUTHORIZED_NOTIFIER_SHA256="bda57d27128637c6dd7139fe8825205c957483400b063c02f48fcf209d294224"',
            deploy,
        )
        self.assertNotIn("backup_has_validator", deploy)
        self.assertNotIn(".local/share", deploy)
        self.assertNotIn("cp -p \"$source", collector)
        self.assertNotIn("notify_feishu.sh.next", deploy)


class PublisherStateSimulationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "asset.bin"
        self.source.write_bytes(b"validated immutable bytes")

    def tearDown(self):
        self.temp.cleanup()

    def run_bash(self, body):
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; shift; ' + body,
                "bash",
                str(ROOT / "upload_release.sh"),
                str(self.root),
                str(self.source),
            ],
            text=True,
            capture_output=True,
        )

    def run_stateful_publisher(
        self,
        operation,
        archive,
        manifest,
        original,
        failure_boundary="",
        failure_timing="",
    ):
        body = r'''
          source "$1"; shift
          HARNESS_ROOT=$1; FIXTURE_ARCHIVE=$2; FIXTURE_MANIFEST=$3
          ORIGINAL_FILE=$4; VALIDATOR=$5; OPERATION=$6
          FAILURE_BOUNDARY=$7; FAILURE_TIMING=$8
          STATE_FILE="$HARNESS_ROOT/state.json"
          REMOTE_DIR="$HARNESS_ROOT/remote"
          EVENT_LOG="$HARNESS_ROOT/events.log"
          NEXT_ID_FILE="$HARNESS_ROOT/next-id"
          WORK_DIR="$HARNESS_ROOT/work"
          mkdir -p "$REMOTE_DIR" "$WORK_DIR"

          GITHUB_SHA=$(printf '0%.0s' {1..40})
          PUBLICATION_IMAGE_DIGEST="sha256:$(printf 'a%.0s' {1..64})"
          ORIGIN_AUTHORITY_VERIFIED=true
          FIXED_ORIGINAL_SIZE=$(file_size "$ORIGINAL_FILE")
          FIXED_ORIGINAL_DIGEST=$(sha256_file "$ORIGINAL_FILE")

          log_event() { printf '%s\n' "$1" >>"$EVENT_LOG"; }
          date() {
            if [[ "$*" == "+%F" ]]; then
              printf '%s\n' '2026-07-20'
            else
              command date "$@"
            fi
          }
          select_normal_dolt_commit() {
            log_event select-dolt
            printf '%s\n' '9vtplc2tar9ver7p6s1bus2oiedjvtqo'
          }
          validate_pair() {
            log_event "validate:$(basename "$1"):$(basename "$2")"
            python3 "$VALIDATOR" --archive "$1" --manifest "$2" \
              --expected-tag "$3" --require-publishable >/dev/null
          }
          build_pair() {
            local tag=$1 dolt_commit=$2 output_dir=$3
            [[ "$tag" == 2026-07-20 && "$dolt_commit" == 9vtplc2tar9ver7p6s1bus2oiedjvtqo ]]
            mkdir -p "$output_dir"
            cp "$FIXTURE_ARCHIVE" "$output_dir/$ARCHIVE_NAME"
            cp "$FIXTURE_MANIFEST" "$output_dir/$MANIFEST_NAME"
            validate_pair "$output_dir/$ARCHIVE_NAME" "$output_dir/$MANIFEST_NAME" "$tag"
          }
          github_get_release_by_tag_optional() {
            log_event release:get-normal
            printf '%s\n' '{"id":42,"tag_name":"2026-07-20","draft":false,"prerelease":false}'
          }
          github_create_release() {
            log_event release:create
            return 99
          }
          github_api_get() {
            log_event release:get-fixed
            printf '%s\n' '{"id":356733573,"tag_name":"2026-07-20","draft":false,"prerelease":false}'
          }
          list_assets() { cat "$STATE_FILE"; }
          github_upload_asset() {
            local release_id=$1 name=$2 path=$3 content_type=$4 id size digest asset temporary
            [[ "$release_id" == "$RELEASE_ID" && -n "$content_type" ]]
            if [[ "$name" == "$FAILURE_BOUNDARY" && ! -e "$HARNESS_ROOT/injected" ]]; then
              : >"$HARNESS_ROOT/injected"
              if [[ "$FAILURE_TIMING" == before ]]; then
                log_event "injected-before:$name"
                exit 77
              elif [[ "$FAILURE_TIMING" == starter ]]; then
                id=$(cat "$NEXT_ID_FILE")
                printf '%s\n' "$((id + 1))" >"$NEXT_ID_FILE"
                asset=$(jq -cn --argjson id "$id" --arg name "$name" \
                  '{id:$id,name:$name,state:"starter",size:0,digest:null,created_at:"2026-07-21T12:00:00Z",updated_at:"2026-07-21T12:00:00Z"}')
                temporary="$STATE_FILE.next"
                jq --argjson asset "$asset" '. + [$asset]' "$STATE_FILE" >"$temporary"
                mv "$temporary" "$STATE_FILE"
                log_event "starter:$name:$id"
                return 77
              fi
            fi
            id=$(cat "$NEXT_ID_FILE")
            printf '%s\n' "$((id + 1))" >"$NEXT_ID_FILE"
            size=$(file_size "$path")
            digest=$(sha256_file "$path")
            asset=$(jq -cn --argjson id "$id" --arg name "$name" --argjson size "$size" \
              --arg digest "$digest" \
              '{id:$id,name:$name,state:"uploaded",size:$size,digest:$digest,created_at:"2026-07-21T12:00:00Z",updated_at:"2026-07-21T12:00:01Z"}')
            temporary="$STATE_FILE.next"
            jq --argjson asset "$asset" '. + [$asset]' "$STATE_FILE" >"$temporary"
            mv "$temporary" "$STATE_FILE"
            cp "$path" "$REMOTE_DIR/$id"
            log_event "upload:$name:$id"
            if [[ "$name" == "$FAILURE_BOUNDARY" && "$FAILURE_TIMING" == after \
                && -e "$HARNESS_ROOT/injected" && ! -e "$HARNESS_ROOT/after-exited" ]]; then
              : >"$HARNESS_ROOT/after-exited"
              exit 77
            fi
            if [[ "$name" == "$FAILURE_BOUNDARY" && "$FAILURE_TIMING" == lost \
                && -e "$HARNESS_ROOT/injected" && ! -e "$HARNESS_ROOT/lost-returned" ]]; then
              : >"$HARNESS_ROOT/lost-returned"
              return 77
            fi
            printf '%s\n' "$asset"
          }
          github_download_asset() {
            local id=$1 destination=$2 asset name
            asset=$(jq -c --argjson id "$id" '.[] | select(.id == $id)' "$STATE_FILE")
            [[ -n "$asset" ]]
            name=$(jq -r '.name' <<<"$asset")
            cp "$REMOTE_DIR/$id" "$destination"
            log_event "download:$name:$id"
          }
          github_delete_asset() {
            local id=$1 name temporary
            name=$(jq -r --argjson id "$id" '.[] | select(.id == $id) | .name' "$STATE_FILE")
            [[ -n "$name" ]]
            temporary="$STATE_FILE.next"
            jq --argjson id "$id" '[.[] | select(.id != $id)]' "$STATE_FILE" >"$temporary"
            mv "$temporary" "$STATE_FILE"
            rm -f -- "$REMOTE_DIR/$id"
            log_event "delete:$name:$id"
            if [[ "$FAILURE_BOUNDARY" == original-delete \
                && "$id" == "$FIXED_ORIGINAL_ASSET_ID" \
                && "$FAILURE_TIMING" == lost && ! -e "$HARNESS_ROOT/delete-lost" ]]; then
              : >"$HARNESS_ROOT/delete-lost"
              return 77
            fi
          }
          github_get_asset_by_id_optional() {
            local asset
            asset=$(jq -c --argjson id "$1" '[.[] | select(.id == $id)] | if length == 1 then .[0] else null end' "$STATE_FILE")
            if [[ "$asset" == null ]]; then
              return 4
            fi
            printf '%s\n' "$asset"
          }

          if [[ "$FAILURE_BOUNDARY" == original-delete \
              && ( "$FAILURE_TIMING" == before || "$FAILURE_TIMING" == after ) ]]; then
            eval "$(declare -f delete_original_after_gate \
              | sed '1s/delete_original_after_gate/original_delete_original_after_gate/')"
            delete_original_after_gate() {
              if [[ "$FAILURE_TIMING" == before && ! -e "$HARNESS_ROOT/delete-before" ]]; then
                : >"$HARNESS_ROOT/delete-before"
                log_event injected-failure-before-original-delete
                exit 77
              fi
              original_delete_original_after_gate || return 1
              if [[ "$FAILURE_TIMING" == after && ! -e "$HARNESS_ROOT/delete-after" ]]; then
                : >"$HARNESS_ROOT/delete-after"
                log_event injected-failure-after-original-delete
                exit 77
              fi
            }
          fi

          if [[ "$OPERATION" == publish ]]; then
            publish_current
          else
            repair_fixed_release
          fi
        '''
        return subprocess.run(
            [
                "bash",
                "-c",
                body,
                "bash",
                str(ROOT / "upload_release.sh"),
                str(self.root),
                str(archive),
                str(manifest),
                str(original),
                str(VALIDATOR),
                operation,
                failure_boundary,
                failure_timing,
            ],
            text=True,
            capture_output=True,
        )

    def remote_bytes(self, state, name):
        asset = next(item for item in state if item["name"] == name)
        return (self.root / "remote" / str(asset["id"])).read_bytes()

    def reset_stateful_repair(self, original):
        for name in (
            "remote",
            "work",
            "events.log",
            "state.json",
            "next-id",
            "injected",
            "after-exited",
            "lost-returned",
            "delete-before",
            "delete-after",
            "delete-lost",
        ):
            path = self.root / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
        original_asset = {
            "id": 483488955,
            "name": "qlib_bin.tar.gz",
            "state": "uploaded",
            "size": original.stat().st_size,
            "digest": "sha256:" + hashlib.sha256(original.read_bytes()).hexdigest(),
            "created_at": "2026-07-20T13:26:30Z",
            "updated_at": "2026-07-20T13:26:48Z",
        }
        (self.root / "state.json").write_text(json.dumps([original_asset]) + "\n")
        (self.root / "next-id").write_text("500000001\n")
        remote = self.root / "remote"
        remote.mkdir()
        shutil.copy2(original, remote / "483488955")

    def reset_stateful_publish(self):
        for name in (
            "remote",
            "work",
            "events.log",
            "state.json",
            "next-id",
            "injected",
            "after-exited",
            "lost-returned",
        ):
            path = self.root / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
        (self.root / "state.json").write_text("[]\n")
        (self.root / "next-id").write_text("100\n")

    def repair_boundary_names(self, archive):
        stem = (
            "qlib_bin.repair-2026-07-20-"
            + "0" * 40
            + "-"
            + "a" * 64
            + "-"
            + hashlib.sha256(archive.read_bytes()).hexdigest()
        )
        return {
            "candidate-archive": stem + ".tar.gz",
            "candidate-manifest": stem + ".manifest.json",
            "backup": "qlib_bin.original-2026-07-20-483488955.tar.gz",
            "receipt": "qlib-repair-2026-07-20.json",
            "original-delete": "original-delete",
            "canonical-archive": "qlib_bin.tar.gz",
            "canonical-manifest": "qlib_bin.manifest.json",
        }

    def assert_repair_accepted(self, archive, manifest, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((self.root / "state.json").read_text())
        names = [asset["name"] for asset in state]
        self.assertIn("qlib_bin.tar.gz", names)
        self.assertIn("qlib_bin.manifest.json", names)
        self.assertIn("qlib-repair-2026-07-20.json", names)
        self.assertEqual(self.remote_bytes(state, "qlib_bin.tar.gz"), archive.read_bytes())
        self.assertEqual(
            self.remote_bytes(state, "qlib_bin.manifest.json"), manifest.read_bytes()
        )

    def test_lost_upload_starter_is_deleted_by_exact_id_then_retried(self):
        state = self.root / "state.json"
        state.write_text("[]\n")
        (self.root / "uploads").write_text("0\n")
        body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2; RELEASE_ID=42; ORIGIN_AUTHORITY_VERIFIED=true
          STATE_FILE="$WORK_DIR/state.json"; DELETE_LOG="$WORK_DIR/delete.log"; UPLOADS="$WORK_DIR/uploads"
          list_assets() { cat "$STATE_FILE"; }
          github_upload_asset() {
            local count size digest
            count=$(($(cat "$UPLOADS") + 1)); printf '%s\n' "$count" >"$UPLOADS"
            if [[ "$count" == 1 ]]; then
              printf '%s\n' '[{"id":91,"name":"asset.bin","state":"starter","size":0,"digest":null}]' >"$STATE_FILE"
              return 1
            fi
            size=$(file_size "$SOURCE_FILE"); digest=$(sha256_file "$SOURCE_FILE")
            jq -cn --arg digest "$digest" --argjson size "$size" '[{id:92,name:"asset.bin",state:"uploaded",size:$size,digest:$digest}]' >"$STATE_FILE"
          }
          github_download_asset() { cp "$SOURCE_FILE" "$2"; }
          github_delete_asset() { printf '%s\n' "$1" >>"$DELETE_LOG"; printf '%s\n' '[]' >"$STATE_FILE"; }
          github_get_asset_by_id_optional() { return 4; }
          ensure_uploaded_asset asset.bin "$SOURCE_FILE" application/octet-stream "$WORK_DIR/downloaded" >/dev/null
          [[ "$(cat "$DELETE_LOG")" == 91 ]]
          [[ "$(cat "$UPLOADS")" == 2 ]]
          cmp -s "$SOURCE_FILE" "$WORK_DIR/downloaded"
        '''
        result = self.run_bash(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonzero_starter_after_failed_upload_is_not_deleted(self):
        (self.root / "state.json").write_text("[]\n")
        body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2; RELEASE_ID=42; ORIGIN_AUTHORITY_VERIFIED=true
          STATE_FILE="$WORK_DIR/state.json"; DELETE_LOG="$WORK_DIR/delete.log"
          list_assets() { cat "$STATE_FILE"; }
          github_upload_asset() {
            printf '%s\n' '[{"id":91,"name":"asset.bin","state":"starter","size":1,"digest":null}]' >"$STATE_FILE"
            return 1
          }
          github_delete_asset() { printf '%s\n' "$1" >>"$DELETE_LOG"; }
          github_get_asset_by_id_optional() { return 4; }
          if ensure_uploaded_asset asset.bin "$SOURCE_FILE" application/octet-stream "$WORK_DIR/downloaded" \
              >/dev/null 2>&1; then
            exit 1
          fi
          [[ ! -e "$DELETE_LOG" ]]
        '''
        result = self.run_bash(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_same_invocation_zero_byte_starter_recovery_covers_all_upload_boundaries(self):
        pair_root = self.root / "starter-pair"
        pair_root.mkdir()
        archive, manifest = build_pair(pair_root)
        original = self.root / "starter-original.tar.gz"
        original.write_bytes(b"captured original archive bytes")
        repair_names = self.repair_boundary_names(archive)

        for label, name in (
            ("normal-archive", "qlib_bin.tar.gz"),
            ("normal-manifest", "qlib_bin.manifest.json"),
        ):
            with self.subTest(boundary=label):
                self.reset_stateful_publish()
                result = self.run_stateful_publisher(
                    "publish", archive, manifest, original, name, "starter"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                events = (self.root / "events.log").read_text().splitlines()
                starter = next(value for value in events if value.startswith(f"starter:{name}:"))
                starter_id = starter.rsplit(":", 1)[1]
                self.assertIn(f"delete:{name}:{starter_id}", events)

        for label in (
            "candidate-archive",
            "candidate-manifest",
            "backup",
            "receipt",
            "canonical-archive",
            "canonical-manifest",
        ):
            with self.subTest(boundary=label):
                self.reset_stateful_repair(original)
                name = repair_names[label]
                result = self.run_stateful_publisher(
                    "repair", archive, manifest, original, name, "starter"
                )
                self.assert_repair_accepted(archive, manifest, result)
                events = (self.root / "events.log").read_text().splitlines()
                starter = next(value for value in events if value.startswith(f"starter:{name}:"))
                starter_id = starter.rsplit(":", 1)[1]
                self.assertIn(f"delete:{name}:{starter_id}", events)

    def test_repair_before_after_and_lost_response_boundaries_all_reach_accepted(self):
        pair_root = self.root / "boundary-pair"
        pair_root.mkdir()
        archive, manifest = build_pair(pair_root)
        original = self.root / "boundary-original.tar.gz"
        original.write_bytes(b"captured original archive bytes")
        names = self.repair_boundary_names(archive)
        boundaries = (
            "candidate-archive",
            "candidate-manifest",
            "backup",
            "receipt",
            "original-delete",
            "canonical-archive",
            "canonical-manifest",
        )
        observed_uploaded_prefixes = set()
        for boundary in boundaries:
            for timing in ("before", "after", "lost"):
                with self.subTest(boundary=boundary, timing=timing):
                    self.reset_stateful_repair(original)
                    interrupted = self.run_stateful_publisher(
                        "repair",
                        archive,
                        manifest,
                        original,
                        names[boundary],
                        timing,
                    )
                    if timing in ("before", "after"):
                        self.assertNotEqual(interrupted.returncode, 0, interrupted.stderr)
                        if timing == "after" and boundary in (
                            "candidate-archive",
                            "candidate-manifest",
                            "backup",
                            "receipt",
                        ):
                            observed_uploaded_prefixes.add(boundary)
                        resumed = self.run_stateful_publisher(
                            "repair", archive, manifest, original
                        )
                    else:
                        resumed = interrupted
                    self.assert_repair_accepted(archive, manifest, resumed)
        # Together with the fresh no-candidate start, these are the five exact
        # uploaded preparation prefixes authorized for unrestricted rerun.
        self.assertEqual(
            observed_uploaded_prefixes,
            {"candidate-archive", "candidate-manifest", "backup", "receipt"},
        )

    def test_preexisting_starters_at_every_repair_step_conflict_without_deletion(self):
        candidate_archive = (
            "qlib_bin.repair-2026-07-20-"
            + "0" * 40
            + "-"
            + "a" * 64
            + "-"
            + "b" * 64
            + ".tar.gz"
        )
        candidate_manifest = candidate_archive[:-7] + ".manifest.json"

        def asset(name, asset_id, state="uploaded", size=1):
            return {"id": asset_id, "name": name, "state": state, "size": size}

        original = asset("qlib_bin.tar.gz", 483488955)
        ca = asset(candidate_archive, 2)
        cm = asset(candidate_manifest, 3)
        backup = asset("qlib_bin.original-2026-07-20-483488955.tar.gz", 4)
        receipt = asset("qlib-repair-2026-07-20.json", 5)
        fixtures = (
            ([original, asset(candidate_archive, 20, "starter", 0)], True),
            ([original, ca, asset(candidate_manifest, 21, "starter", 0)], True),
            ([original, ca, cm, asset(backup["name"], 22, "starter", 0)], True),
            ([original, ca, cm, backup, asset(receipt["name"], 23, "starter", 0)], True),
            ([ca, cm, backup, receipt, asset("qlib_bin.tar.gz", 24, "starter", 0)], False),
            ([ca, cm, backup, receipt, asset("qlib_bin.tar.gz", 6), asset("qlib_bin.manifest.json", 25, "starter", 0)], False),
            ([original, asset(candidate_manifest, 26, "starter", 0)], True),
            ([original, asset(candidate_archive, 27, "starter", 1)], True),
            ([original, asset(candidate_archive, 28, "starter", 0), asset(candidate_archive, 29, "starter", 0)], True),
            ([original, asset(candidate_archive, 30, "processing", 0)], True),
        )
        (self.root / "candidate-archive-name").write_text(candidate_archive)
        (self.root / "candidate-manifest-name").write_text(candidate_manifest)
        for index, (assets, original_present) in enumerate(fixtures):
            with self.subTest(index=index):
                fixture = self.root / f"preexisting-starter-{index}.json"
                fixture.write_text(json.dumps(assets))
                previous_source = self.source
                self.source = fixture
                try:
                    body = r'''
                      WORK_DIR=$1; SOURCE_FILE=$2; DELETE_LOG="$WORK_DIR/delete.log"
                      CANDIDATE_ARCHIVE_NAME=$(cat "$WORK_DIR/candidate-archive-name")
                      CANDIDATE_MANIFEST_NAME=$(cat "$WORK_DIR/candidate-manifest-name")
                      github_delete_asset() { printf '%s\n' "$1" >>"$DELETE_LOG"; }
                      if validate_repair_prefix "$(cat "$SOURCE_FILE")" ORIGINAL_PRESENT; then exit 1; fi
                      [[ ! -e "$DELETE_LOG" ]]
                    '''.replace("ORIGINAL_PRESENT", "true" if original_present else "false")
                    result = self.run_bash(body)
                finally:
                    self.source = previous_source
                self.assertEqual(result.returncode, 0, result.stderr)

    def run_noisy_dolt_publish_case(
        self, case_name, *, clone_required, fetch_status=0
    ):
        case_root = self.root / case_name
        dolt_root = case_root / "dolt"
        case_root.mkdir()
        if not clone_required:
            (dolt_root / "investment_data/.dolt").mkdir(parents=True)
        body = r'''
          CASE_ROOT=$1; FETCH_STATUS=$2
          WORK_DIR="$CASE_ROOT/work"
          DOLT_TEST_DIR="$CASE_ROOT/dolt"
          mkdir -p "$WORK_DIR"
          eval "$(declare -f select_normal_dolt_commit | sed \
            's#local dolt_dir=/dolt checkout=/dolt/investment_data commit#local dolt_dir="$DOLT_TEST_DIR" checkout="$DOLT_TEST_DIR/investment_data" commit#')"
          date() {
            [[ "$*" == "+%F" ]] || return 90
            printf '%s\n' '2026-07-20'
          }
          flock() { return 0; }
          dolt() {
            case "$1" in
              clone)
                printf '%s\n' 'noisy clone progress on stdout'
                mkdir -p "$PWD/investment_data/.dolt"
                ;;
              status)
                printf '%s\n' 'noisy status setup on stdout'
                printf '%s\n' 'nothing to commit, working tree clean'
                ;;
              fetch)
                [[ "$2" == --silent && "$3" == origin && "$4" == master ]] \
                  || return 91
                printf '%s\n' 'noisy fetch progress on stdout'
                return "$FETCH_STATUS"
                ;;
              sql)
                printf '%s\n' value '9vtplc2tar9ver7p6s1bus2oiedjvtqo'
                ;;
              *) return 92 ;;
            esac
          }
          build_pair() {
            local tag=$1 dolt_commit=$2 output_dir=$3
            printf '%s\n' "$tag" "$dolt_commit" "$output_dir" \
              >"$CASE_ROOT/build-args"
            mkdir -p "$output_dir"
            printf '%s\n' archive >"$output_dir/$ARCHIVE_NAME"
            printf '%s\n' manifest >"$output_dir/$MANIFEST_NAME"
          }
          get_or_create_release() {
            : >"$CASE_ROOT/release-accessed"
            RELEASE_ID=42
          }
          list_assets() {
            : >"$CASE_ROOT/assets-listed"
            printf '%s\n' '[]'
          }
          ensure_uploaded_asset() {
            cp "$2" "$4"
            printf '%s\n' '{"id":1}'
          }
          redownload_named_asset() {
            cp "$WORK_DIR/build/$1" "$2"
            printf '%s\n' '{"id":1}'
          }
          validate_pair() {
            [[ -f "$1" && -f "$2" && "$3" == 2026-07-20 ]]
            : >"$CASE_ROOT/validated"
          }
          publish_current
        '''
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; shift; ' + body,
                "bash",
                str(ROOT / "upload_release.sh"),
                str(case_root),
                str(fetch_status),
            ],
            text=True,
            capture_output=True,
        )
        return result, case_root

    def assert_noisy_dolt_publish_succeeded(self, result, case_root):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "Validated release 2026-07-20 with immutable archive and manifest assets.\n",
        )
        self.assertNotIn("noisy", result.stdout)
        self.assertIn("noisy fetch progress on stdout", result.stderr)
        self.assertEqual(
            (case_root / "build-args").read_text(encoding="utf-8").splitlines(),
            [
                "2026-07-20",
                "9vtplc2tar9ver7p6s1bus2oiedjvtqo",
                str(case_root / "work/build"),
            ],
        )
        for name in ("release-accessed", "assets-listed", "validated"):
            self.assertTrue((case_root / name).is_file(), name)

    def test_publish_captures_exact_commit_when_dolt_fetch_is_noisy(self):
        result, case_root = self.run_noisy_dolt_publish_case(
            "noisy-fetch", clone_required=False
        )
        self.assert_noisy_dolt_publish_succeeded(result, case_root)
        self.assertNotIn("noisy clone progress on stdout", result.stderr)

    def test_publish_captures_exact_commit_when_initial_clone_is_noisy(self):
        result, case_root = self.run_noisy_dolt_publish_case(
            "noisy-clone", clone_required=True
        )
        self.assert_noisy_dolt_publish_succeeded(result, case_root)
        self.assertIn("noisy clone progress on stdout", result.stderr)

    def test_dolt_lock_contention_stops_before_build_delete_or_publication(self):
        body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2
          mkdir -p "$WORK_DIR/dolt/investment_data/.dolt"
          eval "$(declare -f select_normal_dolt_commit | sed \
            's#local dolt_dir=/dolt checkout=/dolt/investment_data commit#local dolt_dir="$WORK_DIR/dolt" checkout="$WORK_DIR/dolt/investment_data" commit#')"
          flock() { return 1; }
          dolt() {
            case "$1" in
              status) printf '%s\n' 'nothing to commit, working tree clean' ;;
              fetch) return 0 ;;
              sql) printf '%s\n' 'value' '9vtplc2tar9ver7p6s1bus2oiedjvtqo' ;;
            esac
          }
          build_pair() { : >"$WORK_DIR/build"; }
          github_delete_asset() { : >"$WORK_DIR/delete"; }
          github_upload_asset() { : >"$WORK_DIR/publication"; }
          if selected="$(select_normal_dolt_commit)"; then
            build_pair
            github_delete_asset
            github_upload_asset
          fi
          [[ ! -e "$WORK_DIR/build" && ! -e "$WORK_DIR/delete" && ! -e "$WORK_DIR/publication" ]]
        '''
        result = self.run_bash(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dirty_dolt_checkout_stops_before_build_delete_or_publication(self):
        body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2
          mkdir -p "$WORK_DIR/dolt/investment_data/.dolt"
          eval "$(declare -f select_normal_dolt_commit | sed \
            's#local dolt_dir=/dolt checkout=/dolt/investment_data commit#local dolt_dir="$WORK_DIR/dolt" checkout="$WORK_DIR/dolt/investment_data" commit#')"
          flock() { return 0; }
          dolt() {
            case "$1" in
              status) printf '%s\n' 'modified tables present' ;;
              fetch) return 0 ;;
              sql) printf '%s\n' 'value' '9vtplc2tar9ver7p6s1bus2oiedjvtqo' ;;
            esac
          }
          build_pair() { : >"$WORK_DIR/build"; }
          github_delete_asset() { : >"$WORK_DIR/delete"; }
          github_upload_asset() { : >"$WORK_DIR/publication"; }
          if selected="$(select_normal_dolt_commit)"; then
            build_pair
            github_delete_asset
            github_upload_asset
          fi
          [[ ! -e "$WORK_DIR/build" && ! -e "$WORK_DIR/delete" && ! -e "$WORK_DIR/publication" ]]
        '''
        result = self.run_bash(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_publish_fetch_failure_stops_before_build_or_release_access(self):
        result, case_root = self.run_noisy_dolt_publish_case(
            "fetch-failure", clone_required=False, fetch_status=23
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("noisy fetch progress on stdout", result.stderr)
        self.assertIn("failed to fetch shared Dolt origin/master", result.stderr)
        for name in (
            "build-args",
            "release-accessed",
            "assets-listed",
            "validated",
        ):
            self.assertFalse((case_root / name).exists(), name)

    def test_redownload_failure_stops_before_build_delete_or_publication(self):
        body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2; RELEASE_ID=42
          size="$(file_size "$SOURCE_FILE")"; digest="$(sha256_file "$SOURCE_FILE")"
          list_assets() {
            jq -cn --argjson size "$size" --arg digest "$digest" \
              '[{id:91,name:"asset.bin",state:"uploaded",size:$size,digest:$digest}]'
          }
          github_download_asset() { return 17; }
          build_pair() { : >"$WORK_DIR/build"; }
          github_delete_asset() { : >"$WORK_DIR/delete"; }
          github_upload_asset() { : >"$WORK_DIR/publication"; }
          if asset="$(redownload_named_asset asset.bin "$WORK_DIR/downloaded")"; then
            build_pair
            github_delete_asset
            github_upload_asset
          fi
          [[ ! -e "$WORK_DIR/build" && ! -e "$WORK_DIR/delete" && ! -e "$WORK_DIR/publication" ]]
        '''
        result = self.run_bash(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_starter_observed_before_this_invocation_is_never_deleted(self):
        state = [
            {
                "id": 91,
                "name": "asset.bin",
                "state": "starter",
                "size": 0,
                "digest": None,
            }
        ]
        (self.root / "state.json").write_text(json.dumps(state))
        body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2; RELEASE_ID=42; ORIGIN_AUTHORITY_VERIFIED=true
          STATE_FILE="$WORK_DIR/state.json"; DELETE_LOG="$WORK_DIR/delete.log"
          list_assets() { cat "$STATE_FILE"; }
          github_upload_asset() { printf upload >"$WORK_DIR/upload.log"; }
          github_download_asset() { return 99; }
          github_delete_asset() { printf '%s\n' "$1" >>"$DELETE_LOG"; }
          github_get_asset_by_id_optional() { return 4; }
          if ensure_uploaded_asset asset.bin "$SOURCE_FILE" application/octet-stream "$WORK_DIR/downloaded" \
              >/dev/null 2>&1; then
            exit 1
          fi
          [[ ! -e "$DELETE_LOG" && ! -e "$WORK_DIR/upload.log" ]]
        '''
        result = self.run_bash(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_starter_recovery_rejects_changed_release_authority(self):
        state = [
            {
                "id": 91,
                "name": "asset.bin",
                "state": "starter",
                "size": 0,
                "digest": None,
            }
        ]
        (self.root / "state.json").write_text(json.dumps(state))
        body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2; RELEASE_ID=42; ORIGIN_AUTHORITY_VERIFIED=true
          STATE_FILE="$WORK_DIR/state.json"; DELETE_LOG="$WORK_DIR/delete.log"
          list_assets() { cat "$STATE_FILE"; }
          github_delete_asset() { printf '%s\n' "$1" >>"$DELETE_LOG"; }
          github_get_asset_by_id_optional() { return 4; }
          if recover_exact_starter asset.bin 91 43 >/dev/null 2>&1; then exit 1; fi
          [[ ! -e "$DELETE_LOG" ]]
        '''
        result = self.run_bash(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_uploaded_plus_starter_conflicts_without_deletion(self):
        digest = "sha256:" + hashlib.sha256(self.source.read_bytes()).hexdigest()
        state = [
            {"id": 1, "name": "asset.bin", "state": "uploaded", "size": self.source.stat().st_size, "digest": digest},
            {"id": 2, "name": "asset.bin", "state": "starter", "size": 0, "digest": None},
        ]
        (self.root / "state.json").write_text(json.dumps(state))
        body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2; RELEASE_ID=42; ORIGIN_AUTHORITY_VERIFIED=true
          STATE_FILE="$WORK_DIR/state.json"; DELETE_LOG="$WORK_DIR/delete.log"
          list_assets() { cat "$STATE_FILE"; }
          github_upload_asset() { return 99; }
          github_download_asset() { cp "$SOURCE_FILE" "$2"; }
          github_delete_asset() { printf '%s\n' "$1" >>"$DELETE_LOG"; }
          github_get_asset_by_id_optional() { return 4; }
          if ensure_uploaded_asset asset.bin "$SOURCE_FILE" application/octet-stream "$WORK_DIR/downloaded" >/dev/null 2>&1; then
            exit 1
          fi
          [[ ! -e "$DELETE_LOG" ]]
        '''
        result = self.run_bash(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_normal_and_fixed_releases_reject_draft_and_prerelease(self):
        cases = (
            {"draft": True, "prerelease": False},
            {"draft": False, "prerelease": True},
        )
        for release_state in cases:
            with self.subTest(kind="normal", **release_state):
                release = {
                    "id": 42,
                    "tag_name": "2026-07-20",
                    **release_state,
                }
                self.source.write_text(json.dumps(release))
                body = r'''
                  WORK_DIR=$1; SOURCE_FILE=$2
                  github_get_release_by_tag_optional() { cat "$SOURCE_FILE"; }
                  github_create_release() { printf create >"$WORK_DIR/mutation"; }
                  if get_or_create_release 2026-07-20 >/dev/null 2>&1; then exit 1; fi
                  [[ ! -e "$WORK_DIR/mutation" ]]
                '''
                result = self.run_bash(body)
                self.assertEqual(result.returncode, 0, result.stderr)

            with self.subTest(kind="fixed", **release_state):
                release = {
                    "id": 356733573,
                    "tag_name": "2026-07-20",
                    **release_state,
                }
                self.source.write_text(json.dumps(release))
                body = r'''
                  WORK_DIR=$1; SOURCE_FILE=$2
                  github_api_get() { cat "$SOURCE_FILE"; }
                  if verify_fixed_release >/dev/null 2>&1; then exit 1; fi
                '''
                result = self.run_bash(body)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_stateful_normal_publication_orders_uploads_and_validation(self):
        pair_root = self.root / "pair"
        pair_root.mkdir()
        archive, manifest = build_pair(pair_root)
        original = self.root / "unused-original"
        original.write_bytes(b"unused")
        (self.root / "state.json").write_text("[]\n")
        (self.root / "next-id").write_text("100\n")

        result = self.run_stateful_publisher("publish", archive, manifest, original)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((self.root / "state.json").read_text())
        self.assertEqual([asset["name"] for asset in state], ["qlib_bin.tar.gz", "qlib_bin.manifest.json"])
        self.assertEqual(self.remote_bytes(state, "qlib_bin.tar.gz"), archive.read_bytes())
        self.assertEqual(self.remote_bytes(state, "qlib_bin.manifest.json"), manifest.read_bytes())

        events = (self.root / "events.log").read_text().splitlines()
        archive_upload = next(index for index, value in enumerate(events) if value.startswith("upload:qlib_bin.tar.gz:"))
        archive_download = next(index for index, value in enumerate(events) if value.startswith("download:qlib_bin.tar.gz:"))
        manifest_upload = next(index for index, value in enumerate(events) if value.startswith("upload:qlib_bin.manifest.json:"))
        final_validation = max(index for index, value in enumerate(events) if value.startswith("validate:"))
        local_validation = next(index for index, value in enumerate(events) if value.startswith("validate:qlib_bin.tar.gz:"))
        release_lookup = events.index("release:get-normal")
        self.assertLess(local_validation, release_lookup)
        self.assertLess(release_lookup, archive_upload)
        self.assertLess(archive_upload, archive_download)
        self.assertLess(archive_download, manifest_upload)
        self.assertGreater(final_validation, manifest_upload)

        with tempfile.TemporaryDirectory() as temporary:
            canonical_archive = Path(temporary) / "qlib_bin.tar.gz"
            canonical_manifest = Path(temporary) / "qlib_bin.manifest.json"
            canonical_archive.write_bytes(self.remote_bytes(state, "qlib_bin.tar.gz"))
            canonical_manifest.write_bytes(self.remote_bytes(state, "qlib_bin.manifest.json"))
            self.assertEqual(
                run_validator(canonical_archive, canonical_manifest, "--require-publishable").returncode,
                0,
            )

    def test_normal_publication_idempotence_archive_only_resume_and_conflicts(self):
        pair_root = self.root / "normal-states-pair"
        pair_root.mkdir()
        archive, manifest = build_pair(pair_root)
        original = self.root / "normal-unused-original"
        original.write_bytes(b"unused")

        self.reset_stateful_publish()
        first = self.run_stateful_publisher("publish", archive, manifest, original)
        self.assertEqual(first.returncode, 0, first.stderr)
        events_before = (self.root / "events.log").read_text().splitlines()
        second = self.run_stateful_publisher("publish", archive, manifest, original)
        self.assertEqual(second.returncode, 0, second.stderr)
        events_after = (self.root / "events.log").read_text().splitlines()
        self.assertEqual(
            [event for event in events_before if event.startswith(("upload:", "delete:"))],
            [event for event in events_after if event.startswith(("upload:", "delete:"))],
        )

        state = json.loads((self.root / "state.json").read_text())
        manifest_asset = next(
            item for item in state if item["name"] == "qlib_bin.manifest.json"
        )
        state = [item for item in state if item["id"] != manifest_asset["id"]]
        (self.root / "state.json").write_text(json.dumps(state) + "\n")
        (self.root / "remote" / str(manifest_asset["id"])).unlink()
        resumed = self.run_stateful_publisher("publish", archive, manifest, original)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        new_events = (self.root / "events.log").read_text().splitlines()[len(events_after) :]
        self.assertEqual(
            [event.split(":", 2)[1] for event in new_events if event.startswith("upload:")],
            ["qlib_bin.manifest.json"],
        )

        for conflict in ("manifest-only", "different-archive", "duplicate-archive", "starter"):
            with self.subTest(conflict=conflict):
                self.reset_stateful_publish()
                remote = self.root / "remote"
                remote.mkdir(exist_ok=True)
                if conflict == "manifest-only":
                    files = [(101, "qlib_bin.manifest.json", manifest, "uploaded", manifest.stat().st_size)]
                elif conflict == "different-archive":
                    other = self.root / "different-archive"
                    other.write_bytes(b"different")
                    files = [(101, "qlib_bin.tar.gz", other, "uploaded", other.stat().st_size)]
                elif conflict == "duplicate-archive":
                    files = [
                        (101, "qlib_bin.tar.gz", archive, "uploaded", archive.stat().st_size),
                        (102, "qlib_bin.tar.gz", archive, "uploaded", archive.stat().st_size),
                    ]
                else:
                    files = [(101, "qlib_bin.tar.gz", None, "starter", 0)]
                assets = []
                for asset_id, name, path, state_name, size in files:
                    digest = None
                    if path is not None:
                        shutil.copy2(path, remote / str(asset_id))
                        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                    assets.append(
                        {
                            "id": asset_id,
                            "name": name,
                            "state": state_name,
                            "size": size,
                            "digest": digest,
                            "created_at": "2026-07-21T12:00:00Z",
                            "updated_at": "2026-07-21T12:00:01Z",
                        }
                    )
                (self.root / "state.json").write_text(json.dumps(assets) + "\n")
                result = self.run_stateful_publisher(
                    "publish", archive, manifest, original
                )
                self.assertNotEqual(result.returncode, 0)
                events = (
                    (self.root / "events.log").read_text().splitlines()
                    if (self.root / "events.log").exists()
                    else []
                )
                self.assertFalse(
                    any(event.startswith(("upload:", "delete:")) for event in events),
                    events,
                )

    def test_normal_upload_lost_responses_refetch_exact_uploaded_bytes(self):
        pair_root = self.root / "normal-lost-pair"
        pair_root.mkdir()
        archive, manifest = build_pair(pair_root)
        original = self.root / "normal-lost-unused"
        original.write_bytes(b"unused")
        for name in ("qlib_bin.tar.gz", "qlib_bin.manifest.json"):
            with self.subTest(name=name):
                self.reset_stateful_publish()
                result = self.run_stateful_publisher(
                    "publish", archive, manifest, original, name, "lost"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                state = json.loads((self.root / "state.json").read_text())
                self.assertEqual(
                    [asset["name"] for asset in state],
                    ["qlib_bin.tar.gz", "qlib_bin.manifest.json"],
                )

    def test_stateful_fixed_repair_resumes_after_exact_original_deletion(self):
        pair_root = self.root / "pair"
        pair_root.mkdir()
        archive, manifest = build_pair(pair_root)
        original = self.root / "original.tar.gz"
        original.write_bytes(b"captured original archive bytes")
        original_digest = "sha256:" + hashlib.sha256(original.read_bytes()).hexdigest()
        original_asset = {
            "id": 483488955,
            "name": "qlib_bin.tar.gz",
            "state": "uploaded",
            "size": original.stat().st_size,
            "digest": original_digest,
            "created_at": "2026-07-20T13:26:30Z",
            "updated_at": "2026-07-20T13:26:48Z",
        }
        (self.root / "state.json").write_text(json.dumps([original_asset]) + "\n")
        (self.root / "next-id").write_text("500000001\n")
        remote = self.root / "remote"
        remote.mkdir()
        shutil.copy2(original, remote / "483488955")

        interrupted = self.run_stateful_publisher(
            "repair", archive, manifest, original, "original-delete", "after"
        )
        self.assertNotEqual(interrupted.returncode, 0)
        post_delete = json.loads((self.root / "state.json").read_text())
        post_delete_names = [asset["name"] for asset in post_delete]
        self.assertNotIn("qlib_bin.tar.gz", post_delete_names)
        self.assertNotIn("qlib_bin.manifest.json", post_delete_names)
        self.assertIn("qlib_bin.original-2026-07-20-483488955.tar.gz", post_delete_names)
        self.assertIn("qlib-repair-2026-07-20.json", post_delete_names)

        resumed = self.run_stateful_publisher("repair", archive, manifest, original)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        state = json.loads((self.root / "state.json").read_text())
        names = [asset["name"] for asset in state]
        candidate_archive = next(name for name in names if name.startswith("qlib_bin.repair-") and name.endswith(".tar.gz"))
        candidate_manifest = next(name for name in names if name.startswith("qlib_bin.repair-") and name.endswith(".manifest.json"))
        self.assertEqual(
            names,
            [
                candidate_archive,
                candidate_manifest,
                "qlib_bin.original-2026-07-20-483488955.tar.gz",
                "qlib-repair-2026-07-20.json",
                "qlib_bin.tar.gz",
                "qlib_bin.manifest.json",
            ],
        )
        self.assertEqual(self.remote_bytes(state, "qlib_bin.tar.gz"), archive.read_bytes())
        self.assertEqual(self.remote_bytes(state, "qlib_bin.manifest.json"), manifest.read_bytes())
        self.assertEqual(self.remote_bytes(state, candidate_archive), archive.read_bytes())
        self.assertEqual(self.remote_bytes(state, candidate_manifest), manifest.read_bytes())
        self.assertEqual(
            self.remote_bytes(state, "qlib_bin.original-2026-07-20-483488955.tar.gz"),
            original.read_bytes(),
        )

        events = (self.root / "events.log").read_text().splitlines()
        uploads = [value.split(":", 2)[1] for value in events if value.startswith("upload:")]
        self.assertEqual(
            uploads,
            [
                candidate_archive,
                candidate_manifest,
                "qlib_bin.original-2026-07-20-483488955.tar.gz",
                "qlib-repair-2026-07-20.json",
                "qlib_bin.tar.gz",
                "qlib_bin.manifest.json",
            ],
        )
        deletions = [value for value in events if value.startswith("delete:")]
        self.assertEqual(deletions, ["delete:qlib_bin.tar.gz:483488955"])
        delete_index = events.index(deletions[0])
        for required_download in (
            candidate_archive,
            candidate_manifest,
            "qlib_bin.original-2026-07-20-483488955.tar.gz",
            "qlib-repair-2026-07-20.json",
        ):
            self.assertTrue(
                any(
                    index < delete_index and value.startswith(f"download:{required_download}:")
                    for index, value in enumerate(events)
                ),
                required_download,
            )
        self.assertTrue(any(index < delete_index and value.startswith("validate:") for index, value in enumerate(events)))
        canonical_manifest_upload = max(
            index for index, value in enumerate(events) if value.startswith("upload:qlib_bin.manifest.json:")
        )
        self.assertTrue(any(index > canonical_manifest_upload and value.startswith("validate:") for index, value in enumerate(events)))

        with tempfile.TemporaryDirectory() as temporary:
            canonical_archive = Path(temporary) / "qlib_bin.tar.gz"
            canonical_manifest = Path(temporary) / "qlib_bin.manifest.json"
            canonical_archive.write_bytes(self.remote_bytes(state, "qlib_bin.tar.gz"))
            canonical_manifest.write_bytes(self.remote_bytes(state, "qlib_bin.manifest.json"))
            self.assertEqual(
                run_validator(canonical_archive, canonical_manifest, "--require-publishable").returncode,
                0,
            )

    def test_receipt_has_exact_nested_manifest_and_asset_record_shapes(self):
        archive, manifest = build_pair(self.root)
        candidate_manifest = self.root / "candidate.manifest.json"
        shutil.copy2(manifest, candidate_manifest)
        candidate_archive = self.root / "candidate.tar.gz"
        shutil.copy2(archive, candidate_archive)
        asset_template = {
            "id": 500000001,
            "name": "placeholder",
            "state": "uploaded",
            "size": archive.stat().st_size,
            "digest": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
            "created_at": "2026-07-21T12:00:00Z",
            "updated_at": "2026-07-21T12:00:01Z",
        }
        backup = dict(asset_template, name="qlib_bin.original-2026-07-20-483488955.tar.gz")
        candidate_archive_json = dict(asset_template, id=500000002, name="candidate.tar.gz")
        candidate_manifest_json = dict(
            asset_template,
            id=500000003,
            name="candidate.manifest.json",
            size=candidate_manifest.stat().st_size,
            digest="sha256:" + hashlib.sha256(candidate_manifest.read_bytes()).hexdigest(),
        )
        receipt = self.root / "receipt.json"
        body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2
          CANDIDATE_ARCHIVE_LOCAL="$WORK_DIR/candidate.tar.gz"
          CANDIDATE_MANIFEST_LOCAL="$WORK_DIR/candidate.manifest.json"
          create_receipt "$WORK_DIR/qlib_bin.manifest.json" "$WORK_DIR/receipt.json" \
            "$(cat "$WORK_DIR/backup.json")" "$(cat "$WORK_DIR/candidate-archive.json")" \
            "$(cat "$WORK_DIR/candidate-manifest.json")"
        '''
        (self.root / "backup.json").write_text(json.dumps(backup))
        (self.root / "candidate-archive.json").write_text(json.dumps(candidate_archive_json))
        (self.root / "candidate-manifest.json").write_text(json.dumps(candidate_manifest_json))
        result = self.run_bash(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(receipt.read_text())
        self.assertEqual(list(document), ["operation", "release_tag", "release_id", "authority", "manifest", "assets"])
        self.assertEqual(list(document["manifest"]), list(manifest_payload(archive)))
        self.assertEqual(
            list(document["assets"]),
            ["original", "backup", "candidate_archive", "candidate_manifest", "canonical_archive", "canonical_manifest"],
        )
        record_keys = ["asset_id", "name", "state", "size_bytes", "sha256", "created_at", "updated_at"]
        for record in document["assets"].values():
            self.assertEqual(list(record), record_keys)

    def test_six_record_receipt_validator_enforces_types_rfc3339_nulls_and_live_fields(self):
        archive, manifest = build_pair(self.root)
        original = self.root / "receipt-original.tar.gz"
        backup_file = self.root / "receipt-backup.tar.gz"
        original.write_bytes(b"captured original")
        shutil.copy2(original, backup_file)
        candidate_archive = self.root / "receipt-candidate.tar.gz"
        candidate_manifest = self.root / "receipt-candidate.manifest.json"
        shutil.copy2(archive, candidate_archive)
        shutil.copy2(manifest, candidate_manifest)
        candidate_archive_name = (
            "qlib_bin.repair-2026-07-20-"
            + "0" * 40
            + "-"
            + "a" * 64
            + "-"
            + hashlib.sha256(archive.read_bytes()).hexdigest()
            + ".tar.gz"
        )
        candidate_manifest_name = candidate_archive_name[:-7] + ".manifest.json"

        def api_asset(asset_id, name, path):
            return {
                "id": asset_id,
                "name": name,
                "state": "uploaded",
                "size": path.stat().st_size,
                "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "created_at": "2026-07-21T12:00:00Z",
                "updated_at": "2026-07-21T12:00:01Z",
            }

        live = {
            "original": api_asset(483488955, "qlib_bin.tar.gz", original),
            "backup": api_asset(
                500000001,
                "qlib_bin.original-2026-07-20-483488955.tar.gz",
                backup_file,
            ),
            "candidate_archive": api_asset(
                500000002, candidate_archive_name, candidate_archive
            ),
            "candidate_manifest": api_asset(
                500000003, candidate_manifest_name, candidate_manifest
            ),
        }
        live["original"]["created_at"] = "2026-07-20T13:26:30Z"
        live["original"]["updated_at"] = "2026-07-20T13:26:48Z"
        for name, payload in live.items():
            (self.root / f"live-{name}.json").write_text(json.dumps(payload))
        receipt = self.root / "validated-receipt.json"
        create_body = r'''
          WORK_DIR=$1; SOURCE_FILE=$2
          CANDIDATE_ARCHIVE_NAME=$(cat "$WORK_DIR/candidate-archive-name")
          CANDIDATE_MANIFEST_NAME=$(cat "$WORK_DIR/candidate-manifest-name")
          CANDIDATE_ARCHIVE_LOCAL="$WORK_DIR/receipt-candidate.tar.gz"
          CANDIDATE_MANIFEST_LOCAL="$WORK_DIR/receipt-candidate.manifest.json"
          FIXED_ORIGINAL_SIZE=$(file_size "$WORK_DIR/receipt-original.tar.gz")
          FIXED_ORIGINAL_DIGEST=$(sha256_file "$WORK_DIR/receipt-original.tar.gz")
          create_receipt "$WORK_DIR/qlib_bin.manifest.json" "$WORK_DIR/validated-receipt.json" \
            "$(cat "$WORK_DIR/live-backup.json")" \
            "$(cat "$WORK_DIR/live-candidate_archive.json")" \
            "$(cat "$WORK_DIR/live-candidate_manifest.json")"
          validate_repair_receipt pre-delete \
            "$WORK_DIR/validated-receipt.json" "$WORK_DIR/qlib_bin.manifest.json" \
            "$(cat "$WORK_DIR/live-original.json")" \
            "$(cat "$WORK_DIR/live-backup.json")" \
            "$(cat "$WORK_DIR/live-candidate_archive.json")" \
            "$(cat "$WORK_DIR/live-candidate_manifest.json")" \
            null null "$WORK_DIR/receipt-backup.tar.gz" \
            "$WORK_DIR/receipt-candidate.tar.gz" \
            "$WORK_DIR/receipt-candidate.manifest.json" - -
        '''
        (self.root / "candidate-archive-name").write_text(candidate_archive_name)
        (self.root / "candidate-manifest-name").write_text(candidate_manifest_name)
        result = self.run_bash(create_body)
        self.assertEqual(result.returncode, 0, result.stderr)
        base = json.loads(receipt.read_text())
        self.assertEqual(
            list(base["assets"]),
            [
                "original",
                "backup",
                "candidate_archive",
                "candidate_manifest",
                "canonical_archive",
                "canonical_manifest",
            ],
        )

        validate_body = create_body[create_body.index("          validate_repair_receipt") :]
        mutations = {}
        value = json.loads(json.dumps(base))
        value["assets"].pop("original")
        mutations["missing-record"] = value
        value = json.loads(json.dumps(base))
        value["assets"]["backup"]["asset_id"] = True
        mutations["boolean-id"] = value
        value = json.loads(json.dumps(base))
        value["assets"]["backup"]["created_at"] = "2026-07-21 12:00:00"
        mutations["bad-rfc3339"] = value
        value = json.loads(json.dumps(base))
        value["assets"]["backup"]["updated_at"] = None
        mutations["illegal-null"] = value
        value = json.loads(json.dumps(base))
        value["assets"]["canonical_archive"]["asset_id"] = 9
        mutations["planned-id-not-null"] = value
        value = json.loads(json.dumps(base))
        value["assets"]["candidate_archive"]["sha256"] = "sha256:" + "A" * 64
        mutations["noncanonical-hash"] = value
        value = json.loads(json.dumps(base))
        value["authority"]["image_digest"] = "sha256:" + "b" * 64
        mutations["authority-mismatch"] = value
        for label, document in mutations.items():
            with self.subTest(label=label):
                receipt.write_text(
                    json.dumps(document, separators=(",", ":")) + "\n"
                )
                result = self.run_bash(validate_body)
                self.assertNotEqual(result.returncode, 0)

        receipt.write_text(json.dumps(base, separators=(",", ":")) + "\n")
        changed_live = dict(live["backup"], updated_at="2026-07-21T12:00:02Z")
        (self.root / "live-backup.json").write_text(json.dumps(changed_live))
        result = self.run_bash(validate_body)
        self.assertNotEqual(result.returncode, 0)

    def test_metadata_bytes_receipt_and_manifest_tampering_never_reaches_original_delete(self):
        pair_root = self.root / "tamper-pair"
        pair_root.mkdir()
        archive, manifest = build_pair(pair_root)
        original = self.root / "tamper-original.tar.gz"
        original.write_bytes(b"captured original archive bytes")
        names = self.repair_boundary_names(archive)
        for tamper in ("metadata", "bytes", "receipt", "manifest"):
            with self.subTest(tamper=tamper):
                self.reset_stateful_repair(original)
                prepared = self.run_stateful_publisher(
                    "repair",
                    archive,
                    manifest,
                    original,
                    names["receipt"],
                    "after",
                )
                self.assertNotEqual(prepared.returncode, 0)
                state_path = self.root / "state.json"
                state = json.loads(state_path.read_text())

                if tamper == "metadata":
                    asset = next(
                        item
                        for item in state
                        if item["name"] == names["candidate-archive"]
                    )
                    asset["updated_at"] = "2026-07-21T12:00:09Z"
                else:
                    target_name = {
                        "bytes": names["backup"],
                        "receipt": names["receipt"],
                        "manifest": names["candidate-manifest"],
                    }[tamper]
                    asset = next(item for item in state if item["name"] == target_name)
                    remote_path = self.root / "remote" / str(asset["id"])
                    if tamper == "receipt":
                        document = json.loads(remote_path.read_text())
                        document["assets"]["backup"]["updated_at"] = (
                            "2026-07-21T12:00:09Z"
                        )
                        remote_path.write_text(
                            json.dumps(document, separators=(",", ":")) + "\n"
                        )
                    else:
                        remote_path.write_bytes(remote_path.read_bytes() + b"tamper")
                    asset["size"] = remote_path.stat().st_size
                    asset["digest"] = (
                        "sha256:" + hashlib.sha256(remote_path.read_bytes()).hexdigest()
                    )
                state_path.write_text(json.dumps(state) + "\n")
                rerun = self.run_stateful_publisher(
                    "repair", archive, manifest, original
                )
                self.assertNotEqual(rerun.returncode, 0)
                events = (self.root / "events.log").read_text().splitlines()
                self.assertNotIn("delete:qlib_bin.tar.gz:483488955", events)

    def test_only_uploaded_asset_repair_prefixes_are_recognized_on_rerun(self):
        candidate_archive = "qlib_bin.repair-2026-07-20-" + "0" * 40 + "-" + "a" * 64 + "-" + "b" * 64 + ".tar.gz"
        candidate_manifest = candidate_archive[:-7] + ".manifest.json"

        def asset(name, state="uploaded", asset_id=1):
            return {"id": asset_id, "name": name, "state": state, "size": 0 if state == "starter" else 1}

        original = asset("qlib_bin.tar.gz", asset_id=483488955)
        ca_up = asset(candidate_archive, asset_id=2)
        cm_up = asset(candidate_manifest, asset_id=3)
        backup_up = asset("qlib_bin.original-2026-07-20-483488955.tar.gz", asset_id=4)
        receipt_up = asset("qlib-repair-2026-07-20.json", asset_id=5)
        valid = [
            ([original], True),
            ([original, ca_up], True),
            ([original, ca_up, cm_up], True),
            ([original, ca_up, cm_up, backup_up], True),
            ([original, ca_up, cm_up, backup_up, receipt_up], True),
            ([ca_up, cm_up, backup_up, receipt_up], False),
            ([ca_up, cm_up, backup_up, receipt_up, asset("qlib_bin.tar.gz", "uploaded", 6)], False),
            ([ca_up, cm_up, backup_up, receipt_up, asset("qlib_bin.tar.gz", "uploaded", 6), asset("qlib_bin.manifest.json", "uploaded", 7)], False),
        ]
        for index, (assets, original_present) in enumerate(valid):
            fixture = self.root / f"prefix-{index}.json"
            fixture.write_text(json.dumps(assets))
            body = r'''
              WORK_DIR=$1; SOURCE_FILE=$2
              CANDIDATE_ARCHIVE_NAME=$(cat "$WORK_DIR/candidate-archive-name")
              CANDIDATE_MANIFEST_NAME=$(cat "$WORK_DIR/candidate-manifest-name")
              validate_repair_prefix "$(cat "$SOURCE_FILE")" ORIGINAL_PRESENT
            '''.replace("ORIGINAL_PRESENT", "true" if original_present else "false")
            (self.root / "candidate-archive-name").write_text(candidate_archive)
            (self.root / "candidate-manifest-name").write_text(candidate_manifest)
            previous_source = self.source
            self.source = fixture
            try:
                result = self.run_bash(body)
            finally:
                self.source = previous_source
            self.assertEqual(result.returncode, 0, f"prefix {index}: {result.stderr}")

    def test_externally_created_repair_states_conflict(self):
        candidate_archive = "qlib_bin.repair-2026-07-20-" + "0" * 40 + "-" + "a" * 64 + "-" + "b" * 64 + ".tar.gz"
        candidate_manifest = candidate_archive[:-7] + ".manifest.json"

        def uploaded(name, asset_id):
            return {"id": asset_id, "name": name, "state": "uploaded", "size": 1}

        original = uploaded("qlib_bin.tar.gz", 483488955)
        conflicts = [
            ([original, uploaded(candidate_manifest, 3)], True),
            ([original, uploaded("qlib_bin.original-2026-07-20-483488955.tar.gz", 4)], True),
            ([original, uploaded("qlib-repair-2026-07-20.json", 5)], True),
            ([uploaded(candidate_archive, 2)], False),
            ([uploaded(candidate_archive + ".external", 9)], True),
            ([original, uploaded(candidate_archive, 2), uploaded(candidate_archive, 8)], True),
        ]
        (self.root / "candidate-archive-name").write_text(candidate_archive)
        (self.root / "candidate-manifest-name").write_text(candidate_manifest)
        for index, (assets, original_present) in enumerate(conflicts):
            fixture = self.root / f"conflict-{index}.json"
            fixture.write_text(json.dumps(assets))
            body = r'''
              WORK_DIR=$1; SOURCE_FILE=$2
              CANDIDATE_ARCHIVE_NAME=$(cat "$WORK_DIR/candidate-archive-name")
              CANDIDATE_MANIFEST_NAME=$(cat "$WORK_DIR/candidate-manifest-name")
              validate_repair_prefix "$(cat "$SOURCE_FILE")" ORIGINAL_PRESENT
            '''.replace("ORIGINAL_PRESENT", "true" if original_present else "false")
            previous_source = self.source
            self.source = fixture
            try:
                result = self.run_bash(body)
            finally:
                self.source = previous_source
            self.assertNotEqual(result.returncode, 0, f"conflict {index} was accepted")


class CollectorFixtureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.bin = self.home / ".local/bin"
        self.bin.mkdir(parents=True)
        self.today = "2026-07-20"
        self.archive, self.manifest = build_pair(self.root)
        payload = json.loads(self.manifest.read_text())
        payload["release_tag"] = self.today
        write_manifest(self.manifest, payload)
        self.deployed_scripts = self.root / "deployed/scripts"
        self.deployed_scripts.mkdir(parents=True)
        shutil.copy2(
            ROOT / "ops/investment-data-project-monitor/collect_health.sh",
            self.deployed_scripts / "collect_health.sh",
        )
        shutil.copy2(VALIDATOR, self.deployed_scripts / "validate_archive.py")
        self._write_stubs()

    def tearDown(self):
        self.temp.cleanup()

    def _write_executable(self, name, text):
        path = self.bin / name
        path.write_text(text)
        os.chmod(path, 0o755)

    def _write_release(self, assets):
        release = {
            "tagName": self.today,
            "isDraft": False,
            "isPrerelease": False,
            "publishedAt": self.today + "T12:00:00Z",
            "url": "https://example.invalid/release",
            "assets": assets,
        }
        (self.root / "release.json").write_text(json.dumps(release))

    def _asset_view(self, name, api_url, size, state="uploaded"):
        return {"apiUrl": api_url, "id": "node", "name": name, "size": size, "state": state, "url": "https://example.invalid"}

    def _write_stubs(self):
        archive_url = "https://api.github.invalid/archive"
        manifest_url = "https://api.github.invalid/manifest"
        archive_digest = "sha256:" + hashlib.sha256(self.archive.read_bytes()).hexdigest()
        manifest_digest = "sha256:" + hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        (self.root / "archive-identity.json").write_text(
            json.dumps({"url": archive_url, "name": "qlib_bin.tar.gz", "state": "uploaded", "size": self.archive.stat().st_size, "digest": archive_digest})
        )
        (self.root / "manifest-identity.json").write_text(
            json.dumps({"url": manifest_url, "name": "qlib_bin.manifest.json", "state": "uploaded", "size": self.manifest.stat().st_size, "digest": manifest_digest})
        )
        self._write_release(
            [
                self._asset_view("qlib_bin.tar.gz", archive_url, self.archive.stat().st_size),
                self._asset_view("qlib_bin.manifest.json", manifest_url, self.manifest.stat().st_size),
            ]
        )
        self._write_executable(
            "date",
            """#!/usr/bin/env bash
case "$*" in
  +%F) printf '%s\n' '2026-07-20' ;;
  '+%Y-%m-%d %H:%M:%S %Z %z') printf '%s\n' '2026-07-20 21:30:00 CST +0800' ;;
  +%H%M) printf '%s\n' '2130' ;;
  '-u +%s') printf '%s\n' '1784554200' ;;
  *) /usr/bin/date "$@" ;;
esac
""",
        )
        self._write_executable(
            "curl",
            """#!/usr/bin/env bash
if [[ "$*" == *final_a_stock_eod_price* ]]; then
  printf '%s\n' '{"rows":[{"max_date":"2026-07-20"}]}'
else
  printf '%s\n' '{"rows":[{"expected_date":"2026-07-20"}]}'
fi
""",
        )
        self._write_executable(
            "minikube",
            """#!/usr/bin/env bash
case "$*" in
  *"status"*) printf '%s\n' '{"Host":"Running"}' ;;
  *"get nodes"*) printf '%s\n' '{"items":[{"metadata":{"name":"node"},"status":{"conditions":[{"type":"Ready","status":"True"}],"nodeInfo":{"kernelVersion":"x","containerRuntimeVersion":"x"}}}]}' ;;
  *"get pods"*) printf '%s\n' '{"items":[{"metadata":{"namespace":"arc-systems","name":"controller"},"status":{"phase":"Running","containerStatuses":[{"ready":true,"restartCount":0}],"startTime":"2026-07-20T00:00:00Z"}},{"metadata":{"namespace":"arc-systems","name":"listener"},"status":{"phase":"Running","containerStatuses":[{"ready":true,"restartCount":0}],"startTime":"2026-07-20T00:00:00Z"}}]}' ;;
  *"autoscalingrunnerset"*) printf '%s\n' '{"metadata":{"name":"investment-arc"},"spec":{"minRunners":0,"maxRunners":1},"status":{}}' ;;
  *"get pvc"*) printf '%s\n' '{"metadata":{"name":"investment-data-docker-graph"},"status":{"phase":"Bound","capacity":{"storage":"100Gi"}},"spec":{"storageClassName":"standard"}}' ;;
esac
""",
        )
        self._write_executable(
            "df",
            """#!/usr/bin/env bash
printf '%s\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'
printf '/dev/test 100 1 99 %s%% %s\n' "${FIXTURE_DISK_PERCENT:-89}" "${@: -1}"
""",
        )
        self._write_executable(
            "gh",
            """#!/usr/bin/env bash
if [[ "$1 $2" == "run list" ]]; then
  printf '%s\n' '[{"databaseId":1,"status":"completed","conclusion":"success","createdAt":"2026-07-20T00:00:00Z","updatedAt":"2026-07-20T00:01:00Z","url":"https://example.invalid/run","headBranch":"main","event":"schedule"}]'
elif [[ "$1 $2" == "release view" ]]; then
  cat "$FIXTURE_ROOT/release.json"
elif [[ "$1" == api ]]; then
  url="${@: -1}"
  if [[ "$*" == *"application/octet-stream"* ]]; then
    printf '%s\n' "$url" >>"$FIXTURE_ROOT/downloads.log"
    if [[ "$url" == *archive ]]; then cat "$FIXTURE_ARCHIVE"; else cat "$FIXTURE_MANIFEST"; fi
  elif [[ "$url" == *archive ]]; then
    cat "$FIXTURE_ROOT/archive-identity.json"
  else
    cat "$FIXTURE_ROOT/manifest-identity.json"
  fi
else
  exit 1
fi
""",
        )

    def run_collector(self, disk_percent=89):
        env = {
            **os.environ,
            "HOME": str(self.home),
            "FIXTURE_ROOT": str(self.root),
            "FIXTURE_ARCHIVE": str(self.archive),
            "FIXTURE_MANIFEST": str(self.manifest),
            "FIXTURE_DISK_PERCENT": str(disk_percent),
        }
        return subprocess.run(
            ["bash", str(self.deployed_scripts / "collect_health.sh")],
            text=True,
            capture_output=True,
            env=env,
        )

    def test_valid_pair_is_downloaded_and_validator_document_is_preserved(self):
        result = self.run_collector()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["release"]["valid"])
        self.assertTrue(report["release"]["archive_validation"]["ok"])
        self.assertEqual(report["overall_status"], "healthy")
        second = self.run_collector()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            len((self.root / "downloads.log").read_text().splitlines()), 4
        )

    def test_host_disk_boundary_is_healthy_at_89_and_degraded_at_90(self):
        healthy = self.run_collector(disk_percent=89)
        self.assertEqual(healthy.returncode, 0, healthy.stderr)
        healthy_report = json.loads(healthy.stdout)
        self.assertEqual(healthy_report["platform"]["host_disk_used_percent"], 89)
        self.assertEqual(healthy_report["overall_status"], "healthy")

        degraded = self.run_collector(disk_percent=90)
        self.assertEqual(degraded.returncode, 0, degraded.stderr)
        degraded_report = json.loads(degraded.stdout)
        self.assertEqual(degraded_report["platform"]["host_disk_used_percent"], 90)
        self.assertEqual(degraded_report["overall_status"], "degraded")

    def test_duplicate_name_is_invalid_without_validator_invocation(self):
        release = json.loads((self.root / "release.json").read_text())
        duplicate = dict(release["assets"][0], id="starter", state="starter", size=0)
        release["assets"].append(duplicate)
        (self.root / "release.json").write_text(json.dumps(release))
        result = self.run_collector()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["release"]["valid"])
        self.assertIsNone(report["release"]["archive_validation"])
        self.assertEqual(report["overall_status"], "degraded")

    def test_draft_and_prerelease_release_states_are_rejected(self):
        for field in ("isDraft", "isPrerelease"):
            with self.subTest(field=field):
                release = json.loads((self.root / "release.json").read_text())
                release[field] = True
                (self.root / "release.json").write_text(json.dumps(release))
                result = self.run_collector()
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertFalse(report["release"]["valid"])
                self.assertIsNone(report["release"]["archive_validation"])
                release[field] = False
                (self.root / "release.json").write_text(json.dumps(release))

    def test_validator_semantic_failure_is_unmodified_and_invalid(self):
        members = dict(REQUIRED)
        members["qlib_bin/instruments/csi300.txt"] = "sh600000\t2026-07-17\t2026-07-17\n"
        self.archive = build_archive(self.archive, members)
        payload = json.loads(self.manifest.read_text())
        payload["archive_size_bytes"] = self.archive.stat().st_size
        payload["archive_sha256"] = "sha256:" + hashlib.sha256(self.archive.read_bytes()).hexdigest()
        write_manifest(self.manifest, payload)
        self._write_stubs()
        result = self.run_collector()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["release"]["valid"])
        self.assertEqual(report["release"]["archive_validation"], {"ok": False, "error": "target-mismatch"})
        self.assertEqual(report["overall_status"], "degraded")

    def test_api_metadata_and_download_tampering_block_semantic_acceptance(self):
        cases = ("api-digest", "download-bytes", "wrong-state")
        for case in cases:
            with self.subTest(case=case):
                self._write_stubs()
                if case == "api-digest":
                    identity = json.loads(
                        (self.root / "archive-identity.json").read_text()
                    )
                    identity["digest"] = "sha256:" + "f" * 64
                    (self.root / "archive-identity.json").write_text(
                        json.dumps(identity)
                    )
                elif case == "download-bytes":
                    self.archive.write_bytes(self.archive.read_bytes() + b"tamper")
                else:
                    release = json.loads((self.root / "release.json").read_text())
                    release["assets"][0]["state"] = "starter"
                    (self.root / "release.json").write_text(json.dumps(release))
                result = self.run_collector()
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertFalse(report["release"]["valid"])
                self.assertIsNone(report["release"]["archive_validation"])
                self.assertEqual(report["overall_status"], "degraded")
                if case == "download-bytes":
                    self.archive = build_archive(self.archive)

    def test_malformed_manifest_validator_document_is_preserved_and_degraded(self):
        self.manifest.write_text("{}\n", encoding="utf-8")
        self._write_stubs()
        result = self.run_collector()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["release"]["archive_validation"],
            {"ok": False, "error": "invalid-manifest"},
        )
        self.assertFalse(report["release"]["valid"])
        self.assertEqual(report["overall_status"], "degraded")

    def test_fatal_collector_preflight_has_empty_stdout(self):
        (self.deployed_scripts / "validate_archive.py").unlink()
        result = self.run_collector()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("validator", result.stderr.lower())

    def test_complete_not_due_report_exits_zero(self):
        self._write_executable(
            "date",
            """#!/usr/bin/env bash
case "$*" in
  +%F) printf '%s\n' '2026-07-20' ;;
  '+%Y-%m-%d %H:%M:%S %Z %z') printf '%s\n' '2026-07-20 12:00:00 CST +0800' ;;
  +%H%M) printf '%s\n' '1200' ;;
  '-u +%s') printf '%s\n' '1784520000' ;;
  *) /usr/bin/date "$@" ;;
esac
""",
        )
        result = self.run_collector()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["overall_status"], "not_due")


class DeployRollbackSimulationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.target = self.root / "skill"
        self.merged = self.root / "merged-skill"
        self.rollback = self.root / ".rollback-148"
        (self.source / "scripts").mkdir(parents=True)
        (self.target / "scripts").mkdir(parents=True)
        (self.source / "SKILL.md").write_text("new skill\n")
        (self.source / "scripts/collect_health.sh").write_text(
            '#!/usr/bin/env bash\nprintf \'%s\\n\' \'{"release":{"archive_validation":{"ok":true}}}\'\n'
        )
        shutil.copy2(VALIDATOR, self.source / "scripts/validate_archive.py")
        (self.target / "SKILL.md").write_text("old skill\n")
        (self.target / "scripts/collect_health.sh").write_text("#!/usr/bin/env bash\nprintf 'old\\n'\n")
        (self.target / "scripts/notify_feishu.sh").write_text("#!/usr/bin/env bash\nprintf 'notify\\n'\n")
        (self.target / COMPATIBILITY_LINK_NAME).symlink_to(COMPATIBILITY_LINK_TARGET)
        self.merged.symlink_to(self.target, target_is_directory=True)
        os.chmod(self.source / "SKILL.md", 0o644)
        os.chmod(self.source / "scripts/collect_health.sh", 0o755)
        os.chmod(self.source / "scripts/validate_archive.py", 0o755)
        os.chmod(self.target / "SKILL.md", 0o644)
        os.chmod(self.target / "scripts/collect_health.sh", 0o700)
        os.chmod(self.target / "scripts/notify_feishu.sh", 0o700)
        self.old_skill_digest = hashlib.sha256(
            (self.target / "SKILL.md").read_bytes()
        ).hexdigest()
        self.old_collector_digest = hashlib.sha256(
            (self.target / "scripts/collect_health.sh").read_bytes()
        ).hexdigest()
        self.notifier_digest = hashlib.sha256((self.target / "scripts/notify_feishu.sh").read_bytes()).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def run_function(self, function, *args):
        command = [
            "bash",
            "-c",
            (
                'source "$1"; shift; '
                'AUTHORIZED_OLD_SKILL_SHA256="$TEST_OLD_SKILL_SHA256"; '
                'AUTHORIZED_OLD_COLLECTOR_SHA256="$TEST_OLD_COLLECTOR_SHA256"; '
                'AUTHORIZED_NOTIFIER_SHA256="$TEST_NOTIFIER_SHA256"; '
                'function_name=$1; shift; "$function_name" "$@"'
            ),
            "bash",
            str(ROOT / "ops/investment-data-project-monitor/deploy.sh"),
            function,
            *map(str, args),
        ]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "TEST_OLD_SKILL_SHA256": self.old_skill_digest,
                "TEST_OLD_COLLECTOR_SHA256": self.old_collector_digest,
                "TEST_NOTIFIER_SHA256": self.notifier_digest,
            },
        )

    def run_guarded_function(self, function, *args):
        command = [
            "bash",
            "-c",
            (
                'source "$1"; shift; function_name=$1; shift; '
                'AUTHORIZED_OLD_SKILL_SHA256="$TEST_OLD_SKILL_SHA256"; '
                'AUTHORIZED_OLD_COLLECTOR_SHA256="$TEST_OLD_COLLECTOR_SHA256"; '
                'AUTHORIZED_NOTIFIER_SHA256="$TEST_NOTIFIER_SHA256"; '
                'if ! "$function_name" "$@"; then exit 0; fi; exit 97'
            ),
            "bash",
            str(ROOT / "ops/investment-data-project-monitor/deploy.sh"),
            function,
            *map(str, args),
        ]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "TEST_OLD_SKILL_SHA256": self.old_skill_digest,
                "TEST_OLD_COLLECTOR_SHA256": self.old_collector_digest,
                "TEST_NOTIFIER_SHA256": self.notifier_digest,
            },
        )

    def snapshot_tree(self, root):
        snapshot = []
        if not root.exists() and not root.is_symlink():
            return snapshot
        for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode & 0o7777
            if path.is_symlink():
                snapshot.append((relative, "link", mode, os.readlink(path)))
            elif path.is_dir():
                snapshot.append((relative, "dir", mode, None))
            else:
                snapshot.append(
                    (
                        relative,
                        "file",
                        mode,
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                )
        return snapshot

    def run_interrupted_function(self, checkpoint, function, *args):
        marker = self.root / ("checkpoint-" + checkpoint)
        command = [
            "bash",
            "-c",
            r'''
              source "$1"; shift
              AUTHORIZED_OLD_SKILL_SHA256="$TEST_OLD_SKILL_SHA256"
              AUTHORIZED_OLD_COLLECTOR_SHA256="$TEST_OLD_COLLECTOR_SHA256"
              AUTHORIZED_NOTIFIER_SHA256="$TEST_NOTIFIER_SHA256"
              wanted=$1; marker=$2; function_name=$3; shift 3
              deploy_checkpoint() {
                if [[ "$1" == "$wanted" && ! -e "$marker" ]]; then
                  : >"$marker"
                  exit 99
                fi
              }
              "$function_name" "$@"
            ''',
            "bash",
            str(ROOT / "ops/investment-data-project-monitor/deploy.sh"),
            checkpoint,
            str(marker),
            function,
            *map(str, args),
        ]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "TEST_OLD_SKILL_SHA256": self.old_skill_digest,
                "TEST_OLD_COLLECTOR_SHA256": self.old_collector_digest,
                "TEST_NOTIFIER_SHA256": self.notifier_digest,
            },
        )

    def deploy(self):
        return self.run_function(
            "deploy_paths",
            self.source / "SKILL.md",
            self.source / "scripts/collect_health.sh",
            self.source / "scripts/validate_archive.py",
            self.target,
            self.merged,
            self.rollback,
        )

    def test_success_rerun_mixed_state_and_idempotent_rollback(self):
        old_skill = (self.target / "SKILL.md").read_bytes()
        old_collector = (self.target / "scripts/collect_health.sh").read_bytes()
        old_notifier = (self.target / "scripts/notify_feishu.sh").read_bytes()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / "scripts/validate_archive.py").exists())
        self.assertEqual((self.target / "SKILL.md").read_text(), "new skill\n")
        self.assertEqual(hashlib.sha256((self.target / "scripts/notify_feishu.sh").read_bytes()).hexdigest(), self.notifier_digest)
        self.assertTrue(self.rollback.is_dir())
        self.assertFalse((self.rollback / "scripts/validate_archive.py").exists())
        self.assertFalse(Path(str(self.rollback) + ".next").exists())
        self.assertEqual((self.rollback / "SKILL.md").read_bytes(), old_skill)
        self.assertEqual(
            (self.rollback / "scripts/collect_health.sh").read_bytes(), old_collector
        )
        self.assertEqual(
            (self.rollback / "scripts/notify_feishu.sh").read_bytes(), old_notifier
        )
        self.assertEqual(os.stat(self.rollback / "SKILL.md").st_mode & 0o777, 0o644)
        self.assertEqual(
            os.stat(self.rollback / "scripts/collect_health.sh").st_mode & 0o777,
            0o700,
        )
        self.assertEqual(
            os.stat(self.rollback / "scripts/notify_feishu.sh").st_mode & 0o777,
            0o700,
        )
        self.assertEqual(os.readlink(self.target / COMPATIBILITY_LINK_NAME), COMPATIBILITY_LINK_TARGET)
        self.assertEqual(os.readlink(self.rollback / COMPATIBILITY_LINK_NAME), COMPATIBILITY_LINK_TARGET)

        shutil.copy2(self.rollback / "SKILL.md", self.target / "SKILL.md")
        shutil.copy2(self.rollback / "scripts/collect_health.sh", self.target / "scripts/collect_health.sh")
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / "SKILL.md").read_text(), "new skill\n")

        for _ in range(2):
            result = self.run_function("rollback_paths", self.target, self.merged, self.rollback)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.target / "SKILL.md").read_text(), "old skill\n")
            self.assertFalse((self.target / "scripts/validate_archive.py").exists())
            self.assertEqual(hashlib.sha256((self.target / "scripts/notify_feishu.sh").read_bytes()).hexdigest(), self.notifier_digest)
            self.assertEqual(os.readlink(self.target / COMPATIBILITY_LINK_NAME), COMPATIBILITY_LINK_TARGET)

    def test_rollback_inventory_rejects_an_unexpected_validator(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        shutil.copy2(
            self.source / "scripts/validate_archive.py",
            self.rollback / "scripts/validate_archive.py",
        )
        target_skill = (self.target / "SKILL.md").read_bytes()
        target_validator = (self.target / "scripts/validate_archive.py").read_bytes()

        result = self.run_guarded_function(
            "restore_from_rollback", self.target, self.merged, self.rollback
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), target_skill)
        self.assertEqual(
            (self.target / "scripts/validate_archive.py").read_bytes(),
            target_validator,
        )

    def test_existing_rollback_with_byte_different_old_file_is_rejected(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.rollback / "SKILL.md").write_text("different but structurally valid\n")
        os.chmod(self.rollback / "SKILL.md", 0o644)
        before = self.snapshot_tree(self.target)
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot_tree(self.target), before)

    def test_preflight_failures_do_not_mutate_target_or_create_rollback(self):
        cases = ("notifier-hash", "unexpected-inventory", "bad-source-shell", "bad-stage-type")
        for case in cases:
            with self.subTest(case=case):
                case_root = self.root / case
                source = case_root / "source"
                target = case_root / "skill"
                merged = case_root / "merged"
                rollback = case_root / ".rollback"
                shutil.copytree(self.source, source)
                shutil.copytree(self.target, target, symlinks=True)
                merged.symlink_to(target, target_is_directory=True)
                if case == "notifier-hash":
                    (target / "scripts/notify_feishu.sh").write_text("tampered notifier\n")
                    os.chmod(target / "scripts/notify_feishu.sh", 0o700)
                elif case == "unexpected-inventory":
                    (target / "unexpected").write_text("unexpected\n")
                elif case == "bad-source-shell":
                    (source / "scripts/collect_health.sh").write_text("if broken\n")
                    os.chmod(source / "scripts/collect_health.sh", 0o755)
                else:
                    (target / "scripts/collect_health.sh.next").mkdir()
                before = self.snapshot_tree(target)
                result = self.run_function(
                    "deploy_paths",
                    source / "SKILL.md",
                    source / "scripts/collect_health.sh",
                    source / "scripts/validate_archive.py",
                    target,
                    merged,
                    rollback,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.snapshot_tree(target), before)
                self.assertFalse(rollback.exists())
                self.assertFalse(Path(str(rollback) + ".next").exists())

    def test_every_deploy_checkpoint_reruns_to_verified_success(self):
        checkpoints = (
            "rollback-copy-created",
            "rollback-copy-verified",
            "rollback-copy-promoted",
            "install-validator-staged",
            "install-validator-verified",
            "install-validator-promoted",
            "install-collector-staged",
            "install-collector-verified",
            "install-collector-promoted",
            "install-skill-staged",
            "install-skill-verified",
            "install-skill-promoted",
            "acceptance-physical-verified",
            "acceptance-merged-verified",
            "acceptance-validator-fixtures",
            "acceptance-collector-report",
        )
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint):
                case_root = self.root / checkpoint
                target = case_root / "skill"
                merged = case_root / "merged"
                rollback = case_root / ".rollback"
                shutil.copytree(self.target, target, symlinks=True)
                merged.symlink_to(target, target_is_directory=True)
                interrupted = self.run_interrupted_function(
                    checkpoint,
                    "deploy_paths",
                    self.source / "SKILL.md",
                    self.source / "scripts/collect_health.sh",
                    self.source / "scripts/validate_archive.py",
                    target,
                    merged,
                    rollback,
                )
                self.assertEqual(interrupted.returncode, 99, interrupted.stderr)
                resumed = self.run_function(
                    "deploy_paths",
                    self.source / "SKILL.md",
                    self.source / "scripts/collect_health.sh",
                    self.source / "scripts/validate_archive.py",
                    target,
                    merged,
                    rollback,
                )
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                self.assertEqual(
                    hashlib.sha256((target / "SKILL.md").read_bytes()).hexdigest(),
                    hashlib.sha256((self.source / "SKILL.md").read_bytes()).hexdigest(),
                )
                self.assertTrue((target / "scripts/validate_archive.py").is_file())

    def test_every_restore_checkpoint_reruns_to_exact_prevalidator_tree(self):
        checkpoints = (
            "restore-collector-staged",
            "restore-collector-verified",
            "restore-collector-promoted",
            "restore-skill-staged",
            "restore-skill-verified",
            "restore-skill-promoted",
            "restore-validator-parked",
            "restore-old-views-reverified",
            "restore-validator-removed",
        )
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint):
                case_root = self.root / ("restore-" + checkpoint)
                target = case_root / "skill"
                merged = case_root / "merged"
                rollback = case_root / ".rollback"
                shutil.copytree(self.target, target, symlinks=True)
                merged.symlink_to(target, target_is_directory=True)
                deployed = self.run_function(
                    "deploy_paths",
                    self.source / "SKILL.md",
                    self.source / "scripts/collect_health.sh",
                    self.source / "scripts/validate_archive.py",
                    target,
                    merged,
                    rollback,
                )
                self.assertEqual(deployed.returncode, 0, deployed.stderr)
                interrupted = self.run_interrupted_function(
                    checkpoint,
                    "restore_from_rollback",
                    target,
                    merged,
                    rollback,
                )
                self.assertEqual(interrupted.returncode, 99, interrupted.stderr)
                resumed = self.run_function(
                    "rollback_paths", target, merged, rollback
                )
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                self.assertEqual(
                    hashlib.sha256((target / "SKILL.md").read_bytes()).hexdigest(),
                    self.old_skill_digest,
                )
                self.assertEqual(
                    hashlib.sha256(
                        (target / "scripts/collect_health.sh").read_bytes()
                    ).hexdigest(),
                    self.old_collector_digest,
                )
                self.assertFalse((target / "scripts/validate_archive.py").exists())

    def test_acceptance_failure_restores_exact_old_inventory(self):
        (self.source / "scripts/collect_health.sh").write_text(
            '#!/usr/bin/env bash\nprintf \'%s\\n\' \'{"release":{"archive_validation":{"ok":false}}}\'\n'
        )
        os.chmod(self.source / "scripts/collect_health.sh", 0o755)
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.target / "SKILL.md").read_text(), "old skill\n")
        self.assertEqual((self.target / "scripts/collect_health.sh").read_text(), "#!/usr/bin/env bash\nprintf 'old\\n'\n")
        self.assertFalse((self.target / "scripts/validate_archive.py").exists())
        self.assertEqual(hashlib.sha256((self.target / "scripts/notify_feishu.sh").read_bytes()).hexdigest(), self.notifier_digest)

    def test_actual_collector_healthy_fixture_passes_physical_and_merged_acceptance(self):
        fixture = CollectorFixtureTest("test_valid_pair_is_downloaded_and_validator_document_is_preserved")
        fixture.setUp()
        try:
            shutil.copy2(
                ROOT / "ops/investment-data-project-monitor/collect_health.sh",
                self.source / "scripts/collect_health.sh",
            )
            os.chmod(self.source / "scripts/collect_health.sh", 0o755)
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(fixture.home),
                    "FIXTURE_ROOT": str(fixture.root),
                    "FIXTURE_ARCHIVE": str(fixture.archive),
                    "FIXTURE_MANIFEST": str(fixture.manifest),
                },
            ):
                result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                hashlib.sha256(
                    (self.target / "scripts/collect_health.sh").read_bytes()
                ).hexdigest(),
                hashlib.sha256(
                    (self.source / "scripts/collect_health.sh").read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(
                hashlib.sha256(
                    (self.merged / "scripts/validate_archive.py").read_bytes()
                ).hexdigest(),
                hashlib.sha256(VALIDATOR.read_bytes()).hexdigest(),
            )
        finally:
            fixture.tearDown()

    def test_actual_collector_degraded_fixture_restores_prevalidator_tree(self):
        fixture = CollectorFixtureTest("test_validator_semantic_failure_is_unmodified_and_invalid")
        fixture.setUp()
        try:
            members = dict(REQUIRED)
            members["qlib_bin/instruments/csi300.txt"] = (
                "sh600000\t2026-07-17\t2026-07-17\n"
            )
            fixture.archive = build_archive(fixture.archive, members)
            payload = json.loads(fixture.manifest.read_text())
            payload["archive_size_bytes"] = fixture.archive.stat().st_size
            payload["archive_sha256"] = (
                "sha256:" + hashlib.sha256(fixture.archive.read_bytes()).hexdigest()
            )
            write_manifest(fixture.manifest, payload)
            fixture._write_stubs()
            shutil.copy2(
                ROOT / "ops/investment-data-project-monitor/collect_health.sh",
                self.source / "scripts/collect_health.sh",
            )
            os.chmod(self.source / "scripts/collect_health.sh", 0o755)
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(fixture.home),
                    "FIXTURE_ROOT": str(fixture.root),
                    "FIXTURE_ARCHIVE": str(fixture.archive),
                    "FIXTURE_MANIFEST": str(fixture.manifest),
                },
            ):
                result = self.deploy()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                hashlib.sha256((self.target / "SKILL.md").read_bytes()).hexdigest(),
                self.old_skill_digest,
            )
            self.assertEqual(
                hashlib.sha256(
                    (self.target / "scripts/collect_health.sh").read_bytes()
                ).hexdigest(),
                self.old_collector_digest,
            )
            self.assertFalse((self.target / "scripts/validate_archive.py").exists())
        finally:
            fixture.tearDown()

    def test_rerun_finishes_exact_parked_validator_restore_transition(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)

        shutil.copy2(self.rollback / "SKILL.md", self.target / "SKILL.md")
        shutil.copy2(
            self.rollback / "scripts/collect_health.sh",
            self.target / "scripts/collect_health.sh",
        )
        validator = self.target / "scripts/validate_archive.py"
        parked = self.target / "scripts/validate_archive.py.next"
        validator.rename(parked)

        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(parked.exists())
        self.assertTrue(validator.exists())
        self.assertEqual((self.target / "SKILL.md").read_text(), "new skill\n")

    def test_inventory_accepts_only_the_exact_compatibility_link(self):
        good = self.run_function("verify_inventory", self.target, "false")
        self.assertEqual(good.returncode, 0, good.stderr)

        for case in ("absent", "wrong-type", "wrong-target"):
            with self.subTest(case=case):
                case_root = self.root / f"compatibility-{case}"
                source = case_root / "source"
                target = case_root / "skill"
                merged = case_root / "merged"
                rollback = case_root / ".rollback"
                shutil.copytree(self.source, source)
                shutil.copytree(self.target, target, symlinks=True)
                merged.symlink_to(target, target_is_directory=True)
                link = target / COMPATIBILITY_LINK_NAME
                link.unlink()
                if case == "wrong-type":
                    link.write_text(COMPATIBILITY_LINK_TARGET + "\n", encoding="utf-8")
                elif case == "wrong-target":
                    link.symlink_to("/tmp/not-the-merged-view")
                before = self.snapshot_tree(target)
                result = self.run_function(
                    "deploy_paths",
                    source / "SKILL.md",
                    source / "scripts/collect_health.sh",
                    source / "scripts/validate_archive.py",
                    target,
                    merged,
                    rollback,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.snapshot_tree(target), before)
                self.assertFalse(rollback.exists())
                self.assertFalse(Path(str(rollback) + ".next").exists())

        unexpected = self.root / "unexpected-skill"
        shutil.copytree(self.target, unexpected, symlinks=True)
        (unexpected / "unexpected.txt").write_text("unexpected\n")
        result = self.run_guarded_function("verify_inventory", unexpected, "false")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_install_failure_is_not_masked_beneath_negated_conditional(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        destination = self.target / "scripts/collect_health.sh"
        destination.write_text("#!/usr/bin/env bash\nprintf 'third identity\\n'\n")
        before = destination.read_bytes()
        result = self.run_guarded_function(
            "install_one",
            self.source / "scripts/collect_health.sh",
            destination,
            self.rollback / "scripts/collect_health.sh",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(destination.read_bytes(), before)

    def test_merged_view_failure_is_not_masked_by_later_acceptance_checks(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        stale_merged = self.root / "stale-merged"
        shutil.copytree(self.target, stale_merged, symlinks=True)
        (stale_merged / "SKILL.md").write_text("stale merged instructions\n")
        os.chmod(stale_merged / "SKILL.md", 0o644)
        result = self.run_guarded_function(
            "accept_deployment",
            self.source / "SKILL.md",
            self.source / "scripts/collect_health.sh",
            self.source / "scripts/validate_archive.py",
            self.target,
            stale_merged,
            self.rollback,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_restore_precheck_failure_is_not_masked_or_installed(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        target_before = (self.target / "SKILL.md").read_bytes()
        os.chmod(self.rollback / "SKILL.md", 0o600)
        result = self.run_guarded_function(
            "restore_from_rollback", self.target, self.merged, self.rollback
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), target_before)


@unittest.skipUnless(
    os.environ.get("QLIB_REAL_BUILD_A") and os.environ.get("QLIB_REAL_BUILD_B"),
    "two independent real production build directories were not supplied",
)
class RealProductionBuildEvidenceTest(unittest.TestCase):
    def test_independent_fixed_snapshot_builds_are_byte_and_member_identical(self):
        roots = [
            Path(os.environ["QLIB_REAL_BUILD_A"]),
            Path(os.environ["QLIB_REAL_BUILD_B"]),
        ]
        archives = [root / "qlib_bin.tar.gz" for root in roots]
        manifests = [root / "qlib_bin.manifest.json" for root in roots]

        validator_results = []
        for archive, manifest in zip(archives, manifests):
            result = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--archive",
                    str(archive),
                    "--manifest",
                    str(manifest),
                    "--expected-tag",
                    "2026-07-20",
                    "--require-publishable",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            validator_result = json.loads(result.stdout)
            self.assertEqual(
                result.stdout,
                json.dumps(validator_result, separators=(",", ":")) + "\n",
            )
            validator_results.append(validator_result)

        self.assertEqual(archives[0].read_bytes(), archives[1].read_bytes())
        self.assertEqual(manifests[0].read_bytes(), manifests[1].read_bytes())

        payload = json.loads(manifests[0].read_bytes())
        self.assertEqual(
            payload,
            {
                "release_tag": "2026-07-20",
                "target_trade_date": "2026-07-20",
                "future_start_date": "2026-07-21",
                "future_end_date": "2026-12-31",
                "dolt_commit": os.environ["QLIB_REAL_DOLT_COMMIT"],
                "investment_data_commit": os.environ["QLIB_REAL_REPOSITORY_COMMIT"],
                "qlib_commit": QLIB_COMMIT,
                "image_digest": os.environ["QLIB_REAL_IMAGE_DIGEST"],
                "archive_size_bytes": archives[0].stat().st_size,
                "archive_sha256": "sha256:"
                + hashlib.sha256(archives[0].read_bytes()).hexdigest(),
            },
        )
        self.assertEqual(
            manifests[0].read_bytes(),
            (json.dumps(payload, separators=(",", ":")) + "\n").encode(),
        )
        expected_validator_result = {
            "ok": True,
            "result": {
                "archive_sha256": payload["archive_sha256"],
                "manifest_sha256": "sha256:"
                + hashlib.sha256(manifests[0].read_bytes()).hexdigest(),
                "archive_size_bytes": payload["archive_size_bytes"],
                "target_trade_date": payload["target_trade_date"],
                "future_start_date": payload["future_start_date"],
                "future_end_date": payload["future_end_date"],
            },
        }
        self.assertEqual(validator_results, [expected_validator_result] * 2)

        def ordered_members(path):
            result = []
            with tarfile.open(path, "r:gz") as stream:
                for member in stream:
                    body_hash = None
                    if member.isfile():
                        body = stream.extractfile(member)
                        self.assertIsNotNone(body)
                        body_hash = hashlib.sha256(body.read()).hexdigest()
                    result.append(
                        (
                            member.name,
                            member.type,
                            member.mode,
                            member.uid,
                            member.gid,
                            member.mtime,
                            member.size,
                            body_hash,
                        )
                    )
            return result

        first = ordered_members(archives[0])
        second = ordered_members(archives[1])
        self.assertTrue(first)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
