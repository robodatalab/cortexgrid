import type { Deployment, Model } from "./components/ModelsTree";
import { deploymentId, modelId } from "./ids";

const SHORT_FINGERPRINT_LENGTH = 7;

export type BundleUpdate = {
    deployedFingerprint: string;
    registeredFingerprint: string;
};

export type BundleUpdateByDeploymentId = Record<string, BundleUpdate>;

export function shortFingerprint(fingerprint: string): string {
    return fingerprint === ""
        ? "unsigned"
        : `#${fingerprint.slice(0, SHORT_FINGERPRINT_LENGTH)}`;
}

export function pendingBundleUpdate(
    deployment: Deployment,
    registeredModel: Model | undefined,
): BundleUpdate | null {
    if (registeredModel === undefined) return null;
    const runsRegisteredBundle =
        deployment.bundle_fingerprint !== "" &&
        deployment.bundle_fingerprint === registeredModel.bundle_fingerprint;
    return runsRegisteredBundle
        ? null
        : {
              deployedFingerprint: deployment.bundle_fingerprint,
              registeredFingerprint: registeredModel.bundle_fingerprint,
          };
}

export function pendingBundleUpdates(
    deployments: Deployment[],
    modelsById: Record<string, Model>,
): BundleUpdateByDeploymentId {
    const updates: BundleUpdateByDeploymentId = {};
    for (const deployment of deployments) {
        const update = pendingBundleUpdate(
            deployment,
            modelsById[modelId(deployment.key)],
        );
        if (update !== null) updates[deploymentId(deployment.key)] = update;
    }
    return updates;
}
