import { Fragment, useMemo } from "react";
import { Layers, Box } from "lucide-react";
import "./ModelsTree.css";

export type Model = {
    id: string;
    family: string;
    suffix: string;
    run_name: string;
    created_at: string;
    data_blob_path: string;
    size_bytes: number;
};

export type ModelSelection = { kind: "model"; id: string };

type Props = {
    models: Model[];
    selection: ModelSelection | null;
    onSelect: (selection: ModelSelection) => void;
};

function rowClass(isAncestor: boolean, isLeaf: boolean): string {
    const parts = ["models-tree__row"];
    if (isLeaf) parts.push("models-tree__row--leaf");
    else if (isAncestor) parts.push("models-tree__row--ancestor");
    return parts.join(" ");
}

export function ModelsTree({ models, selection, onSelect }: Props) {
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
                <span>Models</span>
            </div>
            <div className="models-tree__list">
                {families.length === 0 && (
                    <div className="models-tree__status">No models</div>
                )}
                {families.map((family) => {
                    const isFamilyAncestor = selectedFamily === family;
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
                            </div>
                            <div className="models-tree__drawer">
                                {byFamily[family].map((m) => {
                                    const isLeaf = selection?.id === m.id;
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
