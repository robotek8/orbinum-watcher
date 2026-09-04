# Architecture

Orbinum Watcher separates validator operation from monitoring and presentation.

## Data path

```text
Validator host
  |
  | localhost:9615 /metrics
  v
OpenSSH reverse tunnel
  |
  | VPS loopback:19615
  v
monitor.py
  |
  v
SQLite uptime.db
  |                 \
  |                  \
  v                   v
bot.py              web.py
Telegram            read-only dashboard
                        |
                        v
                    Caddy HTTPS
                        |
                        v
               orbinum-watcher.xyz
```

## Why the metrics endpoint stays private

The Prometheus endpoint is intended for operational telemetry, not direct public exposure. In the reference deployment it is bound on the validator host and transported to the VPS through an SSH reverse tunnel. The VPS receives it on loopback only.

This keeps the public surface limited to the dashboard while still measuring the validator from outside its host.

## Collector

`monitor.py` is deliberately a one-shot process. A scheduler (systemd timer in the reference deployment) starts it every 60 seconds.

For every run it:

1. requests the metrics endpoint;
2. measures request latency;
3. extracts peer count, best block and finalized block;
4. derives `online`, `degraded` or `offline` state;
5. writes one sample to SQLite.

The database acts as the shared boundary between collection, alerting and presentation.

## Alerting

`bot.py` reads the latest sample and monitoring history. It does not query the validator directly.

This separation means an outage of the validator does not prevent the bot from reporting the last known state and incident duration.

The bot is private and pairs to one Telegram chat using a temporary pairing code.

## Public dashboard

`web.py` opens SQLite in read-only mode and exposes only monitoring information.

The reference production deployment runs it inside Docker with the monitoring directory mounted read-only. Caddy provides TLS and reverse proxying.

## Failure model

### Metrics unreachable

The collector writes an `offline` sample.

### Metrics reachable but unhealthy

Missing required metrics or zero peers are recorded as `degraded`.

### Collector stops running

The web and Telegram layers treat stale samples as offline after a short grace period.

### Dashboard unavailable

Monitoring history continues to be collected because the collector is independent of the web process.

## Planned health checks

A future health engine will compare sequential samples and detect:

- best block not advancing;
- finalized block not advancing;
- excessive block lag;
- abrupt peer-count degradation;
- repeated short outages.

That makes health evaluation stronger than simple process or port availability checks.
