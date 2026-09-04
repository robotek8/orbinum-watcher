#!/usr/bin/env python3
"""Export passive Orbinum stress events and optionally sync them to the VPS.

This script is read-only with respect to the validator, Docker, tunnels and the
telemetry database. It analyzes the local telemetry SQLite DB, builds a compact
JSON snapshot, and can upload that snapshot through an isolated SFTP-only
account.

The remote write is atomic: events.json.tmp is uploaded first and then renamed
to events.json. If upload fails, the previous remote snapshot stays intact.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(r"C:\OrbinumWatcher")
DEFAULT_DB = Path(r"C:\ProgramData\OrbinumWatcher\telemetry.db")
DEFAULT_REPORT = DEFAULT_ROOT / "analysis" / "stress_event_report.py"
DEFAULT_HOST = os.getenv("ORBINUM_EVENT_HOST", "169.58.246.105")
DEFAULT_USER = os.getenv("ORBINUM_EVENT_USER", "orbinum-events")
DEFAULT_KEY = Path(os.getenv("ORBINUM_EVENT_SSH_KEY", str(Path.home() / ".ssh" / "orbinum_tunnel")))
DEFAULT_REMOTE_DIR = os.getenv("ORBINUM_EVENT_REMOTE_DIR", "upload")
VALIDATOR = os.getenv("ORBINUM_VALIDATOR_NAME", "robotek8-orbinum")


def load_report_module(path: Path):
    spec = importlib.util.spec_from_file_location("stress_event_report", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load stress event report: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def latest_ts(db_path: Path) -> int | None:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT MAX(ts) FROM telemetry").fetchone()
        return int(row[0]) if row and row[0] is not None else None
    finally:
        con.close()


def event_id(event: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "validator": VALIDATOR,
            "started": event.get("started"),
            "kinds": event.get("kinds") or [],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def event_type(kinds: list[str]) -> str:
    s = set(kinds)
    if "container_down" in s:
        return "Node offline"
    if "container_restart" in s:
        return "Validator restart"
    if "block_stall" in s:
        return "Block stall"
    if "sync_lag" in s:
        return "Sync lag"
    if "finality_stall" in s or "finality_gap" in s:
        return "Finality lag"
    if "peer_drop" in s or "peers_low" in s:
        return "Peer drop"
    if "metrics_slow" in s or "telemetry_error" in s:
        return "Telemetry degradation"
    return "Load event"


def compact_event(e: Any) -> dict[str, Any]:
    d = asdict(e)
    d["id"] = event_id(d)
    d["type"] = event_type(list(d.get("kinds") or []))
    d["status"] = "Recovered" if d.get("recovered") else "Open"
    return d


def build_snapshot(report_module, db_path: Path, seconds: int) -> dict[str, Any]:
    rows = report_module.read_sqlite(db_path, None if seconds == 0 else seconds)
    events = report_module.build_stress_events(rows)
    latest = latest_ts(db_path)
    return {
        "schema": 1,
        "generated_at": int(time.time()),
        "validator": VALIDATOR,
        "source_latest_ts": latest,
        "source_sample_age_s": None if latest is None else max(0, int(time.time()) - latest),
        "events": [compact_event(e) for e in events[-50:]],
    }


def run(cmd: list[str], timeout: float = 30.0) -> str:
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if cp.returncode != 0:
        err = (cp.stderr or cp.stdout or "command failed").strip()
        raise RuntimeError(f"{cmd[0]} failed ({cp.returncode}): {err}")
    return (cp.stdout or "").strip()


def upload_sftp(snapshot_path: Path, host: str, user: str, key: Path, remote_dir: str) -> None:
    """Upload through the dedicated internal-sftp account; no remote shell needed."""
    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    target = f"{user}@{host}"
    remote_base = "/" + remote_dir.strip("/")
    remote_tmp = f"{remote_base}/events.json.tmp"
    remote_final = f"{remote_base}/events.json"
    local = snapshot_path.resolve().as_posix().replace('"', '\\"')

    batch_text = (
        f'put "{local}" {remote_tmp}\n'
        f'rename {remote_tmp} {remote_final}\n'
        'bye\n'
    )
    fd, batch_name = tempfile.mkstemp(prefix="orbinum-sftp-", suffix=".txt", dir=DEFAULT_ROOT)
    os.close(fd)
    batch_path = Path(batch_name)
    try:
        batch_path.write_text(batch_text, encoding="ascii")
        run([
            "sftp.exe",
            "-i", str(key),
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8",
            "-b", str(batch_path),
            target,
        ])
    finally:
        try:
            batch_path.unlink()
        except OSError:
            pass


def write_snapshot(text: str, output: Path | None) -> tuple[Path, bool]:
    """Return a local snapshot path and whether it should be deleted afterwards."""
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        return output, False

    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    path = DEFAULT_ROOT / "events-sync.json"
    tmp = DEFAULT_ROOT / "events-sync.json.tmp"
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path, False


def perform_once(args, report) -> dict[str, Any]:
    snapshot = build_snapshot(report, args.db, args.seconds)
    text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    source, _ = write_snapshot(text, args.output)

    if args.upload:
        if not args.key.exists():
            raise FileNotFoundError(f"SSH key not found: {args.key}")
        upload_sftp(source, args.host, args.user, args.key, args.remote_dir)

    if not args.quiet:
        print(f"validator: {snapshot['validator']}")
        print(f"source sample age: {snapshot['source_sample_age_s']}s")
        print(f"events exported: {len(snapshot['events'])}")
        if args.upload:
            print(f"uploaded: {args.user}@{args.host}:/{args.remote_dir.strip('/')}/events.json")
    return snapshot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--seconds", type=int, default=86400, help="analysis look-back; 0 = all stored telemetry")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--key", type=Path, default=DEFAULT_KEY)
    ap.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    ap.add_argument("--loop", action="store_true", help="repeat continuously")
    ap.add_argument("--interval", type=int, default=30, help="seconds between loop iterations")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    report = load_report_module(args.report)

    if not args.loop:
        perform_once(args, report)
        return 0

    while True:
        try:
            perform_once(args, report)
        except Exception as e:
            print(f"event sync error: {e}", file=sys.stderr, flush=True)
        time.sleep(max(10, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
