// The serving-side detail card. Reuses the model-dashboard card chrome (same
// visual language as the registry card) but is driven purely by a Deployment.
import "./ModelDashboard.css";
import type { Deployment } from "./ModelsTree";
import { deploymentId } from "../ids";
import { servingLabel, servingTier } from "../phases";

// Public ingress hosts on the Tailscale network. Both are also configured in
// k8s/charts/cortexflow/values.yaml (ray) and k8s/argo_deployments/base/monitoring.yaml
// (grafana); keep these in sync if either host changes.
const RAY_DASHBOARD_HOST = "ray.robodatalab.com";
const GRAFANA_HOST = "grafana.robodatalab.com";
const GRAFANA_SERVE_DEPLOYMENT_DASHBOARD_UID = "rayServeDeploymentDashboard";

type Props = {
    deployment: Deployment;
    // The registry model may have been deleted while still deployed, so the
    // back-link only renders when the model is actually in the repository.
    modelInRepository: boolean;
    onNavigateToModel: (id: string) => void;
    onStop: (deployment: Deployment) => void;
};

function appName(d: Deployment): string {
    return `${d.family}__${d.suffix}__${d.run_name}`;
}

function rayDashboardUrl(d: Deployment): string {
    return `https://${RAY_DASHBOARD_HOST}/#/serve/applications/${encodeURIComponent(appName(d))}`;
}

function grafanaUrl(d: Deployment): string {
    const params = new URLSearchParams({ "var-Application": appName(d) });
    return `https://${GRAFANA_HOST}/d/${GRAFANA_SERVE_DEPLOYMENT_DASHBOARD_UID}?${params.toString()}`;
}

export function DeploymentDashboard({
    deployment,
    modelInRepository,
    onNavigateToModel,
    onStop,
}: Props) {
    const tier = servingTier(deployment.phase);
    return (
        <div className="model-dashboard">
            <header className="model-dashboard__header">
                <div className="model-dashboard__title-block">
                    <h1 className="model-dashboard__title">
                        {deployment.family} / {deployment.suffix}
                    </h1>
                    <div className="model-dashboard__subtitle">
                        {deployment.run_name}
                    </div>
                </div>
                <div className="model-dashboard__actions">
                    <button
                        type="button"
                        className="btn"
                        onClick={() => onStop(deployment)}
                    >
                        Stop
                    </button>
                </div>
            </header>
            <section className="model-dashboard__deployment">
                <div className="model-dashboard__deployment-status">
                    <span
                        className={`model-dashboard__dot model-dashboard__dot--${tier}`}
                        aria-label={`Deployment status: ${servingLabel(deployment.phase)}`}
                    />
                    <span className="model-dashboard__deployment-label">
                        {servingLabel(deployment.phase)}
                    </span>
                </div>
                <div className="model-dashboard__deployment-links">
                    <a
                        className="model-dashboard__button"
                        href={rayDashboardUrl(deployment)}
                        target="_blank"
                        rel="noreferrer"
                    >
                        Open in Ray dashboard
                    </a>
                    <a
                        className="model-dashboard__button"
                        href={grafanaUrl(deployment)}
                        target="_blank"
                        rel="noreferrer"
                    >
                        Open in Grafana
                    </a>
                </div>
            </section>
            <dl className="model-dashboard__fields">
                <dt>Registry</dt>
                <dd>
                    {modelInRepository ? (
                        <button
                            type="button"
                            className="model-dashboard__link"
                            onClick={() =>
                                onNavigateToModel(deploymentId(deployment))
                            }
                        >
                            View in repository
                        </button>
                    ) : (
                        "Not in repository"
                    )}
                </dd>
                <dt>Family</dt>
                <dd>{deployment.family}</dd>
                <dt>Variant</dt>
                <dd>{deployment.suffix}</dd>
                <dt>Run</dt>
                <dd>{deployment.run_name}</dd>
                <dt>URL</dt>
                <dd className="model-dashboard__path">{deployment.url}</dd>
            </dl>
        </div>
    );
}
