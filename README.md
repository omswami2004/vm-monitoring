# vm-monitoring

Prometheus + Grafana + Alertmanager for **ampere-m1** (Ubuntu 26.04, ARM64, Oracle Cloud), installed as
native binaries under systemd. There's no Docker. Dashboards are in **Grafana Cloud** (stack in ap-south-1), with metrics pushed by Prometheus
`remote_write`, and alerts go to **WhatsApp** from the local Alertmanager, so they don't depend on Grafana.
There is no local Grafana; it was removed to save about 480 MB of RAM.

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
| Public sites (sattle, battle, g1, g2, cdn, index, fetch, auth) | blackbox_exporter `127.0.0.1:9115`, every 60s, 2xx/3xx/401 = up | Status, "Blackbox Exporter" |
| Backends on loopback (3100/health, 8787/health, 8090/healthz, 8000/api/server, RTMP 3000) | blackbox (http / tcp) | Status → Backends |
| TLS: edge cert (Cloudflare) | blackbox `probe_ssl_earliest_cert_expiry` | Cert days left |
| TLS: origin Let's Encrypt certs on disk | textfile `ssl_cert_file_not_after_seconds` | Cert days left |
| The monitoring stack itself | each component's `/metrics` | "Prometheus 2.0 Overview", "Alertmanager" |
| sattle API / private-cdn app metrics | **pending PRs**: separate loopback `/metrics` listeners (9464 / 9466) | commented jobs at the bottom of `prometheus.yml` |

The sites behind Cloudflare (all except g2) are probed through Cloudflare, so a probe failure can also mean a Cloudflare problem. The loopback probes tell the two apart.

### Ports (all 127.0.0.1)
`9090` Prometheus, `9093` Alertmanager (cluster port disabled), `9100` node, `9113` nginx, `9115` blackbox,
`9256` process, `4040` nginxlog, `9097` wa-relay, `8081` nginx stub_status. Nothing is reachable from outside;
data only leaves the VM as an outbound push to Grafana Cloud.

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
Silences can also be added in the Alertmanager web UI over an SSH tunnel (`ssh -L 9093:127.0.0.1:9093 ampere-m1`).

## Layout on the VM

| What | Where |
|---|---|
| Binaries | `/usr/local/bin/{prometheus,promtool,alertmanager,amtool,node_exporter,blackbox_exporter,nginx-prometheus-exporter,process-exporter,prometheus-nginxlog-exporter,wa-relay,vm-textfile-metrics}` |
| Configs | `/etc/prometheus/`, `/etc/alertmanager/`, `/etc/blackbox_exporter/`, `/etc/process-exporter/`, `/etc/prometheus-nginxlog-exporter/`, `/etc/wa-relay/env` |
| Data | `/var/lib/prometheus` (30d local, capped at 10GB), `/var/lib/alertmanager`, `/var/lib/node_exporter/textfile` |
| Units | `/etc/systemd/system/*.service` from `systemd/` |
| nginx | `/etc/nginx/conf.d/monitoring.conf` (log_format + stub_status) |
| Secrets (not in git) | `/etc/prometheus/grafana-cloud.token` (prometheus 600), `/etc/wa-relay/env` (wa-relay 600) |

Every unit runs as its own system user with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `MemoryMax`,
and `Restart=on-failure`.

## Grafana Cloud

- **Metrics:** the `remote_write` block in `prometheus/prometheus.yml` sends to `prometheus-prod-43-prod-ap-south-1.grafana.net` as user
  `3680297`, with the token read from `/etc/prometheus/grafana-cloud.token`. `write_relabel_configs` drop Go runtime stats,
  monitoring-stack internals (except `up`), inactive systemd states and per-thread stats, which keeps about 3.7k series
  (the free tier allows 10k). Scrapes run every 60s, which is 1 sample/min per series.
  Check delivery: `curl -s 127.0.0.1:9090/metrics | grep -E 'remote_storage_samples_(total|failed_total)'`.
- **Alloy** (`alloy/config.alloy`, `127.0.0.1:12345`, about 50 MB) sends its own self-check metrics
  (`job="integrations/alloy-check"`, used by the Cloud "Test connection" button) and **logs** to Grafana Cloud Loki:
  - the journal for our services, ssh, the monitoring units and the bot's user units (review-agent, moshi-hook);
  - any error-or-worse line from other units;
  - `/var/log/nginx/monitoring.log` (with a `vhost` label) and `error.log`.

  Pay-link, group-link and join tokens in URLs are rewritten to `REDACTED` before sending, and `auth.log` is not shipped.
  In Cloud: Explore → `grafanacloud-*-logs` → `{host="ampere-m1", unit="sattle.service"}` or `{job="nginx", vhost="cdn.axiosiiitl.dev"}`.
  Its token is in `/etc/alloy/grafana-cloud.token` (alloy 600).
- node_exporter series arrive in Cloud as `job="integrations/node_exporter", instance="ampere-m1"`, so Cloud's built-in
  Linux integration dashboards work. Locally they keep `job="node"`.
- **Dashboards:** in Grafana Cloud, go to Dashboards → New → Import → upload each file in `grafana/dashboards/`, then pick the
  `grafanacloud-*-prom` data source. "ampere-m1 services" is the per-service overview.
- **Rotating the token:** write the new one to the token file (`sudo install -m600 -o prometheus -g prometheus`), then run
  `sudo systemctl reload prometheus`.

## Deploying changes from this repo

```sh
sudo ./scripts/deploy.sh                # copies configs/units, backs up anything it replaces (*.bak.<ts>), validates
sudo systemctl reload prometheus        # rules / scrape config
sudo systemctl reload alertmanager
sudo systemctl restart <exporter>       # exporter config changes
python3 -I grafana/build-overview.py   # after editing the services dashboard, then re-import it in Grafana Cloud
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
5. **Dashboard.** Import one into Grafana Cloud. Keep the JSON in `grafana/dashboards/` (or add its ID to `scripts/fetch-dashboards.sh`).
   Its series count against the 10k quota, so check `prometheus_remote_storage_samples_total` and drop noisy metrics in `write_relabel_configs`.
6. Run `sudo ./scripts/deploy.sh && sudo systemctl reload prometheus`, and check that `curl -s 127.0.0.1:9090/api/v1/targets` shows it up.

## Updating binaries

Edit the version in `scripts/fetch-binaries.sh` (checksums are fetched and verified against each project's release
`sha256sums.txt` / `checksums.txt`), then:

```sh
sudo ./scripts/fetch-binaries.sh prometheus     # or omit the name to do all
sudo systemctl restart prometheus && systemctl is-active prometheus
```

Restart one at a time and check `systemctl status` after each. wa-relay:
`cd wa-relay && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o wa-relay . && sudo install -m755 wa-relay /usr/local/bin/ && sudo systemctl restart wa-relay`.

Versions at install time (2026-10-16): Prometheus 3.15.0, Alertmanager 0.34.1, node_exporter 1.12.1,
blackbox_exporter 0.29.0, nginx-prometheus-exporter 1.5.3, process-exporter 0.8.7, prometheus-nginxlog-exporter 1.11.0. Upstream releases were used because Ubuntu 26.04 ships Prometheus 2.53 and older exporters.

## Multiple machines

ampere-m1 is the full stack. Every other machine runs **only Grafana Alloy**, which pushes metrics and logs straight to
Grafana Cloud. Nothing on those machines goes through ampere-m1, except alerts on their way to WhatsApp:

```
other VM: Alloy ──metrics+logs──► Grafana Cloud ──alert──► https://stat.axiosiiitl.dev/cloud-alert ──► wa-relay ──► WhatsApp
ampere-m1: Prometheus + Alloy ──► Grafana Cloud        local Alertmanager ──► wa-relay ──► WhatsApp
```

### Trading VM (`hosts/trading-vm/`, 140.245.209.140, `ssh trading-vm`)
It runs auto-trader (tmux pane `0:0`, :8080), kotak-bridge, txntrace.service (:8000), jarvis-bridge.service and nginx (txn./m1./g1.axiosiiitl.dev).
- **Alloy limits** (`alloy-override.conf`): `MemoryMax=300M` and **no swap and no MemoryHigh**. A soft cap made the kernel push
  Alloy into swap and re-read its binary from disk in a loop (about 50 MB/s, load 2.5). It also runs at the lowest CPU/IO weight with `Nice=15`
  and `GOMEMLIMIT=110MiB`, and uses about 60 MB in practice. If it goes over the cap, only Alloy is OOM-killed.
- **Bot logs.** The bot writes only to the tmux pane, so its output is copied to a file without touching the process:
  ```sh
  tmux pipe-pane -t 0:0 "grep --line-buffered -v KOTAK_WS_RAW >> /var/log/auto-trader/server.log"
  ```
  Raw tick frames are dropped. The pipe lasts as long as the pane does; **redo it if the tmux session or pane is recreated.**
  Check it with `tmux list-panes -t 0 -F '#{pane_pipe}'` (1 = piping). Rotation: `logrotate-auto-trader` (daily, 14, copytruncate).
- **Redaction.** Before logs leave the VM, Alloy strips ANSI colours and rewrites access/session/refresh tokens, sid, OTP/TOTP,
  MPIN/PIN, passwords, secrets, consumer keys, API keys, Authorization/Bearer values and any JWT to `REDACTED`.
- **Metrics.** Host (`job="integrations/node_exporter"`), per-process groups (auto-trader, kotak-bridge, txntrace, jarvis-bridge,
  nginx, …) and loopback probes of :8080/api/health, :8000/docs and :8787/health. txn. and m1. are also probed publicly from ampere-m1.
- **Deploy a change:** `scp hosts/trading-vm/config.alloy trading-vm:/tmp/ && ssh trading-vm 'sudo install -m640 -o root -g alloy /tmp/config.alloy /etc/alloy/config.alloy && sudo systemctl reload alloy'`

### Alerts in Grafana Cloud (`grafana-cloud/provision.py`)
These are created through the API with a service account token (`~/.config/grafana-cloud/sa-token`, mode 600), and re-running the script is safe:
- contact point **whatsapp**: a webhook to `https://stat.axiosiiitl.dev/cloud-alert` with `Authorization: Bearer $CLOUD_TOKEN`
  (from `/etc/wa-relay/env`). nginx exposes only that path, and wa-relay rejects requests without the token.
- notification policy: alerts labelled `notify=whatsapp` go to WhatsApp.
- folder "VM monitoring", group **trading-vm**: VM not reporting, auto-trader or kotak-bridge process gone, a local probe failing,
  a failed unit, disk over 85%, RAM under 100 MB available, any OOM kill.
- group **heartbeat**: ampere-m1 not reporting. It is labelled `notify=email` because when ampere-m1 is down, so is its WhatsApp relay.
  **It needs an email contact point**; the stack's default receiver is `empty`.
- dashboards: "Services by host" (pick the host with the filter at the top), Node Exporter Full, NGINX, Named processes, Blackbox.

```sh
ALERT_EMAIL=you@example.com WA_CLOUD_TOKEN="$(sudo sed -n 's/^CLOUD_TOKEN=//p' /etc/wa-relay/env)" python3 -I grafana-cloud/provision.py
```

### Adding another machine
1. Authorize a key: `ssh-keygen -t ed25519 -f ~/.ssh/<name>`, add the `.pub` to that machine's `authorized_keys`, and add a `Host` entry to `~/.ssh/config`.
2. Install Alloy from Grafana's apt repo there (key fingerprint `B53A E77B ADB6 30A6 8304 6005 963F A277 1045 8545`).
3. Copy `hosts/trading-vm/` to `hosts/<name>/` and change the process matchers, probe targets and log files.
   Install the push token as `/etc/alloy/grafana-cloud.token` (alloy 600), plus the config and the override (adjust `MemoryMax` to the machine).
4. Add its rules to `grafana-cloud/provision.py` (copy the trading-vm group and change the instance), then run the script.
5. Watch the series count against 10k: `count({__name__=~".+"})` in Grafana Cloud Explore.
