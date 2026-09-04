#!/usr/bin/env python3

import html
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.getenv("ORBINUM_DB", "/var/lib/orbinum-monitor/uptime.db")
HOST = os.getenv("ORBINUM_WEB_HOST", "127.0.0.1")
PORT = int(os.getenv("ORBINUM_WEB_PORT", "8787"))
VALIDATOR_NAME = os.getenv("ORBINUM_VALIDATOR_NAME", "robotek8-orbinum")

KZ = timezone(timedelta(hours=5))


def connect():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)


def fmt_time(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, KZ).strftime("%d.%m.%Y %H:%M")


def human_duration(seconds):
    seconds = max(0, int(seconds))

    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"

    if seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days}d {hours}h" if hours else f"{days}d"


def latest():
    con = connect()
    row = con.execute(
        """
        SELECT ts, ok, status, peers, best, finalized, latency_ms, error
        FROM samples
        ORDER BY ts DESC
        LIMIT 1
        """
    ).fetchone()
    con.close()
    return row


def first_sample():
    con = connect()
    row = con.execute("SELECT MIN(ts) FROM samples").fetchone()
    con.close()
    return row[0] if row and row[0] else None


def stats(seconds=None):
    con = connect()

    if seconds:
        cutoff = int(time.time()) - seconds
        rows = con.execute(
            "SELECT ts, ok FROM samples WHERE ts >= ? ORDER BY ts",
            (cutoff,),
        ).fetchall()
    else:
        rows = con.execute("SELECT ts, ok FROM samples ORDER BY ts").fetchall()

    con.close()

    if not rows:
        return {"uptime": 0, "samples": 0, "offline": 0, "incidents": 0, "longest": 0, "observed": 0}

    total = len(rows)
    online = sum(row[1] for row in rows)
    offline = total - online
    incidents = 0
    longest = 0
    current = 0
    previous = 1

    for _, ok in rows:
        if not ok:
            current += 1
            longest = max(longest, current)
            if previous:
                incidents += 1
        else:
            current = 0
        previous = ok

    observed = max(0, rows[-1][0] - rows[0][0])

    return {
        "uptime": online / total * 100,
        "samples": total,
        "offline": offline,
        "incidents": incidents,
        "longest": longest,
        "observed": observed,
    }


def incidents(limit=10):
    cutoff = int(time.time()) - 2592000
    con = connect()
    rows = con.execute(
        "SELECT ts, ok FROM samples WHERE ts >= ? ORDER BY ts",
        (cutoff,),
    ).fetchall()
    con.close()

    result = []
    started = None

    for ts, ok in rows:
        if not ok and started is None:
            started = ts
        elif ok and started is not None:
            result.append((started, ts))
            started = None

    if started is not None:
        result.append((started, None))

    return result[-limit:][::-1]


def availability_24h():
    now = int(time.time())
    start = now - 86400
    slot = 900
    slots = 96

    con = connect()
    rows = con.execute(
        "SELECT ts, ok FROM samples WHERE ts >= ? ORDER BY ts",
        (start,),
    ).fetchall()
    con.close()

    buckets = [[] for _ in range(slots)]

    for ts, ok in rows:
        idx = int((ts - start) / slot)
        if 0 <= idx < slots:
            buckets[idx].append(ok)

    states = []
    for bucket in buckets:
        if not bucket:
            states.append("unknown")
        elif all(bucket):
            states.append("online")
        elif not any(bucket):
            states.append("offline")
        else:
            states.append("degraded")

    return states


def uptime_card(title, data, required_age, monitor_age, always_show=False):
    enough_history = monitor_age >= required_age

    if enough_history or always_show:
        if not enough_history:
            extra = f"Observed {human_duration(monitor_age)}"
        elif data["incidents"]:
            extra = f'{data["incidents"]} incidents'
        else:
            extra = "0 incidents"

        return f"""
        <div class="card">
            <div class="label">{title}</div>
            <div class="big">{data['uptime']:.3f}%</div>
            <div class="sub">{extra}</div>
        </div>
        """

    progress = min(100, monitor_age / required_age * 100)

    return f"""
    <div class="card">
        <div class="label">{title}</div>
        <div class="collecting">Collecting data…</div>
        <div class="sub">{human_duration(monitor_age)} observed · {progress:.1f}% complete</div>
    </div>
    """


def page():
    row = latest()
    now = int(time.time())

    if row:
        ts, ok, raw_status, peers, best, finalized, latency, error = row
        age = now - ts

        if age > 150:
            state = "offline"
        elif raw_status == "degraded":
            state = "degraded"
        elif ok:
            state = "online"
        else:
            state = "offline"
    else:
        ts = peers = best = finalized = latency = None
        error = "No monitoring data"
        state = "offline"

    first = first_sample()
    monitor_age = max(0, now - first) if first else 0

    s24 = stats(86400)
    s7 = stats(604800)
    s30 = stats(2592000)
    sall = stats()

    uptime_cards = (
        uptime_card("24 HOURS", s24, 86400, monitor_age, always_show=True)
        + uptime_card("7 DAYS", s7, 604800, monitor_age)
        + uptime_card("30 DAYS", s30, 2592000, monitor_age)
        + f"""
        <div class="card">
            <div class="label">ALL TIME</div>
            <div class="big">{sall['uptime']:.3f}%</div>
            <div class="sub">{sall['samples']} samples</div>
        </div>
        """
    )

    bars = "".join(f'<span class="bar {name}" title="{name}"></span>' for name in availability_24h())

    incident_rows = ""
    for start, end in incidents():
        if end:
            duration = max(1, round((end - start) / 60))
            incident_rows += f"""
            <tr>
                <td>{fmt_time(start)}</td>
                <td>{fmt_time(end)}</td>
                <td>{duration} min</td>
                <td><span class="resolved">Resolved</span></td>
            </tr>
            """
        else:
            incident_rows += f"""
            <tr>
                <td>{fmt_time(start)}</td>
                <td>Now</td>
                <td>—</td>
                <td><span class="active">Active</span></td>
            </tr>
            """

    if not incident_rows:
        incident_rows = """
        <tr>
            <td colspan="4" class="empty">No incidents recorded since monitoring began.</td>
        </tr>
        """

    since = fmt_time(first)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Orbinum Watcher — {html.escape(VALIDATOR_NAME)}</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #090d12; color: #e8edf2; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
.container {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 48px 0 70px; }}
header {{ display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 34px; }}
.brand {{ font-size: 14px; letter-spacing: .14em; color: #82909d; text-transform: uppercase; }}
h1 {{ margin: 8px 0 0; font-size: clamp(28px, 5vw, 46px); letter-spacing: -.035em; }}
.status {{ border: 1px solid #25303a; background: #0d1319; border-radius: 999px; padding: 9px 14px; font-weight: 700; font-size: 13px; }}
.status.online {{ color: #42d788; }}
.status.degraded {{ color: #e6b84f; }}
.status.offline {{ color: #ff616d; }}
.grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }}
.card {{ background: #0d1319; border: 1px solid #202a33; border-radius: 14px; padding: 20px; }}
.label {{ color: #778693; font-size: 12px; text-transform: uppercase; letter-spacing: .11em; margin-bottom: 8px; }}
.value {{ font-size: 26px; font-weight: 720; letter-spacing: -.025em; }}
.sub {{ margin-top: 6px; color: #74818c; font-size: 13px; }}
.section {{ margin-top: 34px; }}
.section-title {{ margin-bottom: 14px; font-size: 15px; letter-spacing: .06em; text-transform: uppercase; color: #9ba8b3; }}
.uptime {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }}
.big {{ font-size: 29px; font-weight: 750; }}
.collecting {{ font-size: 21px; font-weight: 700; color: #9caab5; margin-top: 14px; margin-bottom: 12px; }}
.timeline {{ display: flex; gap: 2px; height: 42px; align-items: stretch; }}
.bar {{ flex: 1; min-width: 2px; border-radius: 2px; }}
.bar.online {{ background: #2bbf75; }}
.bar.offline {{ background: #e34f5f; }}
.bar.degraded {{ background: #d4a83d; }}
.bar.unknown {{ background: #25303a; }}
.legend {{ display: flex; justify-content: space-between; margin-top: 9px; color: #687680; font-size: 12px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 14px 10px; border-bottom: 1px solid #1e2831; text-align: left; font-size: 13px; }}
th {{ color: #75838e; font-weight: 500; }}
.resolved {{ color: #42d788; }}
.active {{ color: #ff616d; }}
.empty {{ color: #697782; text-align: center; padding: 24px; }}
.footer {{ margin-top: 34px; padding-top: 22px; border-top: 1px solid #1b242c; color: #65727d; font-size: 12px; line-height: 1.9; }}
.footer strong {{ color: #8795a0; }}
@media (max-width: 800px) {{ .grid, .uptime {{ grid-template-columns: repeat(2, 1fr); }} header {{ flex-direction: column; }} }}
@media (max-width: 480px) {{ .grid, .uptime {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="container">
<header>
<div>
    <div class="brand">Orbinum Validator Observatory</div>
    <h1>{html.escape(VALIDATOR_NAME)}</h1>
</div>
<div class="status {state}">● {state.upper()}</div>
</header>

<div class="grid">
<div class="card"><div class="label">Peers</div><div class="value">{peers if peers is not None else '—'}</div><div class="sub">connected peers</div></div>
<div class="card"><div class="label">Best Block</div><div class="value">#{best if best is not None else '—'}</div><div class="sub">current chain head</div></div>
<div class="card"><div class="label">Finalized</div><div class="value">#{finalized if finalized is not None else '—'}</div><div class="sub">latest finalized block</div></div>
<div class="card"><div class="label">Latency</div><div class="value">{latency if latency is not None else '—'} ms</div><div class="sub">external metrics check</div></div>
</div>

<div class="section"><div class="section-title">Validator uptime</div><div class="uptime">{uptime_cards}</div></div>

<div class="section">
<div class="section-title">Last 24 hours</div>
<div class="card"><div class="timeline">{bars}</div><div class="legend"><span>24h ago</span><span>Now</span></div></div>
</div>

<div class="section">
<div class="section-title">Incident history</div>
<div class="card">
<table>
<thead><tr><th>Started</th><th>Recovered</th><th>Duration</th><th>Status</th></tr></thead>
<tbody>{incident_rows}</tbody>
</table>
</div>
</div>

<div class="footer">
<strong>Independent external monitoring</strong> of the Orbinum Testnet validator <strong>{html.escape(VALIDATOR_NAME)}</strong>.<br>
Monitoring since {since} UTC+5 · External checks every 60 seconds · Telegram incident alerts enabled.<br>
Best block, finalized height, peer count and validator reachability are observed independently from the validator host.<br>
Last successful monitoring sample: {fmt_time(ts)} UTC+5.
</div>
</div>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return

        try:
            body = page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = f"Internal error: {html.escape(str(exc))}".encode()
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Orbinum Watcher listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
