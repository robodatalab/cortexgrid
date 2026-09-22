import { useEffect, useState } from "react";

export type LoadLevel = "idle" | "moderate" | "busy";

export type LoadByDeploymentId = Record<string, number>;

const LOAD_POLL_INTERVAL_MS = 15_000;

export function loadLevel(load: number): LoadLevel {
    if (load < 0.2) return "idle";
    if (load <= 0.8) return "moderate";
    return "busy";
}

export function useDeploymentLoads(url: string | null): LoadByDeploymentId {
    const [loads, setLoads] = useState<LoadByDeploymentId>({});

    useEffect(() => {
        if (url === null) return;
        const controller = new AbortController();
        const poll = () => {
            fetch(url, { signal: controller.signal })
                .then((r) => (r.ok ? (r.json() as Promise<unknown>) : null))
                .then((body) => {
                    if (isLoadByDeploymentId(body)) setLoads(body);
                })
                .catch(() => undefined);
        };
        poll();
        const id = window.setInterval(poll, LOAD_POLL_INTERVAL_MS);
        return () => {
            controller.abort();
            window.clearInterval(id);
        };
    }, [url]);

    return loads;
}

function isLoadByDeploymentId(body: unknown): body is LoadByDeploymentId {
    return (
        typeof body === "object" &&
        body !== null &&
        !Array.isArray(body) &&
        Object.values(body).every((load) => typeof load === "number")
    );
}
