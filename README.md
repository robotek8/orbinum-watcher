# Orbinum Watcher

Independent external monitoring for an Orbinum Testnet validator.

**Live dashboard:** https://orbinum-watcher.xyz  
**Validator:** `robotek8-orbinum`

Orbinum Watcher continuously checks validator telemetry from outside the validator host, stores historical samples, calculates uptime and incident statistics, exposes a public status dashboard, and sends private Telegram alerts when the validator goes offline or recovers.

> Community-built monitoring tooling. This repository is not an official Orbinum project.

## What it monitors

- Validator reachability
- Connected peer count
- Best block height
- Finalized block height
- External metrics latency
- 24h / 7d / 30d / all-time uptime
- Incident start, recovery and duration

The collector currently samples every 60 seconds.

## Architecture

```text
Orbinum validator host
        |
        | Prometheus metrics :9615
        v
private reverse SSH tunnel
        |
        v
VPS :19615 (loopback only)
        |
        +--> collector --> SQLite history
        |                    |
        |                    +--> Telegram alert bot
        |                    |
        |                    +--> public web dashboard
        |                               |
        |                               v
        +---------------------------> Caddy / HTTPS
                                         |
                                         v
                              orbinum-watcher.xyz
```

The public dashboard never talks directly to the validator. It reads the monitoring database on the VPS. Prometheus metrics remain private and are transported through an SSH tunnel.

## Components

### `monitor.py`

One-shot collector intended to run from a systemd timer. It reads Orbinum Prometheus metrics and writes a timestamped sample to SQLite.

A sample contains:

```text
timestamp
status
peers
best block
finalized block
latency
error
```

### `bot.py`

Private Telegram bot for operational alerts and quick status checks.

Commands:

```text
/status
/uptime
/incidents
```

It also sends automatic offline, degraded and recovery notifications.

### `web.py`

Small read-only HTTP dashboard. It shows current validator state, uptime windows, a 24-hour availability timeline and incident history.

### `Dockerfile`

Runs the public dashboard as an isolated container. The SQLite monitoring directory should be mounted read-only.

## Status model

Current health is derived from externally observed telemetry:

```text
ONLINE
  metrics reachable
  peers > 0
  required block metrics present

DEGRADED
  metrics reachable
  but required health conditions are not satisfied

OFFLINE
  metrics unavailable or monitoring data becomes stale
```

Block-progression / stall detection is planned as an additional health signal.

## Uptime semantics

The dashboard does not pretend that a new monitor already has 7 or 30 days of history.

- 24-hour uptime is shown together with the observed duration while the first day is still being collected.
- 7-day and 30-day windows show **Collecting data** until enough history exists.
- Missing historical periods are treated as unknown, not as downtime.

## Quick start

### Collector

```bash
python3 monitor.py
python3 monitor.py report
```

Default paths / endpoints in the reference deployment:

```text
Metrics: http://127.0.0.1:19615/metrics
Database: /var/lib/orbinum-monitor/uptime.db
```

### Web

```bash
ORBINUM_DB=/var/lib/orbinum-monitor/uptime.db \
ORBINUM_WEB_HOST=127.0.0.1 \
ORBINUM_WEB_PORT=8787 \
python3 web.py
```

Health endpoint:

```bash
curl http://127.0.0.1:8787/health
```

### Docker dashboard

```bash
docker build -t orbinum-watcher .

docker run --rm \
  -p 127.0.0.1:8787:8787 \
  -e ORBINUM_WEB_HOST=0.0.0.0 \
  -e ORBINUM_WEB_PORT=8787 \
  -e ORBINUM_DB=/data/uptime.db \
  -v /var/lib/orbinum-monitor:/data:ro \
  orbinum-watcher
```

### Telegram

Set secrets outside the repository:

```bash
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_PAIR_CODE='...'
python3 bot.py
```

Do **not** commit the bot token or pairing code.

## Deployment notes

The live deployment uses:

- Orbinum validator on a separate host
- private reverse SSH tunnel for metrics
- Python collector + SQLite on a VPS
- systemd timer at 60-second cadence
- private Telegram bot
- Dockerized web dashboard
- Caddy reverse proxy with automatic HTTPS

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## Security

- Prometheus metrics are not exposed publicly.
- The public dashboard only receives read-only access to monitoring history.
- Telegram credentials live outside the repository.
- The monitoring stack does not submit transactions or modify validator state.

## License

No license has been selected yet. All rights remain with the repository owner until a license is added.
