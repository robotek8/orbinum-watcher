#!/usr/bin/env python3
"""Synthetic replay tests for Orbinum Watcher anomaly detection.

This does NOT connect to Docker, Orbinum, the VPS or the network. It generates
fake telemetry rows in memory and verifies that the passive detector recognizes
the expected events.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETECTOR_PATH = ROOT / "analysis" / "anomaly_detector.py"

spec = importlib.util.spec_from_file_location("anomaly_detector", DETECTOR_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load anomaly detector")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def row(ts: int, **changes):
    base = {
        "ts": ts,
        "error": None,
        "best": 700000 + ts // 6,
        "finalized": 699998 + ts // 6,
        "sync_target": 700000 + ts // 6,
        "peers": 26,
        "metrics_latency_ms": 25,
        "container_cpu_pct": 7.0,
        "container_mem_pct": 9.5,
        "container_status": "running",
        "container_running": 1,
        "container_restarts": 0,
        "host_cpu_pct": 15.0,
        "finality_gap": 2,
        "sync_gap": 0,
    }
    base.update(changes)
    return base


def scenario_normal():
    return [row(ts) for ts in range(0, 121, 5)]


def scenario_cpu_burst():
    rows = []
    for ts in range(0, 121, 5):
        cpu = 96.0 if 30 <= ts <= 60 else 7.0
        rows.append(row(ts, container_cpu_pct=cpu))
    return rows


def scenario_finality_lag():
    rows = []
    for ts in range(0, 121, 5):
        gap = 9 if 30 <= ts <= 60 else 2
        rows.append(row(ts, finality_gap=gap, finalized=(700000 + ts // 6) - gap))
    return rows


def scenario_sync_lag():
    rows = []
    for ts in range(0, 121, 5):
        gap = 5 if 30 <= ts <= 60 else 0
        best = 700000 + ts // 6
        rows.append(row(ts, best=best, sync_target=best + gap, sync_gap=gap))
    return rows


def scenario_peer_collapse():
    rows = []
    for ts in range(0, 121, 5):
        peers = 8 if 35 <= ts <= 70 else 26
        rows.append(row(ts, peers=peers))
    return rows


def scenario_block_stall():
    rows = []
    frozen = 700005
    for ts in range(0, 121, 5):
        if 30 <= ts <= 70:
            best = frozen
        else:
            best = 700000 + ts // 6
        rows.append(row(ts, best=best, sync_target=best, sync_gap=0))
    return rows


def scenario_metrics_failure():
    rows = []
    for ts in range(0, 61, 5):
        err = "metrics: timed out" if 25 <= ts <= 35 else None
        rows.append(row(ts, error=err))
    return rows


def scenario_restart():
    rows = [row(ts) for ts in range(0, 61, 5)]
    for r in rows:
        if r["ts"] >= 30:
            r["container_restarts"] = 1
    return rows


def kinds(rows):
    return {e.kind for e in mod.detect(rows)}


def require(name: str, rows, expected: set[str], forbidden: set[str] | None = None):
    got = kinds(rows)
    missing = expected - got
    unexpected = (forbidden or set()) & got
    if missing or unexpected:
        print(f"FAIL {name}")
        print("  got:", sorted(got))
        if missing:
            print("  missing:", sorted(missing))
        if unexpected:
            print("  forbidden:", sorted(unexpected))
        raise SystemExit(1)
    print(f"PASS {name:20} -> {', '.join(sorted(got)) if got else 'no events'}")


def main():
    require("normal", scenario_normal(), set(), {
        "container_cpu_high",
        "finality_gap",
        "sync_lag",
        "peers_low",
        "peer_drop",
        "block_stall",
        "container_restart",
        "telemetry_error",
    })
    require("cpu burst", scenario_cpu_burst(), {"container_cpu_high"})
    require("finality lag", scenario_finality_lag(), {"finality_gap"})
    require("sync lag", scenario_sync_lag(), {"sync_lag"})
    require("peer collapse", scenario_peer_collapse(), {"peers_low", "peer_drop"})
    require("block stall", scenario_block_stall(), {"block_stall"})
    require("metrics failure", scenario_metrics_failure(), {"telemetry_error"})
    require("restart", scenario_restart(), {"container_restart"})
    print("\nALL SYNTHETIC REPLAY TESTS PASSED")


if __name__ == "__main__":
    main()
