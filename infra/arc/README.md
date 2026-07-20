# ARC on nvdev2

This repository uses a dedicated Actions Runner Controller scale set named `investment-arc`.

## Workflows

- `.github/workflows/data_update.yml` runs hourly on `investment-arc`.
- `.github/workflows/upload_release.yml` runs daily at `11:01 UTC`, which matches the legacy `19:01` Asia/Shanghai cron on `serv1`.

## Deployment

From a checkout on `nvdev2`:

```bash
minikube start -p investment-gha --driver=docker --cpus=16 --memory=64g --disk-size=100g
export GITHUB_PAT=...
bash infra/arc/install_nvdev2.sh
```

This installs:

- the ARC controller in namespace `arc-systems`
- the runner scale set in namespace `arc-runners`
- a `100Gi` PVC named `investment-data-docker-graph`

The PVC is mounted into the dind sidecar at `/var/lib/docker`, which keeps Docker images and named volumes such as `dolt_update` and `dolt-vol` warm across ephemeral runner pods.

Use at least `64Gi` of memory for the `investment-gha` minikube profile. The `upload_release` qlib build exceeded a `16Gi` profile and was OOM-killed on `2026-07-20`.
