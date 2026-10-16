#!/usr/bin/env bash
# Download community dashboards from grafana.com and pin their datasource to uid "prometheus".
set -euo pipefail
cd "$(dirname "$0")/../grafana/dashboards"
# id:file
for e in 1860:node-exporter-full 12708:nginx 249:process-exporter 13659:blackbox 9578:alertmanager 3662:prometheus-overview; do
  id="${e%%:*}"; f="${e##*:}.json"
  curl -fsSL "https://grafana.com/api/dashboards/$id/revisions/latest/download" -o "$f.tmp"
  python3 -I - "$f.tmp" "$f" <<'PY'
import json, re, sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
s = re.sub(r'\$\{DS_[A-Z0-9_]+\}', 'prometheus', s)
d = json.loads(s)
d.pop('__inputs', None); d.pop('__requires', None); d.pop('__elements', None)
d['id'] = None
json.dump(d, open(dst, 'w'), indent=1)
PY
  rm -f "$f.tmp"; echo "$f ($(jq -r .title "$f"))"
done
