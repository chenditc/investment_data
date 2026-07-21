#!/usr/bin/env python3
"""Validate the provenance and date semantics of a qlib release archive."""

import argparse
import datetime
import hashlib
import json
import os
import posixpath
import re
import sys
import tarfile
from pathlib import PurePosixPath


QLIB_COMMIT = "b87a2c294d364a33fb739359886acffe8ec907d1"
MANIFEST_KEYS = (
    "release_tag",
    "target_trade_date",
    "future_start_date",
    "future_end_date",
    "dolt_commit",
    "investment_data_commit",
    "qlib_commit",
    "image_digest",
    "archive_size_bytes",
    "archive_sha256",
)
REQUIRED_MEMBERS = (
    "qlib_bin/calendars/day.txt",
    "qlib_bin/calendars/day_future.txt",
    "qlib_bin/instruments/all.txt",
    "qlib_bin/instruments/csi300.txt",
    "qlib_bin/instruments/csi500.txt",
    "qlib_bin/instruments/csi800.txt",
    "qlib_bin/instruments/csi1000.txt",
    "qlib_bin/instruments/csiall.txt",
)
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
GIT_RE = re.compile(r"[0-9a-f]{40}\Z")
DOLT_RE = re.compile(r"[0-9a-v]{32}\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ValidationFailure(Exception):
    def __init__(self, token):
        super().__init__(token)
        self.token = token


def _fail(token):
    raise ValidationFailure(token)


def _canonical_date(value):
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%d") == value


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_manifest(path, expected_tag, require_publishable):
    try:
        with open(path, "rb") as stream:
            raw = stream.read()
    except OSError:
        _fail("io-error")
    try:
        text = raw.decode("utf-8")
        pairs = json.loads(text, object_pairs_hook=lambda values: values)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("invalid-manifest")
    if not isinstance(pairs, list) or any(
        not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs
    ):
        _fail("invalid-manifest")
    if tuple(key for key, _ in pairs) != MANIFEST_KEYS:
        _fail("invalid-manifest")
    manifest = dict(pairs)
    required_string_fields = MANIFEST_KEYS[:7] + ("archive_sha256",)
    if any(not isinstance(manifest[key], str) for key in required_string_fields):
        _fail("invalid-manifest")
    if manifest["image_digest"] is not None and not isinstance(manifest["image_digest"], str):
        _fail("invalid-manifest")
    if any(not _canonical_date(manifest[key]) for key in MANIFEST_KEYS[:4]):
        _fail("invalid-manifest")
    if manifest["release_tag"] != expected_tag:
        _fail("invalid-manifest")
    if DOLT_RE.fullmatch(manifest["dolt_commit"]) is None:
        _fail("invalid-manifest")
    if GIT_RE.fullmatch(manifest["investment_data_commit"]) is None:
        _fail("invalid-manifest")
    if manifest["qlib_commit"] != QLIB_COMMIT:
        _fail("invalid-manifest")
    image_digest = manifest["image_digest"]
    if image_digest is not None and DIGEST_RE.fullmatch(image_digest) is None:
        _fail("invalid-manifest")
    if require_publishable and image_digest is None:
        _fail("invalid-manifest")
    size = manifest["archive_size_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        _fail("invalid-manifest")
    if DIGEST_RE.fullmatch(manifest["archive_sha256"]) is None:
        _fail("invalid-manifest")
    return manifest, "sha256:" + hashlib.sha256(raw).hexdigest()


def _safe_member_name(member):
    name = member.name
    if not name or "\\" in name or name.startswith("/"):
        return None
    trimmed = name[:-1] if name.endswith("/") else name
    if not trimmed or posixpath.normpath(trimmed) != trimmed:
        return None
    parts = PurePosixPath(trimmed).parts
    if any(part in ("", ".", "..") for part in parts):
        return None
    if trimmed != "qlib_bin" and not trimmed.startswith("qlib_bin/"):
        return None
    return trimmed


def _scan_archive(path):
    try:
        archive = tarfile.open(path, mode="r:gz")
    except (OSError, EOFError, tarfile.TarError):
        _fail("archive-open-failed")
    try:
        try:
            members = archive.getmembers()
        except (OSError, EOFError, tarfile.TarError):
            _fail("archive-open-failed")
        safe = {}
        for member in members:
            name = _safe_member_name(member)
            if name is None or name in safe:
                _fail("unsafe-archive-member")
            if not (member.isdir() or member.isfile()):
                _fail("unsafe-archive-member")
            if getattr(member, "sparse", None):
                _fail("unsafe-archive-member")
            safe[name] = member

        required = {}
        for name in REQUIRED_MEMBERS:
            member = safe.get(name)
            if member is None or not member.isfile():
                _fail("missing-required-member")
            try:
                stream = archive.extractfile(member)
                if stream is None:
                    _fail("missing-required-member")
                required[name] = stream.read()
            except (OSError, EOFError, tarfile.TarError):
                _fail("archive-open-failed")
        return required
    finally:
        archive.close()


def _text_lines(raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail("malformed-required-member")
    if not text or "\r" in text or not text.endswith("\n"):
        _fail("malformed-required-member")
    lines = text[:-1].split("\n")
    if not lines or any(not line for line in lines):
        _fail("malformed-required-member")
    return lines


def _calendar(raw):
    lines = _text_lines(raw)
    if any(not _canonical_date(value) for value in lines):
        _fail("malformed-required-member")
    if lines != sorted(set(lines)):
        _fail("malformed-required-member")
    return lines


def _instruments(raw):
    lines = _text_lines(raw)
    rows = []
    for line in lines:
        fields = line.split("\t")
        if len(fields) != 3:
            _fail("malformed-required-member")
        symbol, start, end = fields
        if not symbol or not _canonical_date(start) or not _canonical_date(end) or start > end:
            _fail("malformed-required-member")
        rows.append((symbol, start, end))
    if rows != sorted(set(rows), key=lambda row: (row[0], row[1], row[2])):
        _fail("malformed-required-member")
    return rows


def validate(archive_path, manifest_path, expected_tag, require_publishable=False):
    manifest, manifest_digest = _load_manifest(
        manifest_path, expected_tag, require_publishable
    )
    try:
        archive_size = os.path.getsize(archive_path)
        archive_digest = _sha256(archive_path)
    except OSError:
        _fail("io-error")
    if (
        archive_size != manifest["archive_size_bytes"]
        or archive_digest != manifest["archive_sha256"]
    ):
        _fail("archive-identity-mismatch")

    members = _scan_archive(archive_path)
    day = _calendar(members[REQUIRED_MEMBERS[0]])
    day_future = _calendar(members[REQUIRED_MEMBERS[1]])
    instruments = [_instruments(members[name]) for name in REQUIRED_MEMBERS[2:]]

    target = manifest["target_trade_date"]
    if day[-1] != target or any(max(row[2] for row in rows) != target for rows in instruments):
        _fail("target-mismatch")
    if len(day_future) <= len(day):
        _fail("future-mismatch")
    if day_future[: len(day)] != day:
        _fail("prefix-mismatch")
    if (
        day_future[len(day)] != manifest["future_start_date"]
        or day_future[-1] != manifest["future_end_date"]
    ):
        _fail("future-mismatch")

    return {
        "ok": True,
        "result": {
            "archive_sha256": archive_digest,
            "manifest_sha256": manifest_digest,
            "archive_size_bytes": archive_size,
            "target_trade_date": target,
            "future_start_date": manifest["future_start_date"],
            "future_end_date": manifest["future_end_date"],
        },
    }


def _parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-tag", required=True, type=_expected_tag)
    parser.add_argument("--require-publishable", action="store_true")
    return parser.parse_args(argv)


def _expected_tag(value):
    if not _canonical_date(value):
        raise argparse.ArgumentTypeError("expected tag must be YYYY-MM-DD")
    return value


def main(argv=None):
    args = _parse_args(argv)
    try:
        result = validate(
            args.archive,
            args.manifest,
            args.expected_tag,
            args.require_publishable,
        )
    except ValidationFailure as exc:
        print(json.dumps({"ok": False, "error": exc.token}, separators=(",", ":")))
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
