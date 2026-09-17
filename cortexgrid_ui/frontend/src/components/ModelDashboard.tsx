import { useEffect, useState } from "react";
import "./ModelDashboard.css";
import type { Deployment, Model, ModelRequirements } from "./ModelsTree";
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
    onNavigateToDeployment: (id: string) => void;
    onDeploy: (model: Model) => void;
    onSaveRequirements: (
        model: Model,
        requirements: ModelRequirements,
    ) => Promise<void>;
};

// What the user is typing, before it is a number: an input cleared mid-edit
// holds "", which is not 0.
type Draft = { num_gpus: string; ram_gb: string; vram_gb: string };

function toDraft(requirements: ModelRequirements): Draft {
    return {
        num_gpus: String(requirements.num_gpus),
        ram_gb: String(requirements.ram_gb),
        vram_gb: String(requirements.vram_gb),
    };
}

function parseDraft(draft: Draft): ModelRequirements | null {
    const values = {} as ModelRequirements;
    for (const [field, raw] of Object.entries(draft) as [keyof Draft, string][]) {
        const value = Number(raw);
        if (raw.trim() === "" || !Number.isFinite(value) || value < 0) return null;
        values[field] = value;
    }
    return values;
}

// The same rules `cortexgrid.ModelRequirements` enforces, checked here so the
// dashboard explains the problem instead of the save failing.
function problemWith(requirements: ModelRequirements | null): string | null {
    if (requirements === null) return "Requirements must be zero or more.";
    if (!Number.isInteger(requirements.num_gpus)) {
        return "GPUs must be a whole number.";
    }
    if (requirements.vram_gb > 0 && requirements.num_gpus === 0) {
        return "VRAM needs a GPU: set GPUs to at least 1.";
    }
    return null;
}

function isStored(
    requirements: ModelRequirements | null,
    stored: ModelRequirements,
): boolean {
    return (
        requirements !== null &&
        requirements.num_gpus === stored.num_gpus &&
        requirements.ram_gb === stored.ram_gb &&
        requirements.vram_gb === stored.vram_gb
    );
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

export function ModelDashboard({
    model,
    deployment,
    onNavigateToRun,
    onNavigateToDeployment,
    onDeploy,
    onSaveRequirements,
}: Props) {
    const servingTierValue = deployment ? servingTier(deployment.phase) : null;
    const regTier = registryTier(model.phase);
    const canDeploy = model.phase === "ready" && deployment === null;
    const stored = model.requirements;
    const [draft, setDraft] = useState<Draft>(() => toDraft(stored));
    const [saving, setSaving] = useState(false);
    const [failure, setFailure] = useState<string | null>(null);
    // Follow the model the card is showing, and the values a save (here or in
    // another dashboard) put in the stream. Kept as the three numbers rather
    // than the object, which the stream hands us anew on every poll.
    const { num_gpus: storedGpus, ram_gb: storedRam, vram_gb: storedVram } = stored;
    useEffect(() => {
        setDraft({
            num_gpus: String(storedGpus),
            ram_gb: String(storedRam),
            vram_gb: String(storedVram),
        });
        setFailure(null);
    }, [model.id, storedGpus, storedRam, storedVram]);

    const edited = parseDraft(draft);
    const problem = problemWith(edited);
    const canSave = edited !== null && problem === null && !isStored(edited, stored);

    async function save() {
        if (edited === null) return;
        setSaving(true);
        setFailure(null);
        try {
            await onSaveRequirements(model, edited);
        } catch (err: unknown) {
            setFailure(err instanceof Error ? err.message : String(err));
        } finally {
            setSaving(false);
        }
    }

    function field(name: keyof Draft, label: string, step: string) {
        return (
            <label className="model-dashboard__requirement">
                <span className="model-dashboard__requirement-label">{label}</span>
                <input
                    type="number"
                    min="0"
                    step={step}
                    value={draft[name]}
                    onChange={(e) =>
                        setDraft({ ...draft, [name]: e.target.value })
                    }
                />
            </label>
        );
    }
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
                    {deployment && (
                        <button
                            type="button"
                            className="model-dashboard__link"
                            onClick={() => onNavigateToDeployment(model.id)}
                        >
                            View deployment
                        </button>
                    )}
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
            <section className="model-dashboard__requirements">
                <h2 className="model-dashboard__section-title">Requirements</h2>
                <p className="model-dashboard__hint">
                    What one replica needs to be served. A model is deployed only
                    on a host that has it free; 0 means no requirement.
                </p>
                <div className="model-dashboard__requirement-fields">
                    {field("num_gpus", "GPUs", "1")}
                    {field("ram_gb", "RAM (GiB)", "0.5")}
                    {field("vram_gb", "VRAM (GiB)", "0.5")}
                    <button
                        type="button"
                        className="btn"
                        onClick={save}
                        disabled={!canSave || saving}
                    >
                        Save
                    </button>
                </div>
                {(problem || failure) && (
                    <div className="model-dashboard__requirement-problem" role="alert">
                        {problem ?? failure}
                    </div>
                )}
            </section>
        </div>
    );
}
