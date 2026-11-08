#!/usr/bin/env bash
set -euo pipefail
exec 8>/dolt/.investment-data.lock
flock -n 8
if [[ ! -d /dolt/investment_data/.dolt ]]; then
    (cd /dolt && dolt clone --depth 1 --branch master chenditc/investment_data investment_data)
fi
cd /dolt/investment_data
dolt fetch origin master
remote_commit="$(dolt sql -r csv -q "SELECT DOLT_HASHOF('origin/master') AS value" | tail -n1)"
[[ "$remote_commit" == kl67hfmqtpko9f0kjnu0hqdutjbvvkqs ]]
python3 /repair/ops/repair_dolt_sources.py plan --database /dolt/investment_data \
    --inputs /inputs/factors --index-inputs /inputs/indices --output /output
if [[ "$OPERATION" == apply ]]; then
    python3 /repair/ops/repair_dolt_sources.py apply --database /dolt/investment_data --output /output
    mkdir -p /root/.dolt/creds
    printf '%s\n' "$DOLT_CONFIG_GLOBAL_JSON" >/root/.dolt/config_global.json
    printf '%s\n' "$DOLT_JWK" >/root/.dolt/creds/j6ooicvf6t6a0c18pinnr0p6ao69eglbcsfmmh2247d50.jwk
    dolt add -A
    dolt commit -m 'Repair historical adjustment factors and missing index snapshots'
    dolt push origin repair/source-data-2026-11-08
    dolt sql -r csv -q "SELECT DOLT_HASHOF('HEAD') AS value" >/output/repair-commit.txt
fi
