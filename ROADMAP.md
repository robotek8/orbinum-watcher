# Roadmap

## v0.1 — External uptime monitor

- [x] External Prometheus metrics collection
- [x] SQLite history
- [x] 60-second sampling
- [x] Peer / best block / finalized block tracking
- [x] Uptime calculation
- [x] Incident history
- [x] Private Telegram bot
- [x] Public HTTPS dashboard
- [x] Read-only dashboard database access

## v0.2 — Validator health engine

- [x] Best block progression checks
- [x] Finalization progression checks
- [x] Stalled-chain detection
- [ ] Configurable degraded thresholds
- [ ] Alert deduplication / cooldowns
- [x] Incident reason classification

## v0.25 — Passive stress observability

- [x] Passive Windows validator telemetry agent
- [x] Local telemetry SQLite history
- [x] Container CPU / RAM / I/O observation
- [x] Host CPU / RAM observation
- [x] Peer-drop and low-peer detection
- [x] Finality-gap and sync-lag detection
- [x] Validator restart/down detection
- [x] Synthetic anomaly replay tests
- [x] Grouped stress-event reports
- [x] Isolated SFTP-only event synchronization
- [x] Atomic event snapshot upload
- [x] Stress/load events merged into dashboard Incident history
- [x] Expandable event metrics and recovery context
- [x] End-to-end synthetic event validation through the production path
- [x] Compact Incident history layout without horizontal scrolling

## v0.3 — Better observability

- [ ] 24h latency history
- [ ] Peer-count history
- [ ] Block-lag chart
- [ ] Daily / weekly uptime summaries
- [ ] CSV / JSON export
- [x] `/api/status` endpoint

## v0.4 — Operational hardening

- [ ] Database retention policy
- [ ] Automated backup
- [ ] Structured logs
- [ ] Health checks for collector and Telegram bot
- [x] Docker Compose example
- [ ] systemd unit examples

## v1.0

- [ ] Stable public dashboard
- [ ] Documented self-hosted installation
- [ ] Health engine with stall detection
- [ ] Reliable incident alerts and recovery reports
- [ ] Long-term uptime statistics
