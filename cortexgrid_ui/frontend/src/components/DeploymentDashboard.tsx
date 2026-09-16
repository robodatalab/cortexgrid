// The serving-side detail card. Reuses the model-dashboard card chrome (same
// visual language as the registry card) but is driven purely by a Deployment.
import { useEffect, useState } from "react";
import "./ModelDashboard.css";
import type { Deployment } from "./ModelsTree";
import { TitledFrame } from "./TitledFrame";
import { deploymentId } from "../ids";
import { servingLabel, servingTier } from "../phases";

// Public ingress hosts on the Tailscale network. Both are also configured in
// k8s/charts/cortexgrid/values.yaml (ray) and k8s/argo_deployments/base/monitoring.yaml
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

// One Ray Serve controller message for the app or one of its deployments
// (cortexgrid.model_serving.ServingMessage).
type ServingMessage = {
    source: string;
    status: string;
    message: string;
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

function messagesUrl(d: Deployment): string {
    return `/api/deployments/${encodeURIComponent(d.family)}/${encodeURIComponent(d.suffix)}/${encodeURIComponent(d.run_name)}/messages`;
}

// Ray's explanation of a failed or unhealthy deployment, fetched when the
// deployment enters an error phase. Results are tagged with the url + phase
// they were fetched for, so a response for another deployment or an earlier
// phase is never shown. null until loaded, and for non-error phases.
function useServingMessages(deployment: Deployment): ServingMessage[] | null {
    const failed = servingTier(deployment.phase) === "error";
    const url = messagesUrl(deployment);
    const key = `${url}:${deployment.phase}`;
    const [loaded, setLoaded] = useState<{
        key: string;
        messages: ServingMessage[];
    } | null>(null);

    useEffect(() => {
        if (!failed) return;
        const controller = new AbortController();
        fetch(url, { signal: controller.signal })
            .then((r) =>
                r.ok ? (r.json() as Promise<ServingMessage[]>) : null,
            )
            .then((messages) => {
                if (messages) setLoaded({ key, messages });
            })
            .catch((err: unknown) => {
                if (err instanceof DOMException && err.name === "AbortError")
                    return;
            });
        return () => controller.abort();
    }, [failed, url, key]);

    return failed && loaded?.key === key ? loaded.messages : null;
}

export function DeploymentDashboard({
    deployment,
    modelInRepository,
    onNavigateToModel,
    onStop,
}: Props) {
    const tier = servingTier(deployment.phase);
    const messages = useServingMessages(deployment);
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
            {messages !== null && (
                <section className="model-dashboard__messages">
                    {messages.length === 0 ? (
                        <div className="model-dashboard__messages-empty">
                            (no messages)
                        </div>
                    ) : (
                        messages.map((m) => (
                            <TitledFrame
                                key={m.source}
                                title={`${m.source} · ${m.status}`}
                            >
                                <pre className="model-dashboard__message">
                                    {m.message}
                                </pre>
                            </TitledFrame>
                        ))
                    )}
                </section>
            )}
        </div>
    );
}
