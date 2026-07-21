---
name: investment-data-project-monitor
description: Monitor and safely repair the chenditc/investment_data GitHub Actions pipeline running on the nvdev2 ARC/minikube runner. Use for on-demand nightly health checks, missing daily data, missing qlib releases, ARC health, or investment_data workflow failures.
---

# Investment Data Project Monitor

Run this on demand, preferably after 21:00 Asia/Shanghai when checking the completed daily pipeline. Verify the immutable data source first, then the GitHub Actions jobs and the downloaded archive/manifest pair. Perform only the explicitly allowed low-risk repairs. Notify via Feishu when the state is abnormal, ambiguous, or needs a risky action.

## Current Architecture

- GitHub repository: `chenditc/investment_data`, default branch `main`
- DoltHub repository: `chenditc/investment_data`, branch `master`
- Data table: `final_a_stock_eod_price`
- Trading calendar: `ts_trade_day_calendar`
- Data workflow: `data_update.yml`
- Release workflow: `upload_release.yml`, scheduled at 11:01 UTC / 19:01 Asia/Shanghai
- Canonical release pair: `qlib_bin.tar.gz` and `qlib_bin.manifest.json`
- Runner host: `nvdev2`
- Minikube profile: `investment-gha`
- ARC controller namespace: `arc-systems`
- ARC runner namespace: `arc-runners`
- Runner scale set: `investment-arc`
- Docker graph cache PVC: `investment-data-docker-graph`, expected capacity `100Gi`

The old serv1 runner, `upload-qlib-tar` container, host cron, archive path, and proxy are retired. Never use the old repair commands from serv1.

## Safety Rules

- Never print or inspect secret values, environment dumps, Kubernetes Secrets, or container environments.
- Never delete or recreate a release, tag, PVC, PV, Docker volume, minikube profile, or ARC installation automatically.
- Never publish a release unless Dolt `max_date` equals the expected SSE trade date.
- Never perform a direct release upload. Publication authority belongs only to `upload_release.yml` on `main`.
- Never restart minikube, ARC controller, or listener automatically. Report and notify first.
- Do not rerun a successful workflow merely because a later artifact was intentionally removed.
- Trigger each workflow at most once per monitor run, then wait for and verify its result.
- Prefer observation over repair before 18:30 for data freshness and before 21:00 for release publication.

## Check Sequence

1. Run the collector:

```bash
bash ~/.claude/skills/investment-data-project-monitor/scripts/collect_health.sh
```

The collector downloads both canonical assets on every run, verifies their GitHub API size and SHA-256 identities, and runs the deployed byte-identical archive validator. A complete `healthy`, `degraded`, or `not_due` report exits `0`; an inability to construct a report exits nonzero with empty stdout.

2. Read its JSON report. Required healthy state after 21:00 Asia/Shanghai:

- `dolt.data_fresh` is `true`.
- `platform.healthy` is `true`.
- The latest `main` data workflow is successful.
- The latest `main` upload workflow is successful.
- `release.valid` is `true` for today's tag.
- Exactly one total uploaded asset exists for each canonical name.
- `release.archive_validation.ok` is `true`; the object is the validator's unmodified JSON document.
- No workflow has been in progress for more than 90 minutes.

The reported asset size alone is not evidence of validity. A missing, duplicate, wrong-state, digest-mismatched, stale, malformed, or unsafe archive pair makes the release invalid and, after the due time, degrades overall health.

3. If a workflow failed, inspect only the failed run and its failed job logs:

```bash
gh run view RUN_ID --repo chenditc/investment_data --json status,conclusion,jobs,url
gh run view RUN_ID --repo chenditc/investment_data --log-failed
```

Summarize the error. Do not include secrets or long logs in the report or notification.

## Allowed Repairs

### Stale data after 18:30

Trigger `data_update.yml` only when Dolt is stale, no update is queued/running, the latest scheduled `main` update failed/cancelled/is absent, and this monitor run has not already triggered it:

```bash
gh workflow run data_update.yml --repo chenditc/investment_data --ref main
```

Wait for completion, then rerun the collector.

### Missing or invalid release after 21:00

Trigger `upload_release.yml` only when Dolt is fresh, no upload is queued/running, today's canonical pair is invalid, the scheduled `main` upload failed/cancelled/did not run, and this monitor run has not already triggered it:

```bash
gh workflow run upload_release.yml --repo chenditc/investment_data --ref main -f operation=publish
```

Wait for completion, then rerun the collector. If the workflow succeeds but the downloaded pair remains invalid, notify instead of attempting direct upload.

## Feishu Notification

Send a notification for any degraded state after its due time, any repair attempt, repeated failure, stuck workflow, low disk space, or action requiring human judgment:

```bash
~/.claude/skills/investment-data-project-monitor/scripts/notify_feishu.sh "investment_data nightly monitor
当前时间: ...
状态: ...
expected trade date: ...
Dolt max_date: ...
data_update: ...
upload_release: ...
release archive validation: ...
ARC/minikube: ...
已执行的安全修复: ...
建议人工动作: ..."
```

The helper, its recipient, arguments, secret lookup, retry timing, and message behavior are unchanged. Do not send a normal-success notification. Never include secrets, environment dumps, or long logs.

### Fail-safe repository rollback

A full repository revert is ordered and fail-closed:

1. Run `gh workflow disable upload_release.yml --repo chenditc/investment_data`.
2. Query `gh workflow view upload_release.yml --repo chenditc/investment_data --json state --jq .state` and require the exact result `disabled_manually`.
3. Query both `upload_release.yml` and `data_update.yml` runs and wait until every queued or in-progress job using the shared Dolt volume has drained.
4. Perform the full repository revert.
5. Run `gh workflow view upload_release.yml --repo chenditc/investment_data --json state --jq .state` again and require `disabled_manually`.
6. Do not re-enable publication until validator-backed digest pinning, workflow authority, and shared concurrency/filesystem locking are restored and verified end to end.

The revert may move the convenience `latest` image and therefore affect data update, but it cannot publish while the upload workflow is disabled. Draining is mandatory because a full revert may remove the shared lock and concurrency group. Already accepted release assets are untouched. An interrupted historical repair may complete only through the fixed `repair-2026-07-20` operation, and the stale backup is never auto-restored. The deployed monitor is separate external state; roll it back only with the tracked `ops/investment-data-project-monitor/deploy.sh rollback`, never as part of the repository revert.


## Final Report

Report the Asia/Shanghai time and overall state; expected and actual Dolt dates; workflow states and URLs; canonical asset identities and `release.archive_validation`; ARC/minikube/PVC/disk health; stuck runs; repairs and post-repair verification; Feishu outcome; and remaining human action.

Principle: validate the actual release bytes against immutable provenance before declaring publication healthy.
