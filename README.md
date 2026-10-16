# vm-monitoring

Prometheus + Grafana + Alertmanager for **ampere-m1** (Ubuntu 26.04, ARM64, Oracle Cloud), installed as
native binaries under systemd. There's no Docker. Grafana is at **https://stat.axiosiiitl.dev**, and alerts go to **WhatsApp**.

A copy of this file is installed at `/etc/prometheus/README.md`.

## What is monitored and how

| Thing | How | Where to look |
|---|---|---|
| Host CPU, RAM, disk, network, filesystems | node_exporter `127.0.0.1:9100` | "Node Exporter Full" |
| Up/failed/restarts of **every** systemd system unit | node_exporter `--collector.systemd` (+ restart counts) | "ampere-m1 services" → Status |
| User units `review-agent` (GitHub bot) and `moshi-hook` | `vm-textfile-metrics.timer` (every 1 min) → textfile collector | Status, GitHub review agent row |
| GitHub bot activity | same script: `review_agent_log_age_seconds`, `review_agent_responses_posted_total` | GitHub review agent row |
| Per-service CPU/RAM/FDs (sattle, jarvis-bridge, screen-streamer, private-cdn, review-agent, nginx, …) | process-exporter `127.0.0.1:9256`, groups in `process-exporter/config.yml` | "Named processes", services row |
| nginx connections | stub_status on `127.0.0.1:8081` → nginx-prometheus-exporter `127.0.0.1:9113` | "NGINX exporter" |
| Per-site requests / status codes / latency | extra access log `/var/log/nginx/monitoring.log` (has `$host`, `$request_time`) → prometheus-nginxlog-exporter `127.0.0.1:4040` | Traffic row |
| Public sites (sattle, battle, g1, g2, cdn, index, fetch, auth, stat) | blackbox_exporter `127.0.0.1:9115`, every 60s, 2xx/3xx/401 = up | Status, "Blackbox Exporter" |
| Backends on loopback (3100/health, 8787/health, 8090/healthz, 8000/api/server, RTMP 3000) | blackbox (http / tcp) | Status → Backends |
| TLS: edge cert (Cloudflare) | blackbox `probe_ssl_earliest_cert_expiry` | Cert days left |
| TLS: origin Let's Encrypt certs on disk | textfile `ssl_cert_file_not_after_seconds` | Cert days left |
| The monitoring stack itself | each component's `/metrics` | "Prometheus 2.0 Overview", "Alertmanager" |
| sattle API / private-cdn app metrics | **pending PRs**: separate loopback `/metrics` listeners (9464 / 9466) | commented jobs at the bottom of `prometheus.yml` |

The sites behind Cloudflare (all except g2) are probed through Cloudflare, so a probe failure can also mean a Cloudflare problem. The loopback probes tell the two apart.

### Ports (all 127.0.0.1)
`9090` Prometheus, `9093` Alertmanager (cluster port disabled), `9100` node, `9113` nginx, `9115` blackbox,
`9256` process, `4040` nginxlog, `9097` wa-relay, `3300` Grafana (3000 is screen-streamer's RTMP),
`8081` nginx stub_status. Only Grafana is reachable from outside, through nginx and Grafana's login.

## Alerts → WhatsApp

Rules are in `prometheus/rules/alerts.yml`. Alertmanager has a single receiver, a webhook to **wa-relay** (`wa-relay/`, a
small Go service, stdlib only), which posts a formatted message to your WhatsApp server: `POST $WA_SERVER_URL/api/sendText`
with header `key: $WA_API_KEY` and body `{chatId, text}`.

| Alert | Condition |
|---|---|
| TargetDown | a scrape target is down for more than 2m |
| ProbeFailing | a blackbox probe fails for more than 2m |
| SystemdUnitFailed | any system unit is in `failed` state |
| UserUnitNotActive | review-agent / moshi-hook is not active for more than 2m |
| ServiceRestartingRepeatedly | more than 3 restarts in 15m (system or user units) |
| DiskAlmostFull | more than 85% used for 5m |
| MemoryHigh | more than 90% RAM used for 10m |
| TLSCertExpiringSoon / OriginCertExpiringSoon | less than 14 days left |
| Nginx5xxRateHigh | over 5% of a site's responses are 5xx (and more than 5 in 5m) |

Credentials go in `/etc/wa-relay/env` (owner `wa-relay`, mode 600). Start from `wa-relay/env.sample`:

```sh
sudoedit /etc/wa-relay/env          # WA_SERVER_URL, WA_API_KEY, WA_TO
sudo systemctl restart wa-relay
# test: fire a fake alert, it arrives in ~30s
amtool --alertmanager.url=http://127.0.0.1:9093 alert add TestAlert severity=warning instance=manual-test \
  --annotation=summary="Test alert from ampere-m1" --end="$(date -u -d '+5 min' +%Y-%m-%dT%H:%M:%SZ)"
```

To silence something: `amtool --alertmanager.url=http://127.0.0.1:9093 silence add alertname=X --duration=2h --comment=why`.
You can also use the Alertmanager datasource in Grafana (Alerting → Silences).

## Layout on the VM

| What | Where |
|---|---|
| Binaries | `/usr/local/bin/{prometheus,promtool,alertmanager,amtool,node_exporter,blackbox_exporter,nginx-prometheus-exporter,process-exporter,prometheus-nginxlog-exporter,wa-relay,vm-textfile-metrics}` |
| Configs | `/etc/prometheus/`, `/etc/alertmanager/`, `/etc/blackbox_exporter/`, `/etc/process-exporter/`, `/etc/prometheus-nginxlog-exporter/`, `/etc/wa-relay/env`, `/etc/grafana/` |
| Data | `/var/lib/prometheus` (30d, capped at 10GB), `/var/lib/alertmanager`, `/var/lib/grafana`, `/var/lib/node_exporter/textfile` |
| Units | `/etc/systemd/system/*.service` from `systemd/`, plus the Grafana drop-in `grafana-server.service.d/override.conf` |
| nginx | `/etc/nginx/conf.d/monitoring.conf` (log_format + stub_status), `/etc/nginx/sites-available/stat` (Grafana vhost) |
| Secrets (not in git) | `/etc/grafana/admin_password`, `/etc/grafana/secret_key` (root:grafana 640), `/etc/wa-relay/env` (600) |

Every unit runs as its own system user with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `MemoryMax`,
and `Restart=on-failure`.

Grafana login: user `admin`, password in `/etc/grafana/admin_password` (`sudo cat` it). Sign-up and anonymous access are disabled.

## Deploying changes from this repo

```sh
sudo ./scripts/deploy.sh                # copies configs/units, backs up anything it replaces (*.bak.<ts>), validates
sudo systemctl reload prometheus        # rules / scrape config
sudo systemctl reload alertmanager
sudo systemctl restart <exporter>       # exporter config changes
python3 -I grafana/build-overview.py   # after editing the services dashboard, then deploy
```

nginx changes: `sudo nginx -t && sudo systemctl reload nginx` (reload, never restart).

## Adding a new service later

1. **Metrics.** If it has `/metrics`, bind it to 127.0.0.1 and add a job to `prometheus/prometheus.yml`:
   ```yaml
   - job_name: myapp
     static_configs: [{ targets: ["127.0.0.1:PORT"] }]
   ```
   Otherwise use the standard exporter (postgres_exporter, redis_exporter, …) installed the same way as the others
   (a binary in `scripts/fetch-binaries.sh`, plus a unit in `systemd/`). If neither exists, add a `blackbox-local` target for its health URL.
2. **Process stats.** Add a `process_names` entry in `process-exporter/config.yml` (cmdline regexes in one entry are ANDed)
   and add the name to `APP_GROUPS` in `grafana/build-overview.py`.
3. **Public site.** Add its URL to `blackbox-public` and its hostname to the `whitelist` in `nginxlog-exporter/config.hcl`.
4. **systemd state** is automatic. For user units, add them to `USER_UNITS` in `textfile/vm-textfile-metrics.sh`.
5. **Dashboard.** Drop a JSON in `grafana/dashboards/` (or add the ID to `scripts/fetch-dashboards.sh`), then deploy.
6. Run `sudo ./scripts/deploy.sh && sudo systemctl reload prometheus`, and check that `curl -s 127.0.0.1:9090/api/v1/targets` shows it up.

## Updating binaries

Edit the version in `scripts/fetch-binaries.sh` (checksums are fetched and verified against each project's release
`sha256sums.txt` / `checksums.txt`), then:

```sh
sudo ./scripts/fetch-binaries.sh prometheus     # or omit the name to do all
sudo systemctl restart prometheus && systemctl is-active prometheus
```

Restart one at a time and check `systemctl status` after each. Grafana comes from Grafana's apt repo
(`/etc/apt/sources.list.d/grafana.list`), so `sudo apt-get install --only-upgrade grafana` updates it. wa-relay:
`cd wa-relay && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o wa-relay . && sudo install -m755 wa-relay /usr/local/bin/ && sudo systemctl restart wa-relay`.

Versions at install time (2026-10-16): Prometheus 3.15.0, Alertmanager 0.34.1, node_exporter 1.12.1,
blackbox_exporter 0.29.0, nginx-prometheus-exporter 1.5.3, process-exporter 0.8.7, prometheus-nginxlog-exporter 1.11.0,
Grafana 13.2.3 (apt). Upstream releases were used because Ubuntu 26.04 ships Prometheus 2.53 and older exporters.
