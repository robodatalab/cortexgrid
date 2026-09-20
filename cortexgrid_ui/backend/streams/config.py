"""Backend configuration constants."""
from __future__ import annotations

EXPERIMENTS_STREAM_POLL_INTERVAL_SEC = 10
RUN_JOBS_STREAM_POLL_INTERVAL_SEC = 10
# One lifecycle read per job in the cluster, so slower than the per-run sweep.
JOBS_STREAM_POLL_INTERVAL_SEC = 30
RUN_DASHBOARD_STREAM_POLL_INTERVAL_SEC = 10
JOB_STREAM_POLL_INTERVAL_SEC = 10
NOTES_STREAM_POLL_INTERVAL_SEC = 30
