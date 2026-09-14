// Display helpers for the two model lifecycle vocabularies the backend reports:
// the registry phase (cortexgrid.model_storage.SavedModel.phase) and the serving
// phase (cortexgrid.model_serving.ServingStatus / Deployment.phase). Kept in one
// place so the trees and the detail cards render each phase identically.

export type DotTier = "ok" | "warn" | "error";

// Serving lifecycle: normalized Ray Serve phase.
export function servingTier(phase: string): DotTier {
    if (phase === "failed" || phase === "unhealthy") return "error";
    if (phase === "running") return "ok";
    // not_deployed, not_started, deploying, deleting
    return "warn";
}

export function servingLabel(phase: string): string {
    switch (phase) {
        case "not_deployed":
            return "Not deployed";
        case "not_started":
            return "Not started";
        case "deploying":
            return "Deploying";
        case "running":
            return "Running";
        case "unhealthy":
            return "Unhealthy";
        case "failed":
            return "Deploy failed";
        case "deleting":
            return "Deleting";
        default:
            return phase;
    }
}

// Registry lifecycle: upload/registration phase.
export function registryTier(phase: string): DotTier {
    if (phase === "upload_failed" || phase === "broken") return "error";
    if (phase === "ready") return "ok";
    // uploading
    return "warn";
}

export function registryLabel(phase: string): string {
    switch (phase) {
        case "uploading":
            return "Uploading";
        case "ready":
            return "Ready";
        case "upload_failed":
            return "Upload failed";
        case "broken":
            return "Broken";
        default:
            return phase;
    }
}

export function worstTier(tiers: DotTier[]): DotTier | null {
    if (tiers.includes("error")) return "error";
    if (tiers.includes("warn")) return "warn";
    if (tiers.includes("ok")) return "ok";
    return null;
}
