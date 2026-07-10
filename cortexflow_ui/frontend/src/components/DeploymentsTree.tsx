import { useMemo } from "react";
import { Box } from "lucide-react";
import "./DeploymentsTree.css";
import { servingTier } from "../phases";
import { deploymentId } from "../ids";
import type { Deployment } from "./ModelsTree";

export type DeploymentSelection = { kind: "deployment"; id: string };

type Props = {
    deployments: Deployment[];
    selection: DeploymentSelection | null;
    onSelect: (selection: DeploymentSelection) => void;
};

export function DeploymentsTree({ deployments, selection, onSelect }: Props) {
    const sorted = useMemo(
        () =>
            [...deployments].sort((a, b) => {
                const f = a.family.localeCompare(b.family);
                if (f !== 0) return f;
                const s = a.suffix.localeCompare(b.suffix);
                return s !== 0 ? s : a.run_name.localeCompare(b.run_name);
            }),
        [deployments],
    );

    return (
        <div className="deployments-tree">
            <div className="deployments-tree__title">
                <span>Deployments</span>
            </div>
            <div className="deployments-tree__list">
                {sorted.length === 0 && (
                    <div className="deployments-tree__status">No deployments</div>
                )}
                {sorted.map((d) => {
                    const id = deploymentId(d);
                    const isSelected = selection?.id === id;
                    const tier = servingTier(d.phase);
                    return (
                        <div
                            key={id}
                            className={
                                isSelected
                                    ? "deployments-tree__row deployments-tree__row--selected"
                                    : "deployments-tree__row"
                            }
                            onClick={() => onSelect({ kind: "deployment", id })}
                        >
                            <Box size={16} className="deployments-tree__icon" />
                            <span className="deployments-tree__label">
                                {d.family}/{d.suffix}
                            </span>
                            <span className="deployments-tree__run">
                                {d.run_name}
                            </span>
                            <span
                                className={`deployments-tree__dot deployments-tree__dot--${tier}`}
                                aria-label={`Deployment status: ${d.phase}`}
                            />
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
