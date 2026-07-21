#!/usr/bin/env bash
set -euo pipefail

PRODUCTION_TARGET="/localhome/local-dichen/.claude/skills-own/investment-data-project-monitor"
PRODUCTION_MERGED="/localhome/local-dichen/.claude/skills/investment-data-project-monitor"
PRODUCTION_ROLLBACK="/localhome/local-dichen/.claude/skills-own/.investment-data-project-monitor.rollback-148"
COMPATIBILITY_LINK_NAME="investment-data-project-monitor"
COMPATIBILITY_LINK_TARGET="/localhome/local-dichen/.claude/skills/investment-data-project-monitor"
AUTHORIZED_OLD_SKILL_SHA256="0ec01463ab825502d6599d8c5f236bd56a9a0b2fdb76156fc5d997c04dbb9bbc"
AUTHORIZED_OLD_COLLECTOR_SHA256="f2b6d97b18f2f5cf5b902c1a78f5043c1659ada6659ffb7105454dd84e3057b6"
AUTHORIZED_NOTIFIER_SHA256="bda57d27128637c6dd7139fe8825205c957483400b063c02f48fcf209d294224"

# Tests may override this function only after sourcing the script. The production
# entrypoint never provides an interruption selector.
deploy_checkpoint() {
  :
}

deploy_error() {
  printf 'Error: %s\n' "$*" >&2
  return 1
}

file_digest() {
  sha256sum "$1" | awk '{print $1}'
}

file_mode() {
  stat -c '%a' "$1"
}

same_file_identity() {
  [[ -f "$1" && ! -L "$1" && -f "$2" && ! -L "$2" \
      && "$(file_mode "$1")" == "$(file_mode "$2")" \
      && "$(file_digest "$1")" == "$(file_digest "$2")" ]]
}

fixed_inventory() {
  local root=$1
  find "$root" -mindepth 1 -printf '%P\n' | LC_ALL=C sort
}

verify_inventory() {
  local root=$1 allow_validator=$2 allow_stages=${3:-false}
  local inventory raw_inventory entry expected compatibility_link
  [[ -d "$root" && ! -L "$root" ]] \
    || { deploy_error "skill directory is not physical: $root"; return 1; }
  raw_inventory="$(fixed_inventory "$root")" || return 1
  inventory=""
  while IFS= read -r entry; do
    if [[ "$allow_stages" == true ]] && [[ "$entry" == "SKILL.md.next" \
        || "$entry" == "scripts/collect_health.sh.next" \
        || "$entry" == "scripts/validate_archive.py.next" ]]; then
      [[ -f "$root/$entry" && ! -L "$root/$entry" ]] \
        || { deploy_error "fixed staging path has an unexpected type: $root/$entry"; return 1; }
      continue
    fi
    if [[ -z "$inventory" ]]; then
      inventory=$entry
    else
      inventory+=$'\n'$entry
    fi
  done <<<"$raw_inventory"
  expected=$'SKILL.md\ninvestment-data-project-monitor\nscripts\nscripts/collect_health.sh\nscripts/notify_feishu.sh'
  if [[ "$allow_validator" == true ]]; then
    expected+=$'\nscripts/validate_archive.py'
  fi
  [[ "$inventory" == "$expected" ]] \
    || { deploy_error "unexpected inventory below $root"; return 1; }

  compatibility_link="$root/$COMPATIBILITY_LINK_NAME"
  [[ -L "$compatibility_link" ]] \
    || { deploy_error "authorized compatibility link is absent below $root"; return 1; }
  [[ "$(readlink "$compatibility_link")" == "$COMPATIBILITY_LINK_TARGET" ]] \
    || { deploy_error "compatibility link target is invalid below $root"; return 1; }

  [[ -f "$root/SKILL.md" && ! -L "$root/SKILL.md" ]] || return 1
  [[ -f "$root/scripts/collect_health.sh" && ! -L "$root/scripts/collect_health.sh" ]] \
    || return 1
  [[ -f "$root/scripts/notify_feishu.sh" && ! -L "$root/scripts/notify_feishu.sh" ]] \
    || return 1
  if [[ "$allow_validator" == true ]]; then
    [[ -f "$root/scripts/validate_archive.py" && ! -L "$root/scripts/validate_archive.py" ]] \
      || return 1
  else
    [[ ! -e "$root/scripts/validate_archive.py" && ! -L "$root/scripts/validate_archive.py" ]] \
      || return 1
  fi
}

verify_compatibility_link_matches() {
  local target=$1 backup=$2 target_link backup_link
  target_link="$target/$COMPATIBILITY_LINK_NAME"
  backup_link="$backup/$COMPATIBILITY_LINK_NAME"
  [[ -L "$backup_link" \
      && "$(readlink "$backup_link")" == "$COMPATIBILITY_LINK_TARGET" \
      && -L "$target_link" \
      && "$(readlink "$target_link")" == "$COMPATIBILITY_LINK_TARGET" ]] \
    || { deploy_error "compatibility link was not preserved"; return 1; }
}

verify_source_files() {
  local source_skill=$1 source_collector=$2 source_validator=$3
  [[ -f "$source_skill" && ! -L "$source_skill" && "$(file_mode "$source_skill")" == 644 ]] \
    || { deploy_error "repository SKILL.md must be a 0644 regular file"; return 1; }
  [[ -f "$source_collector" && ! -L "$source_collector" && "$(file_mode "$source_collector")" == 755 ]] \
    || { deploy_error "repository collector must be a 0755 regular file"; return 1; }
  [[ -f "$source_validator" && ! -L "$source_validator" && "$(file_mode "$source_validator")" == 755 ]] \
    || { deploy_error "repository validator must be a 0755 regular file"; return 1; }
  bash -n "$source_collector" || return 1
  python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' "$source_validator" \
    || return 1
  validator_smoke "$source_validator" || return 1
}

verify_authorized_file() {
  local path=$1 expected_mode=$2 expected_digest=$3 label=$4
  [[ -f "$path" && ! -L "$path" ]] \
    || { deploy_error "$label is not an authorized regular file: $path"; return 1; }
  [[ "$(file_mode "$path")" == "$expected_mode" ]] \
    || { deploy_error "$label mode differs from the authorized old file: $path"; return 1; }
  [[ "$(file_digest "$path")" == "$expected_digest" ]] \
    || { deploy_error "$label hash differs from the authorized old file: $path"; return 1; }
}

validator_smoke() {
  local validator=$1 smoke_dir
  smoke_dir="$(mktemp -d /tmp/investment-data-validator-smoke.XXXXXXXX)" || return 1
  if ! python3 - "$validator" "$smoke_dir" <<'PY'
import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

validator = Path(sys.argv[1])
root = Path(sys.argv[2])
required = {
    "qlib_bin/calendars/day.txt": b"2026-07-17\n2026-07-20\n",
    "qlib_bin/calendars/day_future.txt": b"2026-07-17\n2026-07-20\n2026-07-21\n2026-12-31\n",
    "qlib_bin/instruments/all.txt": b"sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csi300.txt": b"sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csi500.txt": b"sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csi800.txt": b"sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csi1000.txt": b"sh600000\t2026-07-17\t2026-07-20\n",
    "qlib_bin/instruments/csiall.txt": b"sh600000\t2026-07-17\t2026-07-20\n",
}

def make_pair(stem, malformed=False, unsafe=False):
    archive = root / f"{stem}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in required.items():
            if malformed and name == "qlib_bin/instruments/csi300.txt":
                data = b"malformed row\n"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
        if unsafe:
            info = tarfile.TarInfo("qlib_bin/unsafe-link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = root / f"{stem}.json"
    payload = {
        "release_tag": "2026-07-20",
        "target_trade_date": "2026-07-20",
        "future_start_date": "2026-07-21",
        "future_end_date": "2026-12-31",
        "dolt_commit": "9vtplc2tar9ver7p6s1bus2oiedjvtqo",
        "investment_data_commit": "0" * 40,
        "qlib_commit": "b87a2c294d364a33fb739359886acffe8ec907d1",
        "image_digest": None,
        "archive_size_bytes": archive.stat().st_size,
        "archive_sha256": digest,
    }
    manifest.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    return archive, manifest

good = make_pair("good")
malformed = make_pair("malformed", malformed=True)
unsafe = make_pair("unsafe", unsafe=True)
good_run = subprocess.run(
    [sys.executable, str(validator), "--archive", str(good[0]), "--manifest", str(good[1]), "--expected-tag", "2026-07-20"],
    capture_output=True,
    text=True,
)
malformed_run = subprocess.run(
    [sys.executable, str(validator), "--archive", str(malformed[0]), "--manifest", str(malformed[1]), "--expected-tag", "2026-07-20"],
    capture_output=True,
    text=True,
)
unsafe_run = subprocess.run(
    [sys.executable, str(validator), "--archive", str(unsafe[0]), "--manifest", str(unsafe[1]), "--expected-tag", "2026-07-20"],
    capture_output=True,
    text=True,
)
if good_run.returncode != 0 or json.loads(good_run.stdout).get("ok") is not True:
    raise SystemExit("good validator fixture failed")
if malformed_run.returncode != 1 or json.loads(malformed_run.stdout) != {"ok": False, "error": "malformed-required-member"}:
    raise SystemExit("malformed validator fixture was not rejected")
if unsafe_run.returncode != 1 or json.loads(unsafe_run.stdout) != {"ok": False, "error": "unsafe-archive-member"}:
    raise SystemExit("unsafe validator fixture was not rejected")
PY
  then
    rm -rf -- "$smoke_dir"
    return 1
  fi
  rm -rf -- "$smoke_dir"
}

verify_backup_tree() {
  local backup=$1
  [[ -d "$backup" && ! -L "$backup" ]] \
    || { deploy_error "rollback copy is not a physical directory"; return 1; }
  verify_inventory "$backup" false || return 1
  verify_authorized_file "$backup/SKILL.md" 644 "$AUTHORIZED_OLD_SKILL_SHA256" \
    "rollback SKILL.md" || return 1
  verify_authorized_file "$backup/scripts/collect_health.sh" 700 \
    "$AUTHORIZED_OLD_COLLECTOR_SHA256" "rollback collector" || return 1
  verify_authorized_file "$backup/scripts/notify_feishu.sh" 700 \
    "$AUTHORIZED_NOTIFIER_SHA256" "rollback notifier" || return 1
  [[ ! -e "$backup/scripts/validate_archive.py" && ! -L "$backup/scripts/validate_archive.py" ]] \
    || { deploy_error "authoritative pre-validator rollback tree contains a validator"; return 1; }
}

prepare_rollback_copy() {
  local target=$1 rollback=$2 next="${rollback}.next" current_has_validator=false
  [[ -f "$target/scripts/validate_archive.py" ]] && current_has_validator=true
  verify_inventory "$target" "$current_has_validator" true || return 1

  if [[ -e "$rollback" ]]; then
    [[ ! -e "$next" ]] \
      || { deploy_error "rollback copy and rollback staging path both exist"; return 1; }
    verify_backup_tree "$rollback" || return 1
    verify_compatibility_link_matches "$target" "$rollback" || return 1
    return
  fi
  [[ "$current_has_validator" == false ]] \
    || { deploy_error "initial rollback source unexpectedly contains a validator"; return 1; }
  verify_authorized_file "$target/SKILL.md" 644 "$AUTHORIZED_OLD_SKILL_SHA256" \
    "installed old SKILL.md" || return 1
  verify_authorized_file "$target/scripts/collect_health.sh" 700 \
    "$AUTHORIZED_OLD_COLLECTOR_SHA256" "installed old collector" || return 1
  verify_authorized_file "$target/scripts/notify_feishu.sh" 700 \
    "$AUTHORIZED_NOTIFIER_SHA256" "installed notifier" || return 1
  if [[ -e "$next" ]]; then
    [[ -d "$next" && ! -L "$next" ]] \
      || { deploy_error "rollback staging path has unexpected type"; return 1; }
    diff -qr "$target" "$next" >/dev/null \
      || { deploy_error "existing rollback staging copy does not match the physical skill"; return 1; }
  else
    cp -a "$target" "$next" || return 1
    deploy_checkpoint rollback-copy-created || return 1
  fi
  diff -qr "$target" "$next" >/dev/null \
    || { deploy_error "rollback copy verification failed"; return 1; }
  verify_backup_tree "$next" || return 1
  verify_compatibility_link_matches "$target" "$next" || return 1
  deploy_checkpoint rollback-copy-verified || return 1
  mv "$next" "$rollback" || return 1
  deploy_checkpoint rollback-copy-promoted || return 1
  verify_backup_tree "$rollback" || return 1
  verify_compatibility_link_matches "$target" "$rollback" || return 1
}

classify_destination() {
  local destination=$1 source=$2 backup_path=$3
  if [[ -L "$destination" || ( -e "$destination" && ! -f "$destination" ) ]]; then
    deploy_error "destination has unexpected type: $destination"
    return 1
  fi
  if [[ -f "$destination" ]] && same_file_identity "$destination" "$source"; then
    printf 'new\n'
  elif [[ -f "$backup_path" ]] && [[ -f "$destination" ]] \
      && same_file_identity "$destination" "$backup_path"; then
    printf 'old\n'
  elif [[ ! -e "$backup_path" && ! -e "$destination" ]]; then
    printf 'old\n'
  else
    deploy_error "destination is neither verified old nor verified new: $destination"
  fi
}

install_one() {
  local source=$1 destination=$2 backup_path=$3 label=${4:-file} next state
  next="${destination}.next"
  state="$(classify_destination "$destination" "$source" "$backup_path")" || return 1
  if [[ "$state" == new ]]; then
    if [[ -e "$next" || -L "$next" ]]; then
      [[ -f "$next" && ! -L "$next" ]] \
        || { deploy_error "staging path has unexpected type: $next"; return 1; }
      rm -f -- "$next" || return 1
    fi
    return
  fi
  if [[ -e "$next" ]] && ! same_file_identity "$next" "$source"; then
    [[ -f "$next" && ! -L "$next" ]] \
      || { deploy_error "staging path has unexpected type: $next"; return 1; }
    # Old/new destination and rollback identities were verified above.
    rm -f -- "$next" || return 1
  fi
  if [[ ! -e "$next" ]]; then
    cp -p "$source" "$next" || return 1
    deploy_checkpoint "$label-staged" || return 1
  fi
  same_file_identity "$next" "$source" \
    || { deploy_error "staged file verification failed: $next"; return 1; }
  if [[ "$source" == *.sh ]]; then
    bash -n "$next" || return 1
  elif [[ "$source" == *.py ]]; then
    python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' "$next" \
      || return 1
  fi
  deploy_checkpoint "$label-verified" || return 1
  mv "$next" "$destination" || return 1
  deploy_checkpoint "$label-promoted" || return 1
  same_file_identity "$destination" "$source" \
    || { deploy_error "installed file verification failed"; return 1; }
}

verify_notifier() {
  local target=$1 rollback=$2
  verify_authorized_file "$target/scripts/notify_feishu.sh" 700 \
    "$AUTHORIZED_NOTIFIER_SHA256" "installed notifier" || return 1
  verify_authorized_file "$rollback/scripts/notify_feishu.sh" 700 \
    "$AUTHORIZED_NOTIFIER_SHA256" "rollback notifier" || return 1
  same_file_identity "$target/scripts/notify_feishu.sh" "$rollback/scripts/notify_feishu.sh" \
    || { deploy_error "notification helper identity changed"; return 1; }
}

verify_view() {
  local source_skill=$1 source_collector=$2 source_validator=$3 view=$4
  same_file_identity "$source_skill" "$view/SKILL.md" || return 1
  same_file_identity "$source_collector" "$view/scripts/collect_health.sh" || return 1
  same_file_identity "$source_validator" "$view/scripts/validate_archive.py" || return 1
}

finish_parked_validator_restore() {
  local source_validator=$1 target=$2 merged=$3 rollback=$4
  local parked="$target/scripts/validate_archive.py.next"
  [[ -e "$parked" ]] || return 0

  [[ -f "$parked" && ! -L "$parked" ]] \
    || { deploy_error "parked validator has unexpected type"; return 1; }
  verify_backup_tree "$rollback" || return 1
  verify_compatibility_link_matches "$target" "$rollback" || return 1
  [[ ! -e "$rollback/scripts/validate_archive.py" ]] \
    || { deploy_error "parked-validator recovery requires the pre-validator rollback inventory"; return 1; }
  same_file_identity "$parked" "$source_validator" \
    || { deploy_error "parked validator identity is unexpected"; return 1; }
  same_file_identity "$target/SKILL.md" "$rollback/SKILL.md" || return 1
  same_file_identity "$target/scripts/collect_health.sh" "$rollback/scripts/collect_health.sh" \
    || return 1
  [[ ! -e "$target/scripts/validate_archive.py" ]] || return 1
  same_file_identity "$merged/SKILL.md" "$rollback/SKILL.md" || return 1
  same_file_identity "$merged/scripts/collect_health.sh" "$rollback/scripts/collect_health.sh" \
    || return 1
  [[ ! -e "$merged/scripts/validate_archive.py" ]] || return 1
  verify_notifier "$target" "$rollback" || return 1
  rm -f -- "$parked" || return 1
  deploy_checkpoint restore-validator-removed || return 1
  verify_inventory "$target" false || return 1
  verify_compatibility_link_matches "$target" "$rollback" || return 1
}

restore_from_rollback() {
  local target=$1 merged=$2 rollback=$3 parked
  verify_backup_tree "$rollback" || return 1
  verify_compatibility_link_matches "$target" "$rollback" || return 1
  verify_notifier "$target" "$rollback" || return 1

  install_one "$rollback/scripts/collect_health.sh" "$target/scripts/collect_health.sh" \
    "$target/scripts/collect_health.sh" restore-collector || return 1
  install_one "$rollback/SKILL.md" "$target/SKILL.md" "$target/SKILL.md" \
    restore-skill || return 1

  parked="$target/scripts/validate_archive.py.next"
  if [[ -e "$target/scripts/validate_archive.py" ]]; then
    [[ -f "$target/scripts/validate_archive.py" && ! -e "$parked" ]] \
      || { deploy_error "cannot park newly installed validator during restore"; return 1; }
    mv "$target/scripts/validate_archive.py" "$parked" || return 1
    deploy_checkpoint restore-validator-parked || return 1
  fi
  same_file_identity "$target/scripts/collect_health.sh" "$rollback/scripts/collect_health.sh" \
    || { deploy_error "restored collector verification failed: $target/scripts/collect_health.sh"; return 1; }
  same_file_identity "$target/SKILL.md" "$rollback/SKILL.md" \
    || { deploy_error "restored SKILL verification failed: $target/SKILL.md"; return 1; }
  [[ ! -e "$target/scripts/validate_archive.py" ]] \
    || { deploy_error "restored validator absence verification failed: $target/scripts/validate_archive.py"; return 1; }
  same_file_identity "$merged/scripts/collect_health.sh" "$rollback/scripts/collect_health.sh" \
    || { deploy_error "merged collector restore verification failed: $merged/scripts/collect_health.sh"; return 1; }
  same_file_identity "$merged/SKILL.md" "$rollback/SKILL.md" \
    || { deploy_error "merged SKILL restore verification failed: $merged/SKILL.md"; return 1; }
  [[ ! -e "$merged/scripts/validate_archive.py" ]] \
    || { deploy_error "merged validator absence verification failed: $merged/scripts/validate_archive.py"; return 1; }
  deploy_checkpoint restore-old-views-reverified || return 1
  if [[ -e "$parked" ]]; then
    rm -f -- "$parked" || return 1
    deploy_checkpoint restore-validator-removed || return 1
  fi
  verify_notifier "$target" "$rollback" || return 1
  verify_inventory "$target" false || return 1
  verify_compatibility_link_matches "$target" "$rollback" || return 1
}

accept_deployment() {
  local source_skill=$1 source_collector=$2 source_validator=$3 target=$4 merged=$5 rollback=$6
  local output report
  verify_inventory "$target" true || return 1
  verify_compatibility_link_matches "$target" "$rollback" || return 1
  verify_view "$source_skill" "$source_collector" "$source_validator" "$target" || return 1
  deploy_checkpoint acceptance-physical-verified || return 1
  verify_view "$source_skill" "$source_collector" "$source_validator" "$merged" || return 1
  deploy_checkpoint acceptance-merged-verified || return 1
  verify_notifier "$target" "$rollback" || return 1
  bash -n "$target/scripts/collect_health.sh" || return 1
  python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' "$target/scripts/validate_archive.py" \
    || return 1
  validator_smoke "$target/scripts/validate_archive.py" || return 1
  deploy_checkpoint acceptance-validator-fixtures || return 1
  output="$(mktemp /tmp/investment-data-collector-accept.XXXXXXXX)" || return 1
  if ! bash "$target/scripts/collect_health.sh" >"$output"; then
    rm -f -- "$output"
    deploy_error "installed collector could not produce a complete report"
    return
  fi
  if ! jq -e '.release.archive_validation | type == "object" and .ok == true' \
      >/dev/null "$output"; then
    rm -f -- "$output"
    deploy_error "installed collector did not validate actual archive semantics"
    return
  fi
  rm -f -- "$output"
  deploy_checkpoint acceptance-collector-report || return 1
}

install_and_accept() {
  local source_skill=$1 source_collector=$2 source_validator=$3 target=$4 merged=$5 rollback=$6
  install_one "$source_validator" "$target/scripts/validate_archive.py" \
    "$rollback/scripts/validate_archive.py" install-validator || return 1
  install_one "$source_collector" "$target/scripts/collect_health.sh" \
    "$rollback/scripts/collect_health.sh" install-collector || return 1
  install_one "$source_skill" "$target/SKILL.md" "$rollback/SKILL.md" \
    install-skill || return 1
  accept_deployment "$source_skill" "$source_collector" "$source_validator" \
    "$target" "$merged" "$rollback" || return 1
}

deploy_paths() {
  local source_skill=$1 source_collector=$2 source_validator=$3 target=$4 merged=$5 rollback=$6
  verify_source_files "$source_skill" "$source_collector" "$source_validator" || return 1
  finish_parked_validator_restore "$source_validator" "$target" "$merged" "$rollback" \
    || return 1
  prepare_rollback_copy "$target" "$rollback" || return 1
  verify_backup_tree "$rollback" || return 1
  verify_compatibility_link_matches "$target" "$rollback" || return 1
  verify_notifier "$target" "$rollback" || return 1

  if ! install_and_accept "$source_skill" "$source_collector" "$source_validator" \
      "$target" "$merged" "$rollback"; then
    printf 'Deployment failed; restoring verified rollback copy.\n' >&2
    restore_from_rollback "$target" "$merged" "$rollback" \
      || deploy_error "deployment and verified restoration both failed"
    return 1
  fi
}

rollback_paths() {
  local target=$1 merged=$2 rollback=$3 repo_root source_validator
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)" || return 1
  source_validator="$repo_root/qlib/validate_archive.py"
  finish_parked_validator_restore "$source_validator" "$target" "$merged" "$rollback" \
    || return 1
  restore_from_rollback "$target" "$merged" "$rollback" || return 1
}

main() {
  local operation repo_root source_skill source_collector source_validator
  if (($# == 0)); then
    operation=deploy
  elif (($# == 1)) && [[ "$1" == rollback ]]; then
    operation=rollback
  else
    printf 'usage: %s [rollback]\n' "${0##*/}" >&2
    return 2
  fi

  for command in bash cp diff find jq mktemp mv python3 readlink rm sha256sum stat; do
    command -v "$command" >/dev/null 2>&1 \
      || { deploy_error "required command is unavailable: $command"; return 1; }
  done
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)" || return 1
  source_skill="$repo_root/ops/investment-data-project-monitor/SKILL.md"
  source_collector="$repo_root/ops/investment-data-project-monitor/collect_health.sh"
  source_validator="$repo_root/qlib/validate_archive.py"

  if [[ "$operation" == deploy ]]; then
    deploy_paths "$source_skill" "$source_collector" "$source_validator" \
      "$PRODUCTION_TARGET" "$PRODUCTION_MERGED" "$PRODUCTION_ROLLBACK" || return 1
  else
    rollback_paths "$PRODUCTION_TARGET" "$PRODUCTION_MERGED" "$PRODUCTION_ROLLBACK" \
      || return 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
