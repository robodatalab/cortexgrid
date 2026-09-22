import { useEffect, useState } from "react";
import {
    CartesianGrid,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import "./DeploymentUsage.css";
import type { Deployment } from "./ModelsTree";
import {
    formatBytes,
    formatClockTime,
    formatPercent,
    formatRate,
} from "../usageFormat";

type TimeSeries = [number, number][];

export type DeploymentMetrics = {
    requests_per_second: TimeSeries;
    succeeded_responses_per_second: TimeSeries;
    failed_responses_per_second: TimeSeries;
    cpu_percent: TimeSeries;
    memory_bytes: TimeSeries;
    gpu_percent: TimeSeries;
    gpu_memory_bytes: TimeSeries;
};

type Series = {
    name: string;
    color: string;
    points: TimeSeries;
};

const USAGE_POLL_INTERVAL_MS = 30_000;

const METRIC_NAMES: (keyof DeploymentMetrics)[] = [
    "requests_per_second",
    "succeeded_responses_per_second",
    "failed_responses_per_second",
    "cpu_percent",
    "memory_bytes",
    "gpu_percent",
    "gpu_memory_bytes",
];

const FIRST_SERIES_COLOR = "#2a78d6";
const SECOND_SERIES_COLOR = "#eb6834";
const GRID_COLOR = "#e6e6e4";
const AXIS_TEXT_COLOR = "#52514e";

function metricsUrl(d: Deployment): string {
    return `/api/deployments/${encodeURIComponent(d.family)}/${encodeURIComponent(d.suffix)}/${encodeURIComponent(d.run_name)}/metrics`;
}

function useDeploymentMetrics(deployment: Deployment): DeploymentMetrics | null {
    const url = metricsUrl(deployment);
    const [loaded, setLoaded] = useState<{
        url: string;
        metrics: DeploymentMetrics;
    } | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        const poll = () => {
            fetch(url, { signal: controller.signal })
                .then((r) => (r.ok ? (r.json() as Promise<unknown>) : null))
                .then((body) => {
                    if (isDeploymentMetrics(body)) setLoaded({ url, metrics: body });
                })
                .catch(() => undefined);
        };
        poll();
        const id = window.setInterval(poll, USAGE_POLL_INTERVAL_MS);
        return () => {
            controller.abort();
            window.clearInterval(id);
        };
    }, [url]);

    return loaded?.url === url ? loaded.metrics : null;
}

function isDeploymentMetrics(body: unknown): body is DeploymentMetrics {
    return (
        typeof body === "object" &&
        body !== null &&
        METRIC_NAMES.every((name) =>
            Array.isArray((body as Record<string, unknown>)[name]),
        )
    );
}

function mergedByTimestamp(series: Series[]): Record<string, number>[] {
    const rowByTimestamp = new Map<number, Record<string, number>>();
    for (const { name, points } of series) {
        for (const [timestamp, value] of points) {
            const row = rowByTimestamp.get(timestamp) ?? { timestamp };
            row[name] = value;
            rowByTimestamp.set(timestamp, row);
        }
    }
    return [...rowByTimestamp.values()].sort((a, b) => a.timestamp - b.timestamp);
}

function latestValue(points: TimeSeries): number | null {
    return points.length === 0 ? null : points[points.length - 1][1];
}

function UsageChart({
    title,
    series,
    format,
}: {
    title: string;
    series: Series[];
    format: (value: number) => string;
}) {
    const reported = series.some((s) => s.points.length > 0);
    return (
        <figure className="deployment-usage__chart">
            <figcaption className="deployment-usage__chart-header">
                <span className="deployment-usage__chart-title">{title}</span>
                {reported && (
                    <span className="deployment-usage__latest">
                        {series.map((s) => {
                            const latest = latestValue(s.points);
                            return (
                                <span key={s.name} className="deployment-usage__latest-value">
                                    {series.length > 1 && (
                                        <span
                                            className="deployment-usage__swatch"
                                            style={{ background: s.color }}
                                        />
                                    )}
                                    {series.length > 1 ? `${s.name} ` : ""}
                                    {latest === null ? "—" : format(latest)}
                                </span>
                            );
                        })}
                    </span>
                )}
            </figcaption>
            {reported ? (
                <ResponsiveContainer width="100%" height={140}>
                    <LineChart
                        data={mergedByTimestamp(series)}
                        margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                    >
                        <CartesianGrid vertical={false} stroke={GRID_COLOR} />
                        <XAxis
                            dataKey="timestamp"
                            type="number"
                            domain={["dataMin", "dataMax"]}
                            tickFormatter={formatClockTime}
                            tick={{ fontSize: 11, fill: AXIS_TEXT_COLOR }}
                            stroke={GRID_COLOR}
                        />
                        <YAxis
                            tickFormatter={format}
                            tick={{ fontSize: 11, fill: AXIS_TEXT_COLOR }}
                            stroke={GRID_COLOR}
                            width={64}
                        />
                        <Tooltip
                            wrapperStyle={{ fontSize: 11 }}
                            labelFormatter={(timestamp) => formatClockTime(Number(timestamp))}
                            formatter={(value) => format(Number(value))}
                        />
                        {series.map((s) => (
                            <Line
                                key={s.name}
                                dataKey={s.name}
                                name={s.name}
                                stroke={s.color}
                                strokeWidth={2}
                                strokeLinejoin="round"
                                strokeLinecap="round"
                                dot={false}
                                isAnimationActive={false}
                            />
                        ))}
                    </LineChart>
                </ResponsiveContainer>
            ) : (
                <div className="deployment-usage__not-reported">Not reported</div>
            )}
        </figure>
    );
}

export function DeploymentUsageCharts({ metrics }: { metrics: DeploymentMetrics }) {
    const single = (name: string, points: TimeSeries): Series[] => [
        { name, color: FIRST_SERIES_COLOR, points },
    ];
    return (
        <div className="deployment-usage__grid">
            <UsageChart
                title="Requests"
                series={single("requests", metrics.requests_per_second)}
                format={formatRate}
            />
            <UsageChart
                title="Responses"
                series={[
                    {
                        name: "succeeded",
                        color: FIRST_SERIES_COLOR,
                        points: metrics.succeeded_responses_per_second,
                    },
                    {
                        name: "failed",
                        color: SECOND_SERIES_COLOR,
                        points: metrics.failed_responses_per_second,
                    },
                ]}
                format={formatRate}
            />
            <UsageChart
                title="CPU"
                series={single("CPU", metrics.cpu_percent)}
                format={formatPercent}
            />
            <UsageChart
                title="Memory"
                series={single("memory", metrics.memory_bytes)}
                format={formatBytes}
            />
            <UsageChart
                title="GPU"
                series={single("GPU", metrics.gpu_percent)}
                format={formatPercent}
            />
            <UsageChart
                title="GPU memory"
                series={single("GPU memory", metrics.gpu_memory_bytes)}
                format={formatBytes}
            />
        </div>
    );
}

export function DeploymentUsage({ deployment }: { deployment: Deployment }) {
    const metrics = useDeploymentMetrics(deployment);
    return (
        <section className="deployment-usage">
            <h2 className="model-dashboard__section-title">Usage</h2>
            <p className="model-dashboard__hint">
                The last hour, summed over this deployment's replicas.
            </p>
            {metrics === null ? (
                <div className="deployment-usage__not-reported">Loading…</div>
            ) : (
                <DeploymentUsageCharts metrics={metrics} />
            )}
        </section>
    );
}
