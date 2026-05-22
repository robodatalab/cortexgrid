import "./ModelDashboard.css";
import type { Deployment, Model } from "./ModelsTree";

type Props = {
    model: Model;
    deployment: Deployment | null;
    onNavigateToRun: (runName: string) => void;
};

// Public ingress hosts on the Tailscale network. Both are also configured in
// k8s/charts/cortexflow/values.yaml (ray) and k8s/argo_deployments/base/monitoring.yaml
// (grafana); keep these in sync if either host changes.
const RAY_DASHBOARD_HOST = "ray.robodatalab.com";
const GRAFANA_HOST = "grafana.robodatalab.com";
const GRAFANA_SERVE_DEPLOYMENT_DASHBOARD_UID = "rayServeDeploymentDashboard";

type DotTier = "ok" | "warn" | "error";

function statusTier(status: string): DotTier {
    if (status === "DEPLOY_FAILED" || status === "UNHEALTHY") return "error";
    if (status === "RUNNING") return "ok";
    return "warn";
}

function appName(model: Model): string {
    return `${model.family}__${model.suffix}__${model.run_name}`;
}

function rayDashboardUrl(model: Model): string {
    return `https://${RAY_DASHBOARD_HOST}/#/serve/applications/${encodeURIComponent(appName(model))}`;
}

function grafanaUrl(model: Model): string {
    const params = new URLSearchParams({ "var-Application": appName(model) });
    return `https://${GRAFANA_HOST}/d/${GRAFANA_SERVE_DEPLOYMENT_DASHBOARD_UID}?${params.toString()}`;
}

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

export function ModelDashboard({ model, deployment, onNavigateToRun }: Props) {
    const tier: DotTier | null = deployment ? statusTier(deployment.status) : null;
    return (
        <div className="model-dashboard">
            <header className="model-dashboard__header">
                <h1 className="model-dashboard__title">
                    {model.family} / {model.suffix}
                </h1>
                <div className="model-dashboard__subtitle">{model.run_name}</div>
            </header>
            <section className="model-dashboard__deployment">
                <div className="model-dashboard__deployment-status">
                    {tier && (
                        <span
                            className={`model-dashboard__dot model-dashboard__dot--${tier}`}
                            aria-label={`Deployment status: ${deployment?.status}`}
                        />
                    )}
                    <span className="model-dashboard__deployment-label">
                        {deployment ? deployment.status : "Not deployed"}
                    </span>
                </div>
                {deployment && (
                    <div className="model-dashboard__deployment-links">
                        <a
                            className="model-dashboard__button"
                            href={rayDashboardUrl(model)}
                            target="_blank"
                            rel="noreferrer"
                        >
                            Open in Ray dashboard
                        </a>
                        <a
                            className="model-dashboard__button"
                            href={grafanaUrl(model)}
                            target="_blank"
                            rel="noreferrer"
                        >
                            Open in Grafana
                        </a>
                    </div>
                )}
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
