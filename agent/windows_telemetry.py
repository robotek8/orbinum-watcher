#!/usr/bin/env python3
"""Passive telemetry collector for the Orbinum validator on Windows.

This agent is deliberately read-only: it does not restart, stop, reconfigure,
or send traffic to the validator. It samples local Prometheus metrics, Docker
container statistics and lightweight Windows host counters, then stores them in
SQLite for later analysis.

Standard-library only. Tested design target: Python 3.11+ on Windows 11.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

CONTAINER = os.getenv("ORBINUM_CONTAINER", "orbinum-validator")
METRICS_URL = os.getenv("ORBINUM_METRICS_URL", "http://127.0.0.1:9615/metrics")
INTERVAL = float(os.getenv("ORBINUM_TELEMETRY_INTERVAL", "5"))
DATA_DIR = Path(os.getenv("ORBINUM_TELEMETRY_DIR", r"C:\ProgramData\OrbinumWatcher"))
DB_PATH = Path(os.getenv("ORBINUM_TELEMETRY_DB", str(DATA_DIR / "telemetry.db")))
RETENTION_DAYS = int(os.getenv("ORBINUM_TELEMETRY_RETENTION_DAYS", "30"))
HTTP_TIMEOUT = float(os.getenv("ORBINUM_METRICS_TIMEOUT", "2.5"))

SIZE_RE = re.compile(r"^\s*([0-9.]+)\s*([KMGTPE]?i?B|B)?\s*$", re.I)


def parse_size(value: str | None) -> int | None:
    if not value:
        return None
    m = SIZE_RE.match(value)
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "B").upper()
    factors = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "PB": 1000**5,
        "KIB": 1024,
        "MIB": 1024**2,
        "GIB": 1024**3,
        "TIB": 1024**4,
        "PIB": 1024**5,
    }
    return int(n * factors.get(unit, 1))


def parse_pair(value: str | None) -> tuple[int | None, int | None]:
    if not value or "/" not in value:
        return None, None
    left, right = value.split("/", 1)
    return parse_size(left), parse_size(right)


def pct(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.strip().rstrip("%"))
    except ValueError:
        return None


def run(cmd: list[str], timeout: float = 3.0) -> str:
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or "command failed").strip())
    return cp.stdout.strip()


def docker_stats() -> dict[str, Any]:
    raw = run(["docker", "stats", CONTAINER, "--no-stream", "--format", "{{json .}}"], timeout=4.0)
    d = json.loads(raw)
    mem_used, mem_limit = parse_pair(d.get("MemUsage"))
    net_rx, net_tx = parse_pair(d.get("NetIO"))
    block_read, block_write = parse_pair(d.get("BlockIO"))
    return {
        "container_cpu_pct": pct(d.get("CPUPerc")),
        "container_mem_pct": pct(d.get("MemPerc")),
        "container_mem_bytes": mem_used,
        "container_mem_limit_bytes": mem_limit,
        "container_net_rx_bytes": net_rx,
        "container_net_tx_bytes": net_tx,
        "container_block_read_bytes": block_read,
        "container_block_write_bytes": block_write,
        "container_pids": int(d["PIDs"]) if str(d.get("PIDs", "")).isdigit() else None,
    }


def docker_state() -> dict[str, Any]:
    raw = run(
        [
            "docker",
            "inspect",
            CONTAINER,
            "--format",
            "{{json .State}}|{{.RestartCount}}",
        ],
        timeout=3.0,
    )
    state_json, restart_count = raw.rsplit("|", 1)
    st = json.loads(state_json)
    return {
        "container_status": st.get("Status"),
        "container_running": 1 if st.get("Running") else 0,
        "container_restarts": int(restart_count),
    }


def fetch_metrics() -> dict[str, Any]:
    req = urllib.request.Request(METRICS_URL, headers={"User-Agent": "OrbinumWatcherTelemetry/1"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    out: dict[str, Any] = {
        "best": None,
        "finalized": None,
        "sync_target": None,
        "peers": None,
        "metrics_latency_ms": None,
    }

    # Prometheus scrape latency from the collector's perspective is measured by caller.
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        try:
            name, value = line.rsplit(" ", 1)
            v = float(value)
        except ValueError:
            continue

        if name.startswith("substrate_block_height"):
            if 'status="best"' in name:
                out["best"] = int(v)
            elif 'status="finalized"' in name:
                out["finalized"] = int(v)
            elif 'status="sync_target"' in name:
                out["sync_target"] = int(v)
        elif name.startswith("substrate_sub_libp2p_peers_count"):
            out["peers"] = int(v)

    return out


def host_stats() -> dict[str, Any]:
    """Lightweight host CPU/RAM snapshot via CIM. Sampled less often than Docker stats."""
    ps = (
        "$cpu=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average;"
        "$os=Get-CimInstance Win32_OperatingSystem;"
        "$total=[double]$os.TotalVisibleMemorySize*1024;"
        "$free=[double]$os.FreePhysicalMemory*1024;"
        "$used=$total-$free;"
        "[pscustomobject]@{cpu=[double]$cpu;used=[double]$used;total=[double]$total}"
        "|ConvertTo-Json -Compress"
    )
    raw = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=5.0)
    d = json.loads(raw)
    total = float(d.get("total") or 0)
    used = float(d.get("used") or 0)
    return {
        "host_cpu_pct": float(d.get("cpu")) if d.get("cpu") is not None else None,
        "host_mem_bytes": int(used) if used else None,
        "host_mem_total_bytes": int(total) if total else None,
        "host_mem_pct": (used / total * 100.0) if total > 0 else None,
    }


def connect_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry (
            ts INTEGER PRIMARY KEY,
            best INTEGER,
            finalized INTEGER,
            sync_target INTEGER,
            peers INTEGER,
            finality_gap INTEGER,
            sync_gap INTEGER,
            metrics_latency_ms INTEGER,
            container_cpu_pct REAL,
            container_mem_pct REAL,
            container_mem_bytes INTEGER,
            container_mem_limit_bytes INTEGER,
            container_net_rx_bytes INTEGER,
            container_net_tx_bytes INTEGER,
            container_block_read_bytes INTEGER,
            container_block_write_bytes INTEGER,
            container_pids INTEGER,
            container_status TEXT,
            container_running INTEGER,
            container_restarts INTEGER,
            host_cpu_pct REAL,
            host_mem_pct REAL,
            host_mem_bytes INTEGER,
            host_mem_total_bytes INTEGER,
            error TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(ts)")
    con.commit()
    return con


def sample(cached_host: dict[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"ts": int(time.time()), "error": None}
    errors: list[str] = []

    started = time.perf_counter()
    try:
        row.update(fetch_metrics())
        row["metrics_latency_ms"] = int((time.perf_counter() - started) * 1000)
    except Exception as e:  # keep collecting Docker/host data even if metrics fail
        errors.append(f"metrics: {e}")
        row.update({"best": None, "finalized": None, "sync_target": None, "peers": None, "metrics_latency_ms": None})

    try:
        row.update(docker_stats())
    except Exception as e:
        errors.append(f"docker stats: {e}")

    try:
        row.update(docker_state())
    except Exception as e:
        errors.append(f"docker inspect: {e}")

    if cached_host:
        row.update(cached_host)

    b = row.get("best")
    f = row.get("finalized")
    t = row.get("sync_target")
    row["finality_gap"] = (b - f) if isinstance(b, int) and isinstance(f, int) else None
    row["sync_gap"] = (t - b) if isinstance(t, int) and isinstance(b, int) else None
    row["error"] = " | ".join(errors) if errors else None
    return row


def insert(con: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = [r[1] for r in con.execute("PRAGMA table_info(telemetry)").fetchall()]
    vals = [row.get(c) for c in cols]
    q = f"INSERT OR REPLACE INTO telemetry ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
    con.execute(q, vals)
    con.commit()


def cleanup(con: sqlite3.Connection) -> None:
    cutoff = int(time.time()) - RETENTION_DAYS * 86400
    con.execute("DELETE FROM telemetry WHERE ts < ?", (cutoff,))
    con.commit()


def human(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="take one sample, print it and exit")
    ap.add_argument("--no-write", action="store_true", help="do not write SQLite (useful for a dry run)")
    args = ap.parse_args()

    con = None if args.no_write else connect_db()
    cached_host: dict[str, Any] = {}
    last_host = 0.0
    last_cleanup = 0.0

    try:
        while True:
            now_mono = time.monotonic()
            if now_mono - last_host >= 15:
                try:
                    cached_host = host_stats()
                except Exception as e:
                    cached_host = {"host_error": str(e)}
                last_host = now_mono

            row = sample(cached_host)
            if con is not None:
                insert(con, row)
                if now_mono - last_cleanup >= 3600:
                    cleanup(con)
                    last_cleanup = now_mono

            print(human(row), flush=True)

            if args.once:
                return 0

            time.sleep(max(1.0, INTERVAL))
    except KeyboardInterrupt:
        return 0
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    raise SystemExit(main())
