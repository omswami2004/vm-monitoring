#!/usr/bin/env python3
"""Provision Grafana Cloud: WhatsApp contact point, notification policy, alert rules, dashboards.

Idempotent (fixed UIDs; existing objects are updated). Secrets are read, never printed:
  ~/.config/grafana-cloud/sa-token    service account token (Editor), mode 600
  ~/.config/grafana-cloud/stack-url   e.g. https://happycreek730.grafana.net
  $WA_CLOUD_TOKEN                     wa-relay's CLOUD_TOKEN (from /etc/wa-relay/env)

Run:  WA_CLOUD_TOKEN="$(sudo sed -n 's/^CLOUD_TOKEN=//p' /etc/wa-relay/env)" python3 -I grafana-cloud/provision.py
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

CFG = pathlib.Path.home() / ".config" / "grafana-cloud"
TOKEN = (CFG / "sa-token").read_text().strip()
STACK = (CFG / "stack-url").read_text().strip().rstrip("/")
WA_TOKEN = os.environ.get("WA_CLOUD_TOKEN", "")
REPO = pathlib.Path(__file__).resolve().parent.parent

PROM = "grafanacloud-prom"
FOLDER_UID = "vm-monitoring"
TRADING = "instance-20260824-1239"  # the trading VM's hostname (instance/host label)


def api(method, path, body=None, ok=(200, 201, 202, 204), extra_headers=None):
    req = urllib.request.Request(STACK + path, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Content-Type", "application/json")
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return e.code, None
        msg = e.read().decode(errors="replace")[:400]
        return e.code, {"error": msg}


def must(res, what):
    code, body = res
    if code >= 300:
        sys.exit(f"{what}: HTTP {code} {body}")
    print(f"ok  {what}")
    return body


# ---- folder ----
code, _ = api("GET", f"/api/folders/{FOLDER_UID}")
if code != 200:  # Cloud answers 403, not 404, for a folder that doesn't exist
    must(api("POST", "/api/folders", {"uid": FOLDER_UID, "title": "VM monitoring"}), "folder")
else:
    print("ok  folder (exists)")

# ---- contact point: WhatsApp via wa-relay on ampere-m1 ----
if not WA_TOKEN:
    sys.exit("WA_CLOUD_TOKEN is not set")
cp = {
    "uid": "whatsapp-relay",
    "name": "whatsapp",
    "type": "webhook",
    "settings": {
        "url": "https://stat.axiosiiitl.dev/cloud-alert",
        "httpMethod": "POST",
        "authorization_scheme": "Bearer",
        "authorization_credentials": WA_TOKEN,
        "maxAlerts": 20,
    },
    "disableResolveMessage": False,
}
existing = api("GET", "/api/v1/provisioning/contact-points")[1] or []
if any(c.get("uid") == "whatsapp-relay" for c in existing):
    must(api("PUT", "/api/v1/provisioning/contact-points/whatsapp-relay", cp,
             extra_headers={"X-Disable-Provenance": "true"}), "contact point whatsapp (updated)")
else:
    must(api("POST", "/api/v1/provisioning/contact-points", cp,
             extra_headers={"X-Disable-Provenance": "true"}), "contact point whatsapp")

# ---- contact point: email, for alerts that must not depend on ampere-m1 (its heartbeat) ----
# Must be the email of a member of the Grafana Cloud org (Cloud refuses other addresses).
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
ecp = {"uid": "email-owner", "name": "email", "type": "email",
       "settings": {"addresses": ALERT_EMAIL, "singleEmail": True}, "disableResolveMessage": False}
existing = api("GET", "/api/v1/provisioning/contact-points")[1] or []
if not ALERT_EMAIL:
    print("skip contact point email (set ALERT_EMAIL to a Grafana org member's address)")
elif any(c.get("uid") == "email-owner" for c in existing):
    must(api("PUT", "/api/v1/provisioning/contact-points/email-owner", ecp,
             extra_headers={"X-Disable-Provenance": "true"}), "contact point email (updated)")
else:
    must(api("POST", "/api/v1/provisioning/contact-points", ecp,
             extra_headers={"X-Disable-Provenance": "true"}), "contact point email")

# ---- notification policy: notify=whatsapp → whatsapp, notify=email → email ----
policy = must(api("GET", "/api/v1/provisioning/policies"), "read policy")
routes = [r for r in (policy.get("routes") or []) if r.get("receiver") not in ("whatsapp", "email")]
routes.insert(0, {"receiver": "whatsapp", "object_matchers": [["notify", "=", "whatsapp"]],
                  "group_by": ["alertname", "host", "instance"],
                  "group_wait": "30s", "group_interval": "5m", "repeat_interval": "4h"})
if any(c.get("uid") == "email-owner" for c in api("GET", "/api/v1/provisioning/contact-points")[1] or []):
  routes.insert(1, {"receiver": "email", "object_matchers": [["notify", "=", "email"]],
                  "group_by": ["alertname"], "group_wait": "30s", "group_interval": "5m", "repeat_interval": "12h"})
policy["routes"] = routes
must(api("PUT", "/api/v1/provisioning/policies", policy, extra_headers={"X-Disable-Provenance": "true"}),
     f"notification policy (default receiver stays '{policy.get('receiver')}')")


# ---- alert rules ----
def rule(uid, title, expr, op, threshold, for_, summary, notify="whatsapp", severity="critical", no_data="OK"):
    return {
        "uid": uid, "title": title, "condition": "C", "for": for_,
        "noDataState": no_data, "execErrState": "Error",
        "labels": {"notify": notify, "severity": severity},
        "annotations": {"summary": summary},
        "data": [
            {"refId": "A", "relativeTimeRange": {"from": 600, "to": 0}, "datasourceUid": PROM,
             "model": {"refId": "A", "expr": expr, "instant": True, "intervalMs": 60000, "maxDataPoints": 43200}},
            {"refId": "B", "datasourceUid": "__expr__",
             "model": {"refId": "B", "type": "reduce", "expression": "A", "reducer": "last"}},
            {"refId": "C", "datasourceUid": "__expr__",
             "model": {"refId": "C", "type": "threshold", "expression": "B",
                       "conditions": [{"evaluator": {"type": op, "params": [threshold]}}]}},
        ],
    }


T = f'instance="{TRADING}"'
H = f'host="{TRADING}"'
# NSE market hours, Mon-Fri 09:15-15:30 IST = 03:45-10:00 UTC (PromQL time functions are UTC). Holidays not handled.
MARKET_HOURS = ('on() (day_of_week() >= 1 <= 5) and on() ((hour() * 60 + minute()) >= 225 < 600)')
trading_rules = [
    rule("trading-vm-down", "Trading VM not reporting",
         f'absent_over_time(up{{job="integrations/node_exporter",{T}}}[5m])', "gt", 0, "1m",
         "Trading VM has sent no metrics for 5 minutes (down, network, or Alloy stopped)"),
    rule("trading-autotrader-gone", "auto-trader process not running",
         f'namedprocess_namegroup_num_procs{{groupname="auto-trader",{T}}}', "lt", 1, "1m",
         "auto-trader server process is not running on the trading VM"),
    rule("trading-kotak-bridge-gone", "kotak-bridge process not running",
         f'namedprocess_namegroup_num_procs{{groupname="kotak-bridge",{T}}} and {MARKET_HOURS}', "lt", 1, "2m",
         "kotak-bridge (node index.js) is not running on the trading VM during market hours"),
    rule("trading-probe-failing", "Trading VM service not answering",
         f'probe_success{{{H}}}', "lt", 1, "2m",
         "{{ $labels.service }} on the trading VM is not answering ({{ $labels.instance }})"),
    rule("trading-unit-failed", "Trading VM systemd unit failed",
         f'node_systemd_unit_state{{state="failed",type!="oneshot",{T}}}', "gt", 0, "1m",
         "systemd unit {{ $labels.name }} failed on the trading VM"),
    rule("trading-oneshot-failed", "Trading VM oneshot unit failed",
         f'node_systemd_unit_state{{state="failed",type="oneshot",name!="fwupd-refresh.service",{T}}}', "gt", 0, "1m",
         "oneshot unit {{ $labels.name }} (a timer or batch job) failed on the trading VM", severity="warning"),
    rule("trading-disk-full", "Trading VM disk almost full",
         f'1 - node_filesystem_avail_bytes{{{T},fstype=~"ext4|xfs|btrfs"}} / node_filesystem_size_bytes{{{T},fstype=~"ext4|xfs|btrfs"}}',
         "gt", 0.85, "5m", "Disk {{ $labels.mountpoint }} on the trading VM is over 85% full", severity="warning"),
    rule("trading-memory-low", "Trading VM low on memory",
         f'node_memory_MemAvailable_bytes{{{T}}} / 1048576', "lt", 100, "5m",
         "Trading VM has less than 100 MB of RAM available", severity="warning"),
    rule("trading-oom", "Trading VM OOM kill",
         f'increase(node_vmstat_oom_kill{{{T}}}[10m])', "gt", 0, "0s",
         "The kernel OOM-killed a process on the trading VM in the last 10 minutes"),
]
# If ampere-m1 dies, its Alertmanager and wa-relay die with it, so this one goes to the default (email).
heartbeat_rules = [
    rule("ampere-m1-heartbeat", "ampere-m1 not reporting",
         'absent_over_time(up{job="integrations/node_exporter",instance="ampere-m1"}[10m])', "gt", 0, "1m",
         "ampere-m1 has sent no metrics for 10 minutes. Its local alerts (WhatsApp) are down too.",
         notify="email"),
]

for group, rules in (("trading-vm", trading_rules), ("heartbeat", heartbeat_rules)):
    body = {"title": group, "folderUid": FOLDER_UID, "interval": 60, "rules": rules}
    must(api("PUT", f"/api/v1/provisioning/folder/{FOLDER_UID}/rule-groups/{group}", body,
             extra_headers={"X-Disable-Provenance": "true"}), f"rule group {group} ({len(rules)} rules)")

# ---- dashboards ----
# alertmanager / prometheus-overview dashboards are skipped: their metrics are dropped before remote_write.
for f in ["vm-services-overview", "node-exporter-full", "nginx", "process-exporter", "blackbox"]:
    d = json.loads((REPO / "grafana" / "dashboards" / f"{f}.json").read_text())
    inputs = [{"name": i["name"], "type": "datasource", "pluginId": i.get("pluginId", "prometheus"), "value": PROM}
              for i in d.get("__inputs", []) if i.get("type") == "datasource"]
    d.pop("id", None)
    body = {"dashboard": d, "overwrite": True, "inputs": inputs, "folderUid": FOLDER_UID}
    res = must(api("POST", "/api/dashboards/import", body), f"dashboard {d.get('title')}")
    print(f"    {STACK}{res.get('importedUrl', '')}")
