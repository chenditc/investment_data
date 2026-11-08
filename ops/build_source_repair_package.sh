#!/usr/bin/env bash
set -euo pipefail
source /investment_data/upload_release.sh
preflight publish
tag="$(TZ=Asia/Shanghai date +%F)"
commit="$(select_normal_dolt_commit)"
build_pair "$tag" "$commit" /output
printf 'Built and validated repair package for Dolt commit %s\n' "$commit"
