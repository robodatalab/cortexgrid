import { useEffect, useState } from "react";
import "./InfraStatusIndicator.css";

type Status = "loading" | "ok" | "bad";

const TOOLTIPS: Record<Status, string> = {
    loading: "Infra: Loading…",
    ok: "Infra: OK",
    bad: "Infra: Error",
};

type Props = {
    onClick: () => void;
};

const POLL_INTERVAL_MS = 10_000;

export function InfraStatusIndicator({ onClick }: Props) {
    const [status, setStatus] = useState<Status>("loading");

    useEffect(() => {
        const controller = new AbortController();
        const poll = () => {
            fetch("/api/infra/status", { signal: controller.signal })
                .then((res) =>
                    res.ok
                        ? res.json()
                        : Promise.reject(new Error(`HTTP ${res.status}`)),
                )
                .then((data: { overall: boolean }) =>
                    setStatus(data.overall ? "ok" : "bad"),
                )
                .catch((err: unknown) => {
                    if (
                        err instanceof DOMException &&
                        err.name === "AbortError"
                    )
                        return;
                    setStatus("bad");
                });
        };
        poll();
        const id = window.setInterval(poll, POLL_INTERVAL_MS);
        return () => {
            controller.abort();
            window.clearInterval(id);
        };
    }, []);

    return (
        <button
            type="button"
            className={`infra-indicator infra-indicator--${status}`}
            onClick={onClick}
            aria-label={TOOLTIPS[status]}
            data-tooltip={TOOLTIPS[status]}
        />
    );
}
