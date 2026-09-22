import type { DeploymentKey } from "./components/ModelsTree";

// The shared key that joins a registry model and its deployments. Matches the
// backend ids (cortexgrid model_id, deployments_stream.deployment_id and the
// Serve app naming): a model is `<family>/<suffix>/<run_name>`, and each of its
// deployments is that id, followed by `/<config_fingerprint>` when it was given
// a config. Kept in one place so the cross-links between the Repository and
// Deployments trees address the same entity.
export function modelId(m: {
    family: string;
    suffix: string;
    run_name: string;
}): string {
    return `${m.family}/${m.suffix}/${m.run_name}`;
}

export function deploymentId(key: DeploymentKey): string {
    return key.config_fingerprint === ""
        ? modelId(key)
        : `${modelId(key)}/${key.config_fingerprint}`;
}

export function serveApplicationName(key: DeploymentKey): string {
    const modelSegments = [key.family, key.suffix, key.run_name];
    const segments =
        key.config_fingerprint === ""
            ? modelSegments
            : [...modelSegments, key.config_fingerprint];
    return segments.join("__");
}

export function deploymentApiUrl(key: DeploymentKey, endpoint = ""): string {
    const modelPath = [key.family, key.suffix, key.run_name]
        .map(encodeURIComponent)
        .join("/");
    const endpointPath = endpoint === "" ? "" : `/${endpoint}`;
    const query =
        key.config_fingerprint === ""
            ? ""
            : `?${new URLSearchParams({ config_fingerprint: key.config_fingerprint }).toString()}`;
    return `/api/deployments/${modelPath}${endpointPath}${query}`;
}
