#!/usr/bin/env python3
"""Generate grafana/dashboards/vm-services-overview.json (the per-service dashboard for ampere-m1).

Edit SERVICES / panels here, run `python3 -I grafana/build-overview.py`, then deploy.
"""
import json
import pathlib

DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
SYSTEM_UNITS = "sattle|txntrace|jarvis-bridge|screen-streamer|private-cdn|nginx|prometheus|alertmanager|alloy|node_exporter|process-exporter|blackbox_exporter|nginx-prometheus-exporter|prometheus-nginxlog-exporter|wa-relay"
APP_GROUPS = "sattle|auto-trader|kotak-bridge|txntrace|jarvis-bridge|screen-streamer|private-cdn|review-agent|moshi-hook|nginx|monitoring"

panels = []
_id = 0
y = 0


def nid():
    global _id
    _id += 1
    return _id


def row(title):
    global y
    panels.append({"type": "row", "title": title, "id": nid(), "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}})
    y += 1


def panel(kind, title, targets, x, w, h, **extra):
    p = {"type": kind, "title": title, "id": nid(), "datasource": DS,
         "gridPos": {"h": h, "w": w, "x": x, "y": y},
         "targets": [dict(datasource=DS, refId=chr(65 + i), **t) for i, t in enumerate(targets)]}
    p.update(extra)
    panels.append(p)
    return p


def updown(unit_text=("DOWN", "UP")):
    return {"defaults": {"mappings": [{"type": "value", "options": {
        "0": {"text": unit_text[0], "color": "red"}, "1": {"text": unit_text[1], "color": "green"}}}],
        "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}},
        "overrides": []}


def stat_opts(text_mode="value_and_name"):
    return {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background", "graphMode": "none", "textMode": text_mode,
            "orientation": "horizontal", "justifyMode": "auto"}


def ts(title, targets, x, w, unit, h=8, stack=False):
    return panel("timeseries", title, targets, x, w, h,
                 fieldConfig={"defaults": {"unit": unit, "custom": {
                     "fillOpacity": 15 if stack else 5, "lineWidth": 1, "showPoints": "never",
                     "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": []},
                 options={"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]},
                          "tooltip": {"mode": "multi", "sort": "desc"}})


# --- Status ---------------------------------------------------------------
row("Status")
panel("stat", "systemd services", [{
    "expr": f'max by (name) (node_systemd_unit_state{{state="active",name=~"({SYSTEM_UNITS})\\\\.service"}})',
    "legendFormat": "{{name}}", "instant": True}], 0, 12, 7,
    fieldConfig=updown(), options=stat_opts())
panel("stat", "User services (ubuntu)", [{
    "expr": "user_unit_active", "legendFormat": "{{unit}}", "instant": True}], 12, 4, 7,
    fieldConfig=updown(), options=stat_opts())
panel("stat", "Failed units", [{
    "expr": 'count(node_systemd_unit_state{state="failed"} == 1) or vector(0)', "instant": True}], 16, 4, 7,
    fieldConfig={"defaults": {"thresholds": {"mode": "absolute", "steps": [
        {"color": "green", "value": None}, {"color": "red", "value": 1}]}}, "overrides": []},
    options=stat_opts("value"))
panel("stat", "Firing alerts", [{
    "expr": 'count(ALERTS{alertstate="firing"}) or vector(0)', "instant": True}], 20, 4, 7,
    fieldConfig={"defaults": {"thresholds": {"mode": "absolute", "steps": [
        {"color": "green", "value": None}, {"color": "orange", "value": 1}]}}, "overrides": []},
    options=stat_opts("value"))
y += 7
panel("stat", "Public sites (via Cloudflare)", [{
    "expr": 'probe_success{job="blackbox-public"}', "legendFormat": "{{instance}}", "instant": True}], 0, 12, 8,
    fieldConfig=updown(), options=stat_opts())
panel("stat", "Backends on loopback", [{
    "expr": 'probe_success{job=~"blackbox-local|blackbox-tcp"}', "legendFormat": "{{service}}", "instant": True}], 12, 6, 8,
    fieldConfig=updown(), options=stat_opts())
panel("bargauge", "Cert days left", [
    {"expr": "(ssl_cert_file_not_after_seconds - time()) / 86400", "legendFormat": "origin {{cert}}", "instant": True},
    {"expr": 'min by (instance) (probe_ssl_earliest_cert_expiry{job="blackbox-public"} - time()) / 86400',
     "legendFormat": "edge {{instance}}", "instant": True}], 18, 6, 8,
    fieldConfig={"defaults": {"unit": "d", "decimals": 0, "min": 0, "max": 90, "thresholds": {"mode": "absolute", "steps": [
        {"color": "red", "value": None}, {"color": "orange", "value": 14}, {"color": "green", "value": 30}]}},
        "overrides": []},
    options={"displayMode": "basic", "orientation": "horizontal", "showUnfilled": True,
             "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}})
y += 8

# --- Traffic (nginx access log) -------------------------------------------
row("Traffic per site (nginx)")
ts("Requests/s by site", [{"expr": "sum by (vhost) (rate(nginx_http_response_count_total[5m]))",
                           "legendFormat": "{{vhost}}"}], 0, 12, "reqps", stack=True)
ts("5xx/s by site", [{"expr": 'sum by (vhost) (rate(nginx_http_response_count_total{status=~"5.."}[5m]))',
                      "legendFormat": "{{vhost}}"}], 12, 12, "reqps")
y += 8
ts("p95 response time by site", [{
    "expr": "histogram_quantile(0.95, sum by (vhost, le) (rate(nginx_http_response_time_seconds_hist_bucket[5m])))",
    "legendFormat": "{{vhost}}"}], 0, 12, "s")
ts("Responses by status class", [{
    "expr": 'sum by (class) (label_replace(rate(nginx_http_response_count_total[5m]), "class", "${1}xx", "status", "(.).."))',
    "legendFormat": "{{class}}"}], 12, 12, "reqps", stack=True)
y += 8
ts("nginx connections", [{"expr": "nginx_connections_active", "legendFormat": "active"},
                         {"expr": "nginx_connections_waiting", "legendFormat": "waiting"},
                         {"expr": "rate(nginx_http_requests_total[5m])", "legendFormat": "req/s total"}], 0, 12, "short")
ts("Probe response time", [{"expr": "probe_duration_seconds", "legendFormat": "{{instance}}"}], 12, 12, "s")
y += 8

# --- Per-service resources ------------------------------------------------
row("Per-service CPU and memory (process-exporter)")
ts("CPU by service", [{
    "expr": f'sum by (groupname) (rate(namedprocess_namegroup_cpu_seconds_total{{groupname=~"{APP_GROUPS}"}}[5m]))',
    "legendFormat": "{{groupname}}"}], 0, 12, "percentunit", stack=True)
ts("Resident memory by service", [{
    "expr": f'namedprocess_namegroup_memory_bytes{{memtype="resident",groupname=~"{APP_GROUPS}"}}',
    "legendFormat": "{{groupname}}"}], 12, 12, "bytes", stack=True)
y += 8
ts("Restarts (systemd NRestarts)", [
    {"expr": f'node_systemd_service_restart_total{{name=~"({SYSTEM_UNITS})\\\\.service"}}', "legendFormat": "{{name}}"},
    {"expr": "user_unit_restarts_total", "legendFormat": "{{unit}} (user)"}], 0, 12, "short")
ts("Open file descriptors by service", [{
    "expr": f'namedprocess_namegroup_open_filedesc{{groupname=~"{APP_GROUPS}"}}', "legendFormat": "{{groupname}}"}],
    12, 12, "short")
y += 8

# --- GitHub review agent ---------------------------------------------------
row("GitHub review agent (@omswami2004)")
panel("stat", "Responses posted (24h)", [{"expr": "increase(review_agent_responses_posted_total[24h])", "instant": True}],
      0, 6, 6, options=stat_opts("value"),
      fieldConfig={"defaults": {"decimals": 0, "color": {"mode": "fixed", "fixedColor": "blue"}}, "overrides": []})
panel("stat", "Last log activity", [{"expr": "review_agent_log_age_seconds", "instant": True}], 6, 6, 6,
      options=stat_opts("value"),
      fieldConfig={"defaults": {"unit": "s", "color": {"mode": "fixed", "fixedColor": "text"}}, "overrides": []})
ts("Log lines / hour", [{"expr": "increase(review_agent_log_lines_total[1h])", "legendFormat": "lines"},
                        {"expr": "increase(review_agent_responses_posted_total[1h])", "legendFormat": "responses"}],
   12, 12, "short", h=6)
y += 6

# --- Host -------------------------------------------------------------------
row("Host")
ts("CPU", [{"expr": '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))', "legendFormat": "busy"}], 0, 8, "percentunit")
ts("Memory", [{"expr": "node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "legendFormat": "used"},
               {"expr": "node_memory_MemTotal_bytes", "legendFormat": "total"}], 8, 8, "bytes")
ts("Disk used", [{"expr": '1 - node_filesystem_avail_bytes{fstype=~"ext4|xfs|btrfs"} / node_filesystem_size_bytes',
                  "legendFormat": "{{mountpoint}}"}], 16, 8, "percentunit")
y += 8

dash = {
    "uid": "ampere-services", "title": "Services by host", "tags": ["ampere-m1", "trading-vm", "overview"],
    # Ad hoc filter: Grafana adds host=<value> to every query. Pick the machine at the top of the dashboard.
    "templating": {"list": [{
        "name": "Filters", "type": "adhoc", "datasource": DS,
        "filters": [{"key": "host", "operator": "=", "value": "ampere-m1", "condition": ""}],
    }]},
    "timezone": "browser", "schemaVersion": 39, "refresh": "1m",
    "time": {"from": "now-6h", "to": "now"}, "editable": False, "panels": panels,
    "links": [{"title": "Node Exporter Full", "type": "link", "url": "/dashboards?query=Node%20Exporter"}],
}
# Import-ready: Grafana's import screen asks which Prometheus data source to use.
dash = {"__inputs": [{"name": "DS_PROMETHEUS", "label": "Prometheus", "type": "datasource",
                      "pluginId": "prometheus", "pluginName": "Prometheus"}], **dash}
out = pathlib.Path(__file__).parent / "dashboards" / "vm-services-overview.json"
out.write_text(json.dumps(dash, indent=1) + "\n")
print(f"wrote {out} ({len(panels)} panels)")
