# ARC on nvdev2

This repository uses a dedicated Actions Runner Controller scale set named `investment-arc`.

## Workflows

- `.github/workflows/data_update.yml` runs hourly on `investment-arc`.
- `.github/workflows/upload_release.yml` runs daily at `11:01 UTC`, which matches the legacy `19:01` Asia/Shanghai cron on `serv1`.

## Deployment

From a checkout on `nvdev2`:

```bash
export GITHUB_PAT=...
bash infra/arc/install_nvdev2.sh
```

This installs:

- the ARC controller in namespace `arc-systems`
- the runner scale set in namespace `arc-runners`
- a `100Gi` PVC named `investment-data-docker-graph`

The PVC is mounted into the dind sidecar at `/var/lib/docker`, which keeps Docker images and named volumes such as `dolt_update` and `dolt-vol` warm across ephemeral runner pods.
