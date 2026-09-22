// The serving-side detail card. Reuses the model-dashboard card chrome (same
// visual language as the registry card) but is driven purely by a Deployment.
import { useEffect, useState } from "react";
import "./ModelDashboard.css";
import { DeploymentUsage } from "./DeploymentUsage";
import { DeviceCard, type PodStatus } from "./DeviceCard";
import type { Deployment } from "./ModelsTree";
import { TitledFrame } from "./TitledFrame";
import { shortFingerprint, type BundleUpdate } from "../bundleUpdate";
import { describeDeploymentConfig } from "../deploymentConfig";
import { deploymentApiUrl, modelId, serveApplicationName } from "../ids";
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
    bundleUpdate: BundleUpdate | null;
    onNavigateToModel: (id: string) => void;
    onStop: (deployment: Deployment) => void;
    onRedeploy: (deployment: Deployment) => Promise<void>;
};

// One Ray Serve controller message for the app or one of its deployments
// (cortexgrid.model_serving.ServingMessage).
type ServingMessage = {
    source: string;
    status: string;
    message: string;
};

function rayDashboardUrl(d: Deployment): string {
    return `https://${RAY_DASHBOARD_HOST}/#/serve/applications/${encodeURIComponent(serveApplicationName(d.key))}`;
}

function grafanaUrl(d: Deployment): string {
    const params = new URLSearchParams({
        "var-Application": serveApplicationName(d.key),
    });
    return `https://${GRAFANA_HOST}/d/${GRAFANA_SERVE_DEPLOYMENT_DASHBOARD_UID}?${params.toString()}`;
}

// One replica and the machine serving it (backend ReplicaDevice). `device` is
// null while Ray has yet to place the replica, and for a worker kubernetes no
// longer knows.
type ReplicaDevice = {
    replica_id: string;
    state: string;
    node_ip: string | null;
    device: PodStatus | null;
};

// Which machines the deployment's replicas landed on, repolled while it is
// live: replicas move as they restart, and a device's health changes under
// them. Results are tagged with the url they were fetched for, so a response
// for a deployment the user has navigated away from is never shown.
function useReplicaDevices(deployment: Deployment): ReplicaDevice[] | null {
    const url = deploymentApiUrl(deployment.key, "devices");
    const [loaded, setLoaded] = useState<{
        url: string;
        devices: ReplicaDevice[];
    } | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        const poll = () => {
            fetch(url, { signal: controller.signal })
                .then((r) => (r.ok ? (r.json() as Promise<ReplicaDevice[]>) : null))
                .then((devices) => {
                    if (devices) setLoaded({ url, devices });
                })
                .catch((err: unknown) => {
                    if (err instanceof DOMException && err.name === "AbortError")
                        return;
                });
        };
        poll();
        const id = window.setInterval(poll, 10_000);
        return () => {
            controller.abort();
            window.clearInterval(id);
        };
    }, [url]);

    return loaded?.url === url ? loaded.devices : null;
}

// Ray's explanation of a failed or unhealthy deployment, fetched when the
// deployment enters an error phase. Results are tagged with the url + phase
// they were fetched for, so a response for another deployment or an earlier
// phase is never shown. null until loaded, and for non-error phases.
function useServingMessages(deployment: Deployment): ServingMessage[] | null {
    const failed = servingTier(deployment.phase) === "error";
    const url = deploymentApiUrl(deployment.key, "messages");
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

function BundleUpdateNotice({
    update,
    onRedeploy,
}: {
    update: BundleUpdate;
    onRedeploy: () => Promise<void>;
}) {
    const [redeploying, setRedeploying] = useState(false);
    const registryHoldsSignedCode = update.registeredFingerprint !== "";
    const redeploy = () => {
        setRedeploying(true);
        onRedeploy().finally(() => setRedeploying(false));
    };
    return (
        <section className="model-dashboard__update" role="status">
            <div className="model-dashboard__update-message">
                <div className="model-dashboard__update-title">
                    {registryHoldsSignedCode ? "Update available" : "May be outdated"}
                </div>
                <p className="model-dashboard__update-text">
                    {registryHoldsSignedCode ? (
                        <>
                            This endpoint runs code{" "}
                            <code>{shortFingerprint(update.deployedFingerprint)}</code>,
                            the registry holds{" "}
                            <code>{shortFingerprint(update.registeredFingerprint)}</code>.
                            Redeploy the model to run the registry's code.
                        </>
                    ) : (
                        <>
                            This model's code was saved before code was signed, so
                            there is no telling whether it is current. Re-import the
                            model, then redeploy it.
                        </>
                    )}
                </p>
            </div>
            {registryHoldsSignedCode && (
                <button
                    type="button"
                    className="btn"
                    disabled={redeploying}
                    onClick={redeploy}
                >
                    {redeploying ? "Redeploying…" : "Redeploy"}
                </button>
            )}
        </section>
    );
}

function RolloutNotice({ deployment }: { deployment: Deployment }) {
    return (
        <section className="model-dashboard__update" role="status">
            <div className="model-dashboard__update-message">
                <div className="model-dashboard__update-title">Redeploying</div>
                <p className="model-dashboard__update-text">
                    This endpoint is moving from code{" "}
                    <code>{shortFingerprint(deployment.replaced_bundle_fingerprint)}</code>{" "}
                    to <code>{shortFingerprint(deployment.bundle_fingerprint)}</code>.
                </p>
            </div>
        </section>
    );
}

function deployedCode(deployment: Deployment): string {
    const current = shortFingerprint(deployment.bundle_fingerprint);
    return deployment.replaced_bundle_fingerprint === ""
        ? current
        : `${shortFingerprint(deployment.replaced_bundle_fingerprint)} → ${current}`;
}

export function DeploymentDashboard({
    deployment,
    modelInRepository,
    bundleUpdate,
    onNavigateToModel,
    onStop,
    onRedeploy,
}: Props) {
    const tier = servingTier(deployment.phase);
    const messages = useServingMessages(deployment);
    const devices = useReplicaDevices(deployment);
    return (
        <div className="model-dashboard">
            <header className="model-dashboard__header">
                <div className="model-dashboard__title-block">
                    <h1 className="model-dashboard__title">
                        {deployment.key.family} / {deployment.key.suffix}
                    </h1>
                    <div className="model-dashboard__subtitle">
                        {deployment.key.run_name}
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
            {deployment.replaced_bundle_fingerprint !== "" && (
                <RolloutNotice deployment={deployment} />
            )}
            {bundleUpdate !== null && (
                <BundleUpdateNotice
                    update={bundleUpdate}
                    onRedeploy={() => onRedeploy(deployment)}
                />
            )}
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
                                onNavigateToModel(modelId(deployment.key))
                            }
                        >
                            View in repository
                        </button>
                    ) : (
                        "Not in repository"
                    )}
                </dd>
                <dt>Family</dt>
                <dd>{deployment.key.family}</dd>
                <dt>Variant</dt>
                <dd>{deployment.key.suffix}</dd>
                <dt>Run</dt>
                <dd>{deployment.key.run_name}</dd>
                <dt>Config</dt>
                <dd className="model-dashboard__path">
                    {describeDeploymentConfig(deployment.config) || "the model's own"}
                </dd>
                <dt>Code</dt>
                <dd
                    className="model-dashboard__path"
                    title={deployment.bundle_fingerprint}
                >
                    {deployedCode(deployment)}
                </dd>
                <dt>URL</dt>
                <dd className="model-dashboard__path">{deployment.url}</dd>
            </dl>
            <DeploymentUsage deployment={deployment} />
            <section className="model-dashboard__devices">
                <h2 className="model-dashboard__section-title">Devices</h2>
                <p className="model-dashboard__hint">
                    Where this deployment's replicas are running. A model is
                    placed on the smallest GPU that fits it, so this is the card
                    its requirements actually resolved to.
                </p>
                {devices === null ? (
                    <div className="model-dashboard__devices-empty">Loading…</div>
                ) : devices.length === 0 ? (
                    <div className="model-dashboard__devices-empty">
                        No replica is running yet.
                    </div>
                ) : (
                    <div className="model-dashboard__devices-grid">
                        {devices.map((replica) =>
                            replica.device ? (
                                <DeviceCard
                                    key={replica.replica_id}
                                    pod={replica.device}
                                    title={replica.device.node ?? replica.device.name}
                                    leading={<span>replica: {replica.state}</span>}
                                />
                            ) : (
                                <TitledFrame
                                    key={replica.replica_id}
                                    title={replica.node_ip ?? "Not placed"}
                                >
                                    <div className="infra-card__meta">
                                        <span>replica: {replica.state}</span>
                                        <span>
                                            {replica.node_ip
                                                ? "host not found in the cluster"
                                                : "awaiting a device"}
                                        </span>
                                    </div>
                                </TitledFrame>
                            ),
                        )}
                    </div>
                )}
            </section>
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
