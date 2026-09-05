#!/usr/bin/env python3
"""Passive anomaly detection for Orbinum Watcher telemetry.

Consumes rows from the local Windows telemetry SQLite database or from JSONL.
Read-only: it never touches the validator, Docker state, tunnels, or networking.

The detector is intentionally conservative. It looks for sustained conditions
rather than single noisy samples so normal validator jitter does not become an
incident.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB = Path(r"C:\ProgramData\OrbinumWatcher\telemetry.db")


@dataclass(frozen=True)
class Thresholds:
    cpu_high_pct: float = 85.0
    cpu_high_seconds: int = 20
    host_cpu_high_pct: float = 90.0
    host_cpu_high_seconds: int = 30
    finality_gap_blocks: int = 6
    finality_gap_seconds: int = 15
    sync_gap_blocks: int = 2
    sync_gap_seconds: int = 15
    peer_low_count: int = 10
    peer_low_seconds: int = 20
    peer_drop_fraction: float = 0.40
    peer_drop_window_seconds: int = 30
    block_stall_seconds: int = 30
    finality_stall_seconds: int = 45
    metrics_latency_ms: int = 1000
    metrics_latency_seconds: int = 20
    container_mem_pct: float = 80.0
    container_mem_seconds: int = 30
    docker_engine_down_seconds: int = 10


@dataclass
class Event:
    kind: str
    severity: str
    started: int
    ended: int
    duration_s: int
    summary: str
    peak: float | int | None = None


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _docker_engine_unavailable(row: dict[str, Any]) -> bool:
    """Recognize Docker Desktop/engine transport failure without confusing it
    with a missing validator container.

    windows_telemetry records both docker stats and docker inspect failures in
    one error string. A stopped Docker Desktop / WSL backend usually surfaces as
    a named-pipe, daemon-connect or engine API failure. A plain "no such
    container" is intentionally excluded because that is a different fault.
    """
    error = str(row.get("error") or "").lower()
    if not error or "docker stats:" not in error or "docker inspect:" not in error:
        return False
    if "no such container" in error or "no such object" in error:
        return False

    markers = (
        "dockerdesktoplinuxengine",
        "docker_engine",
        "cannot connect to the docker daemon",
        "is the docker daemon running",
        "error during connect",
        "the system cannot find the file specified",
        "open //./pipe/",
        "open \\\\.\\pipe\\",
        "request returned internal server error",
        "docker desktop",
    )
    return any(marker in error for marker in markers)


def _duration_true(rows: list[dict[str, Any]], predicate) -> list[tuple[int, int, list[dict[str, Any]]]]:
    out: list[tuple[int, int, list[dict[str, Any]]]] = []
    cur: list[dict[str, Any]] = []
    for row in rows:
        if predicate(row):
            cur.append(row)
        elif cur:
            out.append((int(cur[0]["ts"]), int(cur[-1]["ts"]), cur[:]))
            cur.clear()
    if cur:
        out.append((int(cur[0]["ts"]), int(cur[-1]["ts"]), cur[:]))
    return out


def _emit_sustained(
    events: list[Event],
    rows: list[dict[str, Any]],
    predicate,
    min_seconds: int,
    kind: str,
    severity: str,
    summary_fn,
    peak_fn=None,
) -> None:
    for start, end, seg in _duration_true(rows, predicate):
        duration = max(0, end - start)
        if duration >= min_seconds:
            events.append(Event(
                kind=kind,
                severity=severity,
                started=start,
                ended=end,
                duration_s=duration,
                summary=summary_fn(seg),
                peak=peak_fn(seg) if peak_fn else None,
            ))


def detect(rows: Iterable[dict[str, Any]], thresholds: Thresholds | None = None) -> list[Event]:
    t = thresholds or Thresholds()
    data = sorted((dict(r) for r in rows if r.get("ts") is not None), key=lambda r: int(r["ts"]))
    if not data:
        return []

    events: list[Event] = []

    # Docker Desktop / WSL backend failure is more useful than the generic
    # telemetry_error it also causes. Keep both signals so event clustering can
    # retain all evidence while the presentation layer chooses the root cause.
    _emit_sustained(
        events,
        data,
        _docker_engine_unavailable,
        t.docker_engine_down_seconds,
        "docker_engine_unavailable",
        "critical",
        lambda seg: "Docker Engine unavailable to Windows telemetry",
    )

    # Any metrics error is kept as a short event; it is useful during real load
    # because the metrics endpoint may become slow/unavailable before the node dies.
    _emit_sustained(
        events,
        data,
        lambda r: bool(r.get("error")),
        0,
        "telemetry_error",
        "warning",
        lambda seg: str(seg[-1].get("error") or "telemetry error"),
    )

    _emit_sustained(
        events,
        data,
        lambda r: r.get("container_running") == 0 or str(r.get("container_status") or "").lower() not in {"", "running"},
        0,
        "container_down",
        "critical",
        lambda seg: f"validator container not running ({seg[-1].get('container_status')})",
    )

    # Restart counter increase is definitive and does not need a sustained window.
    prev_restarts: int | None = None
    for row in data:
        cur = row.get("container_restarts")
        if isinstance(cur, int):
            if prev_restarts is not None and cur > prev_restarts:
                events.append(Event(
                    kind="container_restart",
                    severity="critical",
                    started=int(row["ts"]),
                    ended=int(row["ts"]),
                    duration_s=0,
                    summary=f"container restart counter increased {prev_restarts} -> {cur}",
                    peak=cur,
                ))
            prev_restarts = cur

    _emit_sustained(
        events,
        data,
        lambda r: (_num(r.get("container_cpu_pct")) or 0) >= t.cpu_high_pct,
        t.cpu_high_seconds,
        "container_cpu_high",
        "warning",
        lambda seg: f"validator CPU >= {t.cpu_high_pct:.0f}% for {seg[-1]['ts'] - seg[0]['ts']}s",
        lambda seg: max((_num(r.get("container_cpu_pct")) or 0) for r in seg),
    )

    _emit_sustained(
        events,
        data,
        lambda r: (_num(r.get("host_cpu_pct")) or 0) >= t.host_cpu_high_pct,
        t.host_cpu_high_seconds,
        "host_cpu_high",
        "warning",
        lambda seg: f"host CPU >= {t.host_cpu_high_pct:.0f}% for {seg[-1]['ts'] - seg[0]['ts']}s",
        lambda seg: max((_num(r.get("host_cpu_pct")) or 0) for r in seg),
    )

    _emit_sustained(
        events,
        data,
        lambda r: (_num(r.get("container_mem_pct")) or 0) >= t.container_mem_pct,
        t.container_mem_seconds,
        "container_memory_high",
        "warning",
        lambda seg: f"validator memory >= {t.container_mem_pct:.0f}% for {seg[-1]['ts'] - seg[0]['ts']}s",
        lambda seg: max((_num(r.get("container_mem_pct")) or 0) for r in seg),
    )

    _emit_sustained(
        events,
        data,
        lambda r: isinstance(r.get("finality_gap"), int) and r["finality_gap"] >= t.finality_gap_blocks,
        t.finality_gap_seconds,
        "finality_gap",
        "warning",
        lambda seg: f"finality gap >= {t.finality_gap_blocks} blocks",
        lambda seg: max(int(r.get("finality_gap") or 0) for r in seg),
    )

    _emit_sustained(
        events,
        data,
        lambda r: isinstance(r.get("sync_gap"), int) and r["sync_gap"] >= t.sync_gap_blocks,
        t.sync_gap_seconds,
        "sync_lag",
        "critical",
        lambda seg: f"sync gap >= {t.sync_gap_blocks} blocks",
        lambda seg: max(int(r.get("sync_gap") or 0) for r in seg),
    )

    _emit_sustained(
        events,
        data,
        lambda r: isinstance(r.get("peers"), int) and r["peers"] <= t.peer_low_count,
        t.peer_low_seconds,
        "peers_low",
        "warning",
        lambda seg: f"peer count <= {t.peer_low_count}",
        lambda seg: min(int(r.get("peers") or 0) for r in seg),
    )

    _emit_sustained(
        events,
        data,
        lambda r: isinstance(r.get("metrics_latency_ms"), int) and r["metrics_latency_ms"] >= t.metrics_latency_ms,
        t.metrics_latency_seconds,
        "metrics_slow",
        "warning",
        lambda seg: f"metrics latency >= {t.metrics_latency_ms} ms",
        lambda seg: max(int(r.get("metrics_latency_ms") or 0) for r in seg),
    )

    # Detect a sharp peer collapse relative to a recent baseline.
    for i, row in enumerate(data):
        p = row.get("peers")
        if not isinstance(p, int):
            continue
        ts = int(row["ts"])
        baseline = [
            int(r["peers"])
            for r in data[:i]
            if isinstance(r.get("peers"), int) and 0 < ts - int(r["ts"]) <= t.peer_drop_window_seconds
        ]
        if not baseline:
            continue
        base = max(baseline)
        if base > 0 and p <= base * (1.0 - t.peer_drop_fraction):
            events.append(Event(
                kind="peer_drop",
                severity="warning",
                started=ts,
                ended=ts,
                duration_s=0,
                summary=f"peer count dropped {base} -> {p}",
                peak=p,
            ))
            break

    # Block/finality stalls: compare movement timestamps, not wall-clock sample cadence.
    last_best = data[0].get("best")
    best_changed_at = int(data[0]["ts"])
    last_finalized = data[0].get("finalized")
    finalized_changed_at = int(data[0]["ts"])
    block_stall_emitted = False
    finality_stall_emitted = False

    for row in data[1:]:
        ts = int(row["ts"])
        best = row.get("best")
        finalized = row.get("finalized")

        if best is not None and best != last_best:
            last_best = best
            best_changed_at = ts
            block_stall_emitted = False
        elif best is not None and not block_stall_emitted and ts - best_changed_at >= t.block_stall_seconds:
            events.append(Event(
                kind="block_stall",
                severity="critical",
                started=best_changed_at,
                ended=ts,
                duration_s=ts - best_changed_at,
                summary=f"best block did not advance for {ts - best_changed_at}s",
                peak=best,
            ))
            block_stall_emitted = True

        if finalized is not None and finalized != last_finalized:
            last_finalized = finalized
            finalized_changed_at = ts
            finality_stall_emitted = False
        elif finalized is not None and not finality_stall_emitted and ts - finalized_changed_at >= t.finality_stall_seconds:
            events.append(Event(
                kind="finality_stall",
                severity="warning",
                started=finalized_changed_at,
                ended=ts,
                duration_s=ts - finalized_changed_at,
                summary=f"finalized block did not advance for {ts - finalized_changed_at}s",
                peak=finalized,
            ))
            finality_stall_emitted = True

    events.sort(key=lambda e: (e.started, e.kind))
    return events


def read_sqlite(path: Path, seconds: int) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    latest = con.execute("SELECT MAX(ts) FROM telemetry").fetchone()[0]
    if latest is None:
        con.close()
        return []
    rows = con.execute("SELECT * FROM telemetry WHERE ts >= ? ORDER BY ts", (int(latest) - seconds,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--jsonl", type=Path)
    ap.add_argument("--seconds", type=int, default=900, help="analyze recent telemetry window")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    rows = read_jsonl(args.jsonl) if args.jsonl else read_sqlite(args.db, args.seconds)
    events = detect(rows)

    if args.json:
        print(json.dumps([asdict(e) for e in events], ensure_ascii=False, indent=2))
        return 0

    print(f"samples: {len(rows)}")
    print(f"events: {len(events)}")
    for e in events:
        peak = "" if e.peak is None else f" | peak={e.peak}"
        print(f"[{e.severity.upper():8}] {e.kind:22} {e.duration_s:4}s | {e.summary}{peak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
