#!/usr/bin/env bash
set -uo pipefail

if (($# != 0)); then
  printf 'usage: %s\n' "${0##*/}" >&2
  exit 2
fi

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

for command in jq gh curl minikube sha256sum stat mktemp python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "$command" >&2
    exit 1
  fi
done
if [[ ! -r "$script_dir/validate_archive.py" ]]; then
  printf 'Archive validator is unavailable.\n' >&2
  exit 1
fi

tmp_dir="$(mktemp -d /tmp/investment-data-health.XXXXXXXX)" || {
  printf 'Unable to create collector temporary directory.\n' >&2
  exit 1
}
trap 'rm -rf -- "$tmp_dir"' EXIT

set -a
if [[ -f "$HOME/.secret_env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.secret_env"
fi
if [[ -f "$HOME/.amc/.env" ]]; then
  # Compatibility with the existing nvdev2 secret store.
  # shellcheck disable=SC1091
  source "$HOME/.amc/.env"
fi
set +a
export GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"

repo="chenditc/investment_data"
profile="investment-gha"
today="$(TZ=Asia/Shanghai date +%F)"
now="$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S %Z %z')"
time_hhmm="$(TZ=Asia/Shanghai date +%H%M)"

error_json() {
  jq -cn --arg error "$1" '{error:$error}'
}

dolt_query() {
  local query="$1"
  local output
  if output="$(curl -fsS --get --data-urlencode "q=$query" \
      "https://www.dolthub.com/api/v1alpha1/chenditc/investment_data/master" 2>&1)"; then
    printf '%s' "$output"
  else
    error_json "$output"
  fi
}

dolt_max_raw="$(dolt_query 'SELECT MAX(tradedate) AS max_date FROM final_a_stock_eod_price')"
expected_raw="$(dolt_query "SELECT MAX(date) AS expected_date FROM ts_trade_day_calendar WHERE exchange = 'SSE' AND is_open = 1 AND date <= CURRENT_DATE")"
dolt_max="$(jq -r '.rows[0].max_date // empty' <<<"$dolt_max_raw" 2>/dev/null)"
expected_date="$(jq -r '.rows[0].expected_date // empty' <<<"$expected_raw" 2>/dev/null)"

data_fresh=false
if [[ -n "$dolt_max" && "$dolt_max" == "$expected_date" ]]; then
  data_fresh=true
fi

minikube_raw="$(minikube status -p "$profile" --output=json 2>&1)"
if ! jq -e . >/dev/null 2>&1 <<<"$minikube_raw"; then
  minikube_raw="$(error_json "$minikube_raw")"
fi

nodes_raw="$(minikube -p "$profile" kubectl -- get nodes -o json 2>&1)"
if jq -e . >/dev/null 2>&1 <<<"$nodes_raw"; then
  nodes="$(jq -c '[.items[] | {name:.metadata.name,ready:any(.status.conditions[]?; .type == "Ready" and .status == "True"),kernel:(.status.nodeInfo.kernelVersion // null),containerRuntime:(.status.nodeInfo.containerRuntimeVersion // null)}]' <<<"$nodes_raw")"
else
  nodes="$(error_json "$nodes_raw")"
fi

pods_raw="$(minikube -p "$profile" kubectl -- get pods --all-namespaces -o json 2>&1)"
if jq -e . >/dev/null 2>&1 <<<"$pods_raw"; then
  pods="$(jq -c '[.items[] | select(.metadata.namespace == "arc-systems" or .metadata.namespace == "arc-runners") | {namespace:.metadata.namespace,name:.metadata.name,phase:.status.phase,ready:([.status.containerStatuses[]? | .ready] | all),restarts:([.status.containerStatuses[]?.restartCount] | add // 0),startedAt:(.status.startTime // null)}]' <<<"$pods_raw")"
else
  pods="$(error_json "$pods_raw")"
fi

scale_set_raw="$(minikube -p "$profile" kubectl -- get autoscalingrunnerset investment-arc -n arc-runners -o json 2>&1)"
if jq -e . >/dev/null 2>&1 <<<"$scale_set_raw"; then
  scale_set="$(jq -c '{name:.metadata.name,minRunners:(.spec.minRunners // null),maxRunners:(.spec.maxRunners // null),currentRunners:(.status.currentRunners // 0),pendingRunners:(.status.pendingRunners // 0),runningRunners:(.status.runningRunners // 0)}' <<<"$scale_set_raw")"
else
  scale_set="$(error_json "$scale_set_raw")"
fi

pvc_raw="$(minikube -p "$profile" kubectl -- get pvc investment-data-docker-graph -n arc-runners -o json 2>&1)"
if jq -e . >/dev/null 2>&1 <<<"$pvc_raw"; then
  pvc="$(jq -c '{name:.metadata.name,phase:.status.phase,capacity:(.status.capacity.storage // null),storageClass:(.spec.storageClassName // null)}' <<<"$pvc_raw")"
else
  pvc="$(error_json "$pvc_raw")"
fi

platform_healthy="$(jq -rn \
  --argjson nodes "$nodes" \
  --argjson pods "$pods" \
  --argjson scale_set "$scale_set" \
  --argjson pvc "$pvc" '
    (($nodes | type) == "array")
    and (($nodes | length) > 0)
    and ([$nodes[] | select(.ready != true)] | length == 0)
    and (($pods | type) == "array")
    and ([$pods[] | select(.namespace == "arc-systems" and (.name | contains("controller")))] | length == 1)
    and ([$pods[] | select(.namespace == "arc-systems" and (.name | contains("listener")))] | length == 1)
    and ([$pods[] | select(.phase != "Running" or .ready != true)] | length == 0)
    and ($scale_set.name == "investment-arc")
    and ($pvc.phase == "Bound")')"

data_runs="$(gh run list --repo "$repo" --workflow data_update.yml --limit 10 \
  --json databaseId,status,conclusion,createdAt,updatedAt,url,headBranch,event 2>&1)"
if ! jq -e . >/dev/null 2>&1 <<<"$data_runs"; then
  data_runs="$(error_json "$data_runs")"
fi

upload_runs="$(gh run list --repo "$repo" --workflow upload_release.yml --limit 10 \
  --json databaseId,status,conclusion,createdAt,updatedAt,url,headBranch,event 2>&1)"
if ! jq -e . >/dev/null 2>&1 <<<"$upload_runs"; then
  upload_runs="$(error_json "$upload_runs")"
fi

latest_main_data="$(jq -cn --argjson runs "$data_runs" '
  if ($runs | type) == "array" then ([ $runs[] | select(.headBranch == "main") ][0] // null) else null end')"
latest_main_upload="$(jq -cn --argjson runs "$upload_runs" '
  if ($runs | type) == "array" then ([ $runs[] | select(.headBranch == "main") ][0] // null) else null end')"
now_epoch="$(date -u +%s)"
stuck_runs="$(jq -cn \
  --argjson now "$now_epoch" \
  --argjson data "$data_runs" \
  --argjson upload "$upload_runs" '
    [
      ((if ($data | type) == "array" then $data else [] end)[]?),
      ((if ($upload | type) == "array" then $upload else [] end)[]?)
      | select(.status != "completed")
      | . + {ageSeconds:($now - (.createdAt | fromdateiso8601))}
      | select(.ageSeconds > 5400)
    ]')"

release_output="$(gh release view "$today" --repo "$repo" \
  --json tagName,isDraft,isPrerelease,publishedAt,url,assets 2>&1)"
if jq -e . >/dev/null 2>&1 <<<"$release_output"; then
  release="$(jq -c '. + {exists:true}' <<<"$release_output")"
else
  release="$(jq -cn --arg error "$release_output" '{exists:false,error:$error,assets:[]}')"
fi

archive_validation=null
archive_error=""
release_valid=false
archive_assets="$(jq -c '[.assets[]? | select(.name == "qlib_bin.tar.gz")]' <<<"$release")"
manifest_assets="$(jq -c '[.assets[]? | select(.name == "qlib_bin.manifest.json")]' <<<"$release")"
if ! jq -e --arg today "$today" \
    '.exists == true and .tagName == $today and .isDraft == false and .isPrerelease == false' \
    >/dev/null 2>&1 <<<"$release"; then
  archive_error="release identity is invalid"
elif [[ "$(jq 'length' <<<"$archive_assets")" != "1" \
    || "$(jq 'length' <<<"$manifest_assets")" != "1" ]]; then
  archive_error="canonical archive and manifest names must each occur exactly once"
elif [[ "$(jq -r '.[0].state' <<<"$archive_assets")" != "uploaded" \
    || "$(jq -r '.[0].state' <<<"$manifest_assets")" != "uploaded" ]]; then
  archive_error="canonical archive and manifest must both be uploaded"
else
  archive_api_url="$(jq -r '.[0].apiUrl' <<<"$archive_assets")"
  manifest_api_url="$(jq -r '.[0].apiUrl' <<<"$manifest_assets")"
  archive_identity="$(gh api "$archive_api_url" 2>&1)"
  manifest_identity="$(gh api "$manifest_api_url" 2>&1)"
  if ! jq -e --arg url "$archive_api_url" \
      '.url == $url and .name == "qlib_bin.tar.gz" and .state == "uploaded" and (.size | type == "number") and (.size > 0) and (.digest | test("^sha256:[0-9a-f]{64}$"))' \
      >/dev/null 2>&1 <<<"$archive_identity"; then
    archive_error="archive API identity is invalid"
  elif ! jq -e --arg url "$manifest_api_url" \
      '.url == $url and .name == "qlib_bin.manifest.json" and .state == "uploaded" and (.size | type == "number") and (.size > 0) and (.digest | test("^sha256:[0-9a-f]{64}$"))' \
      >/dev/null 2>&1 <<<"$manifest_identity"; then
    archive_error="manifest API identity is invalid"
  else
    archive_path="$tmp_dir/qlib_bin.tar.gz"
    manifest_path="$tmp_dir/qlib_bin.manifest.json"
    if ! gh api -H 'Accept: application/octet-stream' "$archive_api_url" >"$archive_path" 2>"$tmp_dir/archive-download.err"; then
      archive_error="archive download failed"
    elif ! gh api -H 'Accept: application/octet-stream' "$manifest_api_url" >"$manifest_path" 2>"$tmp_dir/manifest-download.err"; then
      archive_error="manifest download failed"
    elif [[ "$(stat -c%s "$archive_path")" != "$(jq -r '.size' <<<"$archive_identity")" \
        || "sha256:$(sha256sum "$archive_path" | awk '{print $1}')" != "$(jq -r '.digest' <<<"$archive_identity")" ]]; then
      archive_error="archive download identity mismatch"
    elif [[ "$(stat -c%s "$manifest_path")" != "$(jq -r '.size' <<<"$manifest_identity")" \
        || "sha256:$(sha256sum "$manifest_path" | awk '{print $1}')" != "$(jq -r '.digest' <<<"$manifest_identity")" ]]; then
      archive_error="manifest download identity mismatch"
    else
      validator_stdout="$tmp_dir/validator.stdout"
      validator_stderr="$tmp_dir/validator.stderr"
      validator_status=0
      python3 "$script_dir/validate_archive.py" \
        --archive "$archive_path" --manifest "$manifest_path" \
        --expected-tag "$today" --require-publishable \
        >"$validator_stdout" 2>"$validator_stderr" || validator_status=$?
      if [[ "$validator_status" != "0" && "$validator_status" != "1" ]]; then
        archive_error="archive validator could not produce a report"
      elif ! jq -e 'type == "object" and (.ok | type == "boolean")' \
          >/dev/null 2>&1 "$validator_stdout"; then
        archive_error="archive validator output is invalid"
      else
        archive_validation="$(jq -c . "$validator_stdout")"
        if [[ "$validator_status" == "0" && "$(jq -r '.ok' <<<"$archive_validation")" == "true" ]]; then
          release_valid=true
        fi
      fi
    fi
  fi
fi

host_disk="$(df -P "$HOME" | awk 'NR==2 {print $5}' | tr -d '%')"
if [[ ! "$host_disk" =~ ^[0-9]+$ ]]; then
  host_disk=0
fi

data_check_due=false
release_check_due=false
if ((10#$time_hhmm >= 1830)); then
  data_check_due=true
fi
if ((10#$time_hhmm >= 2100)); then
  release_check_due=true
fi

overall="healthy"
if [[ "$platform_healthy" != "true" ]] || ((host_disk >= 90)); then
  overall="degraded"
elif [[ "$data_check_due" == "true" && "$data_fresh" != "true" ]]; then
  overall="degraded"
elif [[ "$data_check_due" == "true" && "$(jq -r '.conclusion // empty' <<<"$latest_main_data")" != "success" ]]; then
  overall="degraded"
elif [[ "$release_check_due" == "true" && "$release_valid" != "true" ]]; then
  overall="degraded"
elif [[ "$release_check_due" == "true" && "$(jq -r '.conclusion // empty' <<<"$latest_main_upload")" != "success" ]]; then
  overall="degraded"
elif (("$(jq 'length' <<<"$stuck_runs")" > 0)); then
  overall="degraded"
elif [[ "$data_check_due" != "true" || "$release_check_due" != "true" ]]; then
  overall="not_due"
fi

report="$(jq -n \
  --arg now "$now" \
  --arg today "$today" \
  --arg overall "$overall" \
  --arg expected_date "$expected_date" \
  --arg dolt_max "$dolt_max" \
  --arg archive_error "$archive_error" \
  --argjson archive_validation "$archive_validation" \
  --argjson data_fresh "$data_fresh" \
  --argjson data_check_due "$data_check_due" \
  --argjson release_check_due "$release_check_due" \
  --argjson minikube "$minikube_raw" \
  --argjson nodes "$nodes" \
  --argjson platform_healthy "$platform_healthy" \
  --argjson pods "$pods" \
  --argjson scale_set "$scale_set" \
  --argjson pvc "$pvc" \
  --argjson data_runs "$data_runs" \
  --argjson upload_runs "$upload_runs" \
  --argjson latest_main_data "$latest_main_data" \
  --argjson latest_main_upload "$latest_main_upload" \
  --argjson stuck_runs "$stuck_runs" \
  --argjson release "$release" \
  --argjson release_valid "$release_valid" \
  --argjson host_disk_percent "$host_disk" \
  '{
    checked_at:$now,
    today:$today,
    overall_status:$overall,
    policy:{data_check_due:$data_check_due,release_check_due:$release_check_due},
    dolt:{expected_date:$expected_date,max_date:$dolt_max,data_fresh:$data_fresh},
    workflows:{
      data_update:$data_runs,
      upload_release:$upload_runs,
      latest_main_data:$latest_main_data,
      latest_main_upload:$latest_main_upload,
      stuck_over_90m:$stuck_runs
    },
    release:($release + {
      valid:$release_valid,
      archive_validation:$archive_validation,
      archive_error:(if $archive_error == "" then null else $archive_error end)
    }),
    platform:{healthy:$platform_healthy,minikube:$minikube,nodes:$nodes,pods:$pods,scale_set:$scale_set,pvc:$pvc,host_disk_used_percent:$host_disk_percent}
  }' 2>"$tmp_dir/report.err")" || {
    printf 'Unable to construct complete health report.\n' >&2
    exit 1
  }
printf '%s\n' "$report"
