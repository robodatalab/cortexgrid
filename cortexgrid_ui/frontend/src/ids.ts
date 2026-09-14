// The shared key that joins a registry model and its deployment. Matches the
// backend id (cortexflow model_id / deployment app naming):
// `<family>/<suffix>/<run_name>`. Kept in one place so the cross-links between
// the Repository and Deployments trees address the same entity.
export function deploymentId(d: {
    family: string;
    suffix: string;
    run_name: string;
}): string {
    return `${d.family}/${d.suffix}/${d.run_name}`;
}
