import { useMemo } from "react";
import { Box } from "lucide-react";
import "./DeploymentsTree.css";
import { servingTier } from "../phases";
import { deploymentId } from "../ids";
import type { BundleUpdateByDeploymentId } from "../bundleUpdate";
import type { LoadByDeploymentId } from "../deploymentLoad";
import { LoadBars } from "./LoadBars";
import type { Deployment } from "./ModelsTree";

export type DeploymentSelection = { kind: "deployment"; id: string };

type Props = {
    deployments: Deployment[];
    loads: LoadByDeploymentId;
    bundleUpdates: BundleUpdateByDeploymentId;
    selection: DeploymentSelection | null;
    onSelect: (selection: DeploymentSelection) => void;
};

export function DeploymentsTree({
    deployments,
    loads,
    bundleUpdates,
    selection,
    onSelect,
}: Props) {
    const sorted = useMemo(
        () =>
            [...deployments].sort((a, b) =>
                deploymentId(a.key).localeCompare(deploymentId(b.key)),
            ),
        [deployments],
    );

    return (
        <div className="deployments-tree">
            <div className="deployments-tree__title">
                <span>Deployments</span>
            </div>
            <div className="deployments-tree__list">
                {sorted.length > 0 && (
                    <div className="deployments-tree__header">
                        <span>Model</span>
                        <span>Experiment</span>
                    </div>
                )}
                {sorted.length === 0 && (
                    <div className="deployments-tree__status">No deployments</div>
                )}
                {sorted.map((d) => {
                    const id = deploymentId(d.key);
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
                            <span className="deployments-tree__model">
                                <Box size={16} className="deployments-tree__icon" />
                                <span className="deployments-tree__label">
                                    {d.key.family}/{d.key.suffix}
                                </span>
                                {id in bundleUpdates && (
                                    <span
                                        className="deployments-tree__update"
                                        title="The registry holds newer code for this model; redeploy it"
                                    >
                                        update
                                    </span>
                                )}
                            </span>
                            <span
                                className="deployments-tree__experiment"
                                title={d.experiment_name}
                            >
                                {d.experiment_name}
                            </span>
                            <span
                                className={`deployments-tree__dot deployments-tree__dot--${tier}`}
                                aria-label={`Deployment status: ${d.phase}`}
                            />
                            <span className="deployments-tree__load">
                                {id in loads && <LoadBars load={loads[id]} />}
                            </span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
