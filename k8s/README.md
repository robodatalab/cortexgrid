# k8s

GitOps manifests watched by Argo CD. See [argo-deployments/](argo-deployments/) for the actual deployment specs. [argocd.yaml](argocd.yaml) is the one-shot bootstrap applied at seed time and is not watched by Argo.

## FAQ

### Argo sync is stuck / app stays OutOfSync after a Git push

The operation hit its retry limit, or Argo cached a stale state. Clear it:

```bash
kubectl -n argocd patch app <app-name> --type merge -p '{"operation": null}'
kubectl -n argocd annotate app <app-name> argocd.argoproj.io/refresh=hard --overwrite
```

### How do I verify Argo is actually looking at my latest commit?

```bash
kubectl -n argocd get app <app-name> -o jsonpath='{.status.sync.revision}'
git show <that-sha>:<path-to-file>
```

If the SHA is stale, force a hard refresh as above.

### A sync fails because a CRD isn't installed yet

Argo validates all resources upfront; a resource whose CRD comes from a sibling Application will fail validation before sync-waves take effect. Annotate the CRD-dependent resource:

```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true
```

## Known issues we accept

### SSA field-ownership breakage when changing `Deployment.strategy.type`

**Symptom.** Argo sync fails with `Deployment.apps ... is invalid: spec.strategy.rollingUpdate: Forbidden: may not be specified when strategy type is 'Recreate'`. Live Deployment holds stale `rollingUpdate` subfields owned by `kube-controller-manager` (default-filled when `type: RollingUpdate`). ServerSideApply can't remove them — it only touches fields it owns.

**Why we don't do anything preemptive.** The fix is `argocd.argoproj.io/sync-options: Replace=true` on the affected Deployment, which swaps SSA for `kubectl replace` and wipes stale fields. Applying that blanket to every Deployment costs pod churn on every manifest edit (image tag, env, probe — all trigger a full recreate). The trigger — changing `strategy.type` — is rare, so we eat the one-time pain of adding the annotation when it bites. See [workloads/ray/deployment.yaml](workloads/ray/deployment.yaml) for the live example.
