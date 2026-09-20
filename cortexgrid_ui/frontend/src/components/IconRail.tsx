import { FlaskConical, ListChecks, Box, KeyRound } from "lucide-react";
import { BrandIcon } from "./BrandIcon";
import { InfraStatusIndicator } from "./InfraStatusIndicator";
import "./IconRail.css";

export type RailView =
    | "experiments"
    | "jobs"
    | "models"
    | "secrets"
    | "infra";

type Dashboard = {
    id: string;
    url: string;
};

type Props = {
    view: RailView;
    onSelect: (view: RailView) => void;
    dashboards: Dashboard[];
};

type RailButton = {
    view: RailView;
    label: string;
    icon: React.ReactNode;
};

const VIEW_BUTTONS: RailButton[] = [
    {
        view: "experiments",
        label: "Experiments",
        icon: <FlaskConical size={20} />,
    },
    { view: "jobs", label: "Jobs", icon: <ListChecks size={20} /> },
    { view: "models", label: "Models", icon: <Box size={20} /> },
    { view: "secrets", label: "Secrets", icon: <KeyRound size={20} /> },
];

export function IconRail({ view, onSelect, dashboards }: Props) {
    return (
        <nav className="icon-rail" aria-label="Primary">
            {VIEW_BUTTONS.map((b) => (
                <button
                    key={b.view}
                    type="button"
                    className={`icon-rail__btn${view === b.view ? " icon-rail__btn--active" : ""}`}
                    onClick={() => onSelect(b.view)}
                    aria-label={b.label}
                    data-tooltip={b.label}
                >
                    {b.icon}
                </button>
            ))}
            <div
                className={`icon-rail__slot${view === "infra" ? " icon-rail__slot--active" : ""}`}
            >
                <InfraStatusIndicator onClick={() => onSelect("infra")} />
            </div>
            <div className="icon-rail__divider" />
            {dashboards.map((d) => (
                <a
                    key={d.id}
                    className="icon-rail__btn"
                    href={d.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={d.id}
                    data-tooltip={d.id}
                >
                    <BrandIcon slug={d.id} size={20} />
                </a>
            ))}
        </nav>
    );
}
