import "./ModelDashboard.css";
import type { Deployment, Model } from "./ModelsTree";
import {
    registryLabel,
    registryTier,
    servingLabel,
    servingTier,
} from "../phases";

type Props = {
    model: Model;
    deployment: Deployment | null;
    onNavigateToRun: (runName: string) => void;
    onDeploy: (model: Model) => void;
};

function formatSize(bytes: number): string {
    if (bytes <= 0) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = bytes;
    while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i += 1;
    }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatCreatedAt(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function ModelDashboard({
    model,
    deployment,
    onNavigateToRun,
    onDeploy,
}: Props) {
    const servingTierValue = deployment ? servingTier(deployment.phase) : null;
    const regTier = registryTier(model.phase);
    const canDeploy = model.phase === "ready" && deployment === null;
    return (
        <div className="model-dashboard">
            <header className="model-dashboard__header">
                <div className="model-dashboard__title-block">
                    <h1 className="model-dashboard__title">
                        {model.family} / {model.suffix}
                    </h1>
                    <div className="model-dashboard__subtitle">{model.run_name}</div>
                    <div className="model-dashboard__registry">
                        <span
                            className={`model-dashboard__dot model-dashboard__dot--${regTier}`}
                            aria-label={`Registry status: ${registryLabel(model.phase)}`}
                        />
                        <span className="model-dashboard__registry-label">
                            {registryLabel(model.phase)}
                        </span>
                    </div>
                </div>
                <div className="model-dashboard__actions">
                    <button
                        type="button"
                        className="btn"
                        onClick={() => onDeploy(model)}
                        disabled={!canDeploy}
                    >
                        Deploy
                    </button>
                </div>
            </header>
            <section className="model-dashboard__deployment">
                <div className="model-dashboard__deployment-status">
                    {servingTierValue && (
                        <span
                            className={`model-dashboard__dot model-dashboard__dot--${servingTierValue}`}
                            aria-label={`Deployment status: ${deployment ? servingLabel(deployment.phase) : "Not deployed"}`}
                        />
                    )}
                    <span className="model-dashboard__deployment-label">
                        {deployment ? servingLabel(deployment.phase) : "Not deployed"}
                    </span>
                </div>
            </section>
            <dl className="model-dashboard__fields">
                <dt>Family</dt>
                <dd>{model.family}</dd>
                <dt>Variant</dt>
                <dd>{model.suffix}</dd>
                <dt>Run</dt>
                <dd>
                    <button
                        type="button"
                        className="model-dashboard__link"
                        onClick={() => onNavigateToRun(model.run_name)}
                    >
                        {model.run_name}
                    </button>
                </dd>
                <dt>Created</dt>
                <dd>{formatCreatedAt(model.created_at)}</dd>
                <dt>Size</dt>
                <dd>{formatSize(model.size_bytes)}</dd>
                <dt>Storage</dt>
                <dd className="model-dashboard__path">{model.data_blob_path}</dd>
            </dl>
        </div>
    );
}
