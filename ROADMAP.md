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

- [ ] Best block progression checks
- [ ] Finalization progression checks
- [ ] Stalled-chain detection
- [ ] Configurable degraded thresholds
- [ ] Alert deduplication / cooldowns
- [ ] Incident reason classification

## v0.3 — Better observability

- [ ] 24h latency history
- [ ] Peer-count history
- [ ] Block-lag chart
- [ ] Daily / weekly uptime summaries
- [ ] CSV / JSON export
- [ ] `/api/status` endpoint

## v0.4 — Operational hardening

- [ ] Database retention policy
- [ ] Automated backup
- [ ] Structured logs
- [ ] Health checks for collector and Telegram bot
- [ ] Docker Compose example
- [ ] systemd unit examples

## v1.0

- [ ] Stable public dashboard
- [ ] Documented self-hosted installation
- [ ] Health engine with stall detection
- [ ] Reliable incident alerts and recovery reports
- [ ] Long-term uptime statistics
