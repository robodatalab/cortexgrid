import type { ModelConfig } from "./components/ModelsTree";

export function describeDeploymentConfig(config: ModelConfig): string {
    return Object.entries(config)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([name, value]) => `${name}=${value}`)
        .join(", ");
}
