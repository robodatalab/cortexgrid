# Argo CD Image Updater

## Problem

Pods running `:latest` or a specific SHA tag don't re-pull when GHCR updates — pod restarts don't happen without `kubectl rollout restart`. Without this component every image change required a manual pod kick. Image Updater polls the registry and writes the new tag into the Application CR, which triggers Argo to re-sync the Deployment.

## Components

**stack.yaml** — Argo Application installing `argocd-image-updater` Helm chart into the `argocd` namespace. Configured with a single GHCR registry entry using the reflected `ghcr-pull` Secret for auth. No write-back to git — updates go straight into the Application CR (`write-back-method: argocd` on each tracked Application).

## Dependencies

- **Reflector** ([../secrets/](../secrets/)) — mirrors `ghcr-pull` into the `argocd` namespace so Image Updater can pull registry metadata.
- **Tracked Applications** — each Argo Application whose workload runs a private image must carry the `argocd-image-updater.argoproj.io/*` annotations naming the image, tag filter, and write-back method. See the README in each tracked Application's folder.
- **CI tagging convention** — CI must push images with branch-slug + commit SHA tags (e.g., `main-abc123…`). Image Updater's `allow-tags` filter per Application selects the right branch's images.
