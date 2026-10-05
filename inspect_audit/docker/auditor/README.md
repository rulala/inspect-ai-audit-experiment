# Auditor image

The auditor's container image for Kubernetes sandboxes (e.g. METR's Hawk), where the
`build:`-based sandbox that `audit_compose` generates does not work because k8s can
only pull published images. Build and push it once, then pass its name as the
`auditor_image` argument to `audit_values`:

```sh
docker build -t ghcr.io/OWNER/inspect-audit-auditor:latest docker/auditor
docker push ghcr.io/OWNER/inspect-audit-auditor:latest
```
