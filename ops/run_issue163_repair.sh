#!/usr/bin/env bash
set -euo pipefail
exec 8>/dolt/.investment-data.lock
flock -n 8
if [[ ! -d /dolt/investment_data/.dolt ]]; then
  (cd /dolt && dolt clone --depth 1 --branch master chenditc/investment_data investment_data)
fi
cd /dolt/investment_data
dolt status | grep -q 'nothing to commit, working tree clean'
dolt checkout master
dolt fetch origin master
base=qlftrn2agatvmoo3ahr5gui8fds679jd
remote_commit="$(dolt sql -r csv -q "SELECT DOLT_HASHOF('origin/master') AS value" | tail -n1 | tr -d '\r')"
[[ "$remote_commit" == "$base" ]]
[[ "$(dolt sql -r csv -q "SELECT DOLT_HASHOF('HEAD') AS value" | tail -n1 | tr -d '\r')" == "$base" ]]
if [[ "$OPERATION" == merge ]]; then
  [[ "$EXPECTED_REPAIR_COMMIT" =~ ^[0-9a-v]{32}$ ]]
  dolt fetch origin repair/issue-163
  actual="$(dolt sql -r csv -q "SELECT DOLT_HASHOF('origin/repair/issue-163') AS value" | tail -n1 | tr -d '\r')"
  [[ "$actual" == "$EXPECTED_REPAIR_COMMIT" ]]
  dolt merge --ff-only origin/repair/issue-163
else
  python3 /repair/ops/repair_issue163.py plan --database /dolt/investment_data --inputs /inputs --output /output
  if [[ "$OPERATION" == apply ]]; then
    python3 /repair/ops/repair_issue163.py apply --database /dolt/investment_data --output /output
    dolt add -A
    dolt commit -m 'Fix issue 163: authoritative BSE adjustment factors for 2025-10-31'
  fi
fi
if [[ "$OPERATION" == apply || "$OPERATION" == merge ]]; then
  mkdir -p /root/.dolt/creds
  printf '%s\n' "$DOLT_CONFIG_GLOBAL_JSON" >/root/.dolt/config_global.json
  printf '%s\n' "$DOLT_JWK" >/root/.dolt/creds/j6ooicvf6t6a0c18pinnr0p6ao69eglbcsfmmh2247d50.jwk
  if [[ "$OPERATION" == merge ]]; then
    dolt push origin master
  else
    dolt push origin repair/issue-163
  fi
  dolt sql -r csv -q "SELECT DOLT_HASHOF('HEAD') AS value" >/output/repair-commit.txt
fi
