import { Fragment, useMemo } from "react";
import { Layers, Box, Trash2 } from "lucide-react";
import "./ModelsTree.css";
import { registryTier, worstTier } from "../phases";

// Hardware one replica of the model needs; 0 means no requirement.
export type ModelRequirements = {
    num_gpus: number;
    ram_gb: number;
    vram_gb: number;
};

export type Model = {
    id: string;
    family: string;
    suffix: string;
    run_name: string;
    created_at: string;
    data_blob_path: string;
    size_bytes: number;
    // Registry lifecycle phase: uploading | ready | upload_failed | broken.
    phase: string;
    requirements: ModelRequirements;
};

export type Deployment = {
    family: string;
    suffix: string;
    run_name: string;
    url: string;
    // Normalized serving lifecycle phase (see ServingStatus).
    phase: string;
};

export type ModelSelection = { kind: "model"; id: string };

type Props = {
    models: Model[];
    selection: ModelSelection | null;
    onSelect: (selection: ModelSelection) => void;
    onDeleteModel: (model: Model) => void;
    onDeleteFamily: (family: string) => void;
};

// Registry dots highlight attention-worthy phases only; a "ready" model shows no
// dot so a large, healthy repository stays quiet.
function registryLeafTier(phase: string) {
    return phase === "ready" ? null : registryTier(phase);
}

function rowClass(isAncestor: boolean, isLeaf: boolean): string {
    const parts = ["models-tree__row"];
    if (isLeaf) parts.push("models-tree__row--leaf");
    else if (isAncestor) parts.push("models-tree__row--ancestor");
    return parts.join(" ");
}

export function ModelsTree({
    models,
    selection,
    onSelect,
    onDeleteModel,
    onDeleteFamily,
}: Props) {
    const byFamily = useMemo(() => {
        const map: Record<string, Model[]> = {};
        for (const m of models) {
            (map[m.family] ??= []).push(m);
        }
        for (const list of Object.values(map)) {
            list.sort((a, b) => {
                const s = a.suffix.localeCompare(b.suffix);
                return s !== 0 ? s : a.run_name.localeCompare(b.run_name);
            });
        }
        return map;
    }, [models]);

    const families = useMemo(() => Object.keys(byFamily).sort(), [byFamily]);
    const selectedFamily = selection
        ? (models.find((m) => m.id === selection.id)?.family ?? null)
        : null;

    return (
        <div className="models-tree">
            <div className="models-tree__title">
                <span>Repository</span>
            </div>
            <div className="models-tree__list">
                {families.length === 0 && (
                    <div className="models-tree__status">No models</div>
                )}
                {families.map((family) => {
                    const isFamilyAncestor = selectedFamily === family;
                    const familyTier = worstTier(
                        byFamily[family]
                            .map((m) => registryLeafTier(m.phase))
                            .filter((t): t is Exclude<typeof t, null> => t !== null),
                    );
                    return (
                        <Fragment key={family}>
                            <div
                                className={rowClass(isFamilyAncestor, false)}
                            >
                                <Layers
                                    size={16}
                                    className="models-tree__icon"
                                />
                                <span className="models-tree__label">
                                    {family}
                                </span>
                                {familyTier && (
                                    <span
                                        className={`models-tree__dot models-tree__dot--${familyTier}`}
                                        aria-label={`Family ${family} deployment status: ${familyTier}`}
                                    />
                                )}
                                <button
                                    type="button"
                                    className="models-tree__delete"
                                    aria-label={`Delete family ${family}`}
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        onDeleteFamily(family);
                                    }}
                                >
                                    <Trash2 size={14} />
                                </button>
                            </div>
                            <div className="models-tree__drawer">
                                {byFamily[family].map((m) => {
                                    const isLeaf = selection?.id === m.id;
                                    const leafTier = registryLeafTier(m.phase);
                                    return (
                                        <div
                                            key={m.id}
                                            className={rowClass(false, isLeaf)}
                                            onClick={() =>
                                                onSelect({
                                                    kind: "model",
                                                    id: m.id,
                                                })
                                            }
                                        >
                                            <Box
                                                size={16}
                                                className="models-tree__icon"
                                            />
                                            <span className="models-tree__label">
                                                {m.suffix} · {m.run_name}
                                            </span>
                                            {leafTier && (
                                                <span
                                                    className={`models-tree__dot models-tree__dot--${leafTier}`}
                                                    aria-label={`Registry status: ${m.phase}`}
                                                />
                                            )}
                                            <button
                                                type="button"
                                                className="models-tree__delete"
                                                aria-label={`Delete model ${m.suffix} ${m.run_name}`}
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    onDeleteModel(m);
                                                }}
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    );
                                })}
                            </div>
                        </Fragment>
                    );
                })}
            </div>
        </div>
    );
}
