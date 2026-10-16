#!/usr/bin/env bash
# Download community dashboards from grafana.com as-is (with __inputs), ready for
# Grafana Cloud: Dashboards → New → Import → Upload JSON, then pick the grafanacloud-*-prom data source.
set -euo pipefail
cd "$(dirname "$0")/../grafana/dashboards"
for e in 1860:node-exporter-full 12708:nginx 249:process-exporter 13659:blackbox 9578:alertmanager 3662:prometheus-overview; do
  id="${e%%:*}"; f="${e##*:}.json"
  curl -fsSL "https://grafana.com/api/dashboards/$id/revisions/latest/download" -o "$f"
  echo "$f ($(jq -r .title "$f"))"
done
