const BYTES_PER_MIB = 1024 * 1024;
const BYTES_PER_GIB = 1024 * BYTES_PER_MIB;

export function formatRate(perSecond: number): string {
    return `${perSecond.toFixed(perSecond < 10 ? 2 : 1)}/s`;
}

export function formatPercent(percent: number): string {
    return `${percent.toFixed(0)}%`;
}

export function formatBytes(bytes: number): string {
    if (bytes >= BYTES_PER_GIB) return `${(bytes / BYTES_PER_GIB).toFixed(1)} GiB`;
    return `${(bytes / BYTES_PER_MIB).toFixed(0)} MiB`;
}

export function formatClockTime(epochSeconds: number): string {
    return new Date(epochSeconds * 1000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
    });
}
