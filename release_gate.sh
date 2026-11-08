#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="chenditc/investment_data"
API_ROOT="https://api.github.com/repos/${REPOSITORY}"
DOLTHUB_GRAPHQL_URL="https://www.dolthub.com/graphql"
ARCHIVE_NAME="qlib_bin.tar.gz"
MANIFEST_NAME="qlib_bin.manifest.json"

die() {
    printf 'Error: %s\n' "$*" >&2
    return 1
}

usage() {
    printf 'usage: %s\n' "${0##*/}" >&2
}

canonical_date() {
    local value=$1
    [[ "$value" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
        && [[ "$(date -u -d "$value" +%F 2>/dev/null)" == "$value" ]]
}

require_command() {
    command -v "$1" >/dev/null 2>&1 \
        || { die "required command is unavailable: $1"; return 1; }
}

preflight() {
    [[ "${GITHUB_ACTIONS:-}" == "true" ]] \
        || { die "release gate requires GitHub Actions authority"; return 1; }
    [[ "${GITHUB_EVENT_NAME:-}" == "schedule" \
        || "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]] \
        || { die "release gate requires schedule or workflow_dispatch"; return 1; }
    [[ "${GITHUB_REPOSITORY:-}" == "$REPOSITORY" ]] \
        || { die "unexpected GitHub repository"; return 1; }
    [[ "${GITHUB_REF:-}" == "refs/heads/main" ]] \
        || { die "release gate requires main branch authority"; return 1; }
    [[ "${OPERATION:-}" == "publish" ]] \
        || { die "release gate only supports publish"; return 1; }
    [[ -n "${GITHUB_TOKEN:-}" ]] \
        || { die "GITHUB_TOKEN is required"; return 1; }
    for command in cat curl jq date mktemp rm; do
        require_command "$command" || return 1
    done
}

github_get_release_by_tag_optional() {
    local tag=$1 response_file status curl_status=0
    response_file="$(mktemp /tmp/investment-data-release-gate.XXXXXXXX)" \
        || return 1
    status="$(curl -sS -o "$response_file" -w '%{http_code}' \
        --connect-timeout 10 --max-time 30 \
        --retry 1 --retry-delay 2 --retry-max-time 65 \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Accept: application/vnd.github+json" \
        "${API_ROOT}/releases/tags/${tag}")" || curl_status=$?
    if ((curl_status != 0)); then
        rm -f -- "$response_file"
        die "GitHub release lookup failed"
        return 1
    fi
    case "$status" in
        200)
            cat "$response_file"
            rm -f -- "$response_file"
            ;;
        404)
            rm -f -- "$response_file"
            return 4
            ;;
        *)
            rm -f -- "$response_file"
            die "GitHub release lookup failed with HTTP $status"
            return 1
            ;;
    esac
}

release_identity_is_valid() {
    local release=$1 tag=$2
    jq -e --arg tag "$tag" '
        (.id | type) == "number"
        and .id > 0
        and .tag_name == $tag
        and .draft == false
        and .prerelease == false
        and (.assets | type) == "array"
    ' >/dev/null 2>&1 <<<"$release"
}

release_is_complete() {
    local release=$1
    jq -e --arg archive "$ARCHIVE_NAME" --arg manifest "$MANIFEST_NAME" '
        (.assets | length) == 2
        and ([.assets[].name] | sort) == ([$archive, $manifest] | sort)
        and all(.assets[];
            .state == "uploaded"
            and (.size | type) == "number"
            and .size > 0
            and (.digest | type) == "string"
            and (.digest | test("^sha256:[0-9a-f]{64}$"))
        )
    ' >/dev/null 2>&1 <<<"$release"
}

query_freshness() {
    local tag=$1 sql payload response values source_max_date target_trade_date
    canonical_date "$tag" \
        || { die "freshness query date is invalid"; return 1; }
    sql="SELECT (SELECT MAX(tradedate) FROM final_a_stock_eod_price) AS source_max_date, (SELECT MAX(date) FROM ts_trade_day_calendar WHERE exchange = 'SSE' AND is_open = 1 AND date <= '${tag}') AS target_trade_date"
    payload="$(jq -cn --arg sql "$sql" '{
        query: "query ReleaseFreshness($sql: String!) { sqlSelect(ownerName: \"chenditc\", repoName: \"investment_data\", refName: \"master\", queryString: $sql, rowLimit: 1) { queryExecutionStatus columns { name } rows { columnValues { displayValue } } } }",
        variables: {sql: $sql}
    }')" || return 1
    response="$(curl -fsSL --connect-timeout 10 --max-time 30 \
        --retry 1 --retry-delay 2 --retry-max-time 65 \
        -H 'Content-Type: application/json' --data-binary "$payload" \
        "$DOLTHUB_GRAPHQL_URL")" \
        || { die "DoltHub freshness query failed"; return 1; }
    values="$(jq -er '
        select((.errors // []) == [])
        | .data.sqlSelect
        | select(.queryExecutionStatus == "Success")
        | select([.columns[].name] == ["source_max_date", "target_trade_date"])
        | .rows
        | if length == 1 then .[0] else error("unexpected row count") end
        | .columnValues
        | select(length == 2 and all(.[]; (.displayValue | type) == "string"))
        | map(.displayValue)
        | @tsv
    ' <<<"$response")" \
        || { die "DoltHub freshness response is invalid"; return 1; }
    IFS=$'\t' read -r source_max_date target_trade_date <<<"$values"
    canonical_date "$source_max_date" \
        || { die "source max date is invalid"; return 1; }
    canonical_date "$target_trade_date" \
        || { die "target trade date is invalid"; return 1; }
    printf '%s\t%s\n' "$source_max_date" "$target_trade_date"
}

publication_mode() {
    local tag release status freshness source_max_date target_trade_date
    tag="$(TZ=Asia/Shanghai date +%F)" || return 1
    canonical_date "$tag" || { die "release tag is invalid"; return 1; }

    status=0
    release="$(github_get_release_by_tag_optional "$tag")" || status=$?
    if ((status == 0)); then
        release_identity_is_valid "$release" "$tag" \
            || { die "release identity, draft, prerelease, or asset shape is invalid"; return 1; }
        if release_is_complete "$release"; then
            printf 'Existing release %s has two uploaded canonical assets; validating bytes.\n' \
                "$tag" >&2
            printf 'validate-existing\n'
            return 0
        fi
        printf 'Release %s is incomplete; checking whether publication can resume.\n' \
            "$tag" >&2
    elif ((status != 4)); then
        return 1
    fi

    if ! freshness="$(query_freshness "$tag")"; then
        # This is an optimization, not publication authority. publish_current
        # builds and validates a fresh, immutable local snapshot before it can
        # create a release or upload any asset.
        printf '::warning::Online freshness check unavailable; deferring to mandatory local snapshot and archive validation.\n' >&2
        printf 'publish\n'
        return 0
    fi
    IFS=$'\t' read -r source_max_date target_trade_date <<<"$freshness"
    if [[ "$source_max_date" == "$target_trade_date" ]]; then
        printf 'DoltHub is fresh at %s; publication may proceed.\n' \
            "$target_trade_date" >&2
        printf 'publish\n'
    elif [[ "$source_max_date" < "$target_trade_date" ]]; then
        printf 'DoltHub source date %s has not reached target trade date %s; skipping this opportunity.\n' \
            "$source_max_date" "$target_trade_date" >&2
        printf 'skip\n'
    else
        die "source max date $source_max_date is later than target trade date $target_trade_date"
        return 1
    fi
}

main() {
    if (($# != 0)); then
        usage
        return 2
    fi
    preflight || return 1
    publication_mode
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
