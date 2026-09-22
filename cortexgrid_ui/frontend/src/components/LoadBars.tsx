import "./LoadBars.css";
import { loadLevel } from "../deploymentLoad";
import type { LoadLevel } from "../deploymentLoad";

const LIT_BARS_BY_LEVEL: Record<LoadLevel, number> = {
    idle: 1,
    moderate: 2,
    busy: 3,
};

const BARS = [1, 2, 3];

export function LoadBars({ load }: { load: number }) {
    const level = loadLevel(load);
    const litBars = LIT_BARS_BY_LEVEL[level];
    return (
        <span
            className={`load-bars load-bars--${level}`}
            role="img"
            aria-label={`Load: ${level}`}
        >
            {BARS.map((bar) => (
                <span
                    key={bar}
                    className={
                        bar <= litBars
                            ? `load-bars__bar load-bars__bar--${bar} load-bars__bar--lit`
                            : `load-bars__bar load-bars__bar--${bar}`
                    }
                />
            ))}
        </span>
    );
}
