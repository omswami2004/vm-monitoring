#!/usr/bin/env bash
# Install configs + units from this repo onto the VM. Idempotent.
# Backs up any existing file it replaces as <file>.bak.<timestamp>. Does NOT start anything
# on its own beyond `daemon-reload`; start/enable units explicitly (see README).
set -euo pipefail
cd "$(dirname "$0")/.."
TS="$(date +%Y%m%d%H%M%S)"

put() { # src dst mode owner
  local src="$1" dst="$2" mode="$3" owner="${4:-root:root}"
  if [[ -e "$dst" ]] && ! cmp -s "$src" "$dst"; then cp -a "$dst" "$dst.bak.$TS"; fi
  install -D -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$src" "$dst"
}

# System users (no login shell, no home)
for u in prometheus alertmanager node_exporter blackbox_exporter nginx_exporter process_exporter nginxlog_exporter wa-relay; do
  id "$u" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$u"
done

install -d -o prometheus -g prometheus -m 750 /var/lib/prometheus /etc/prometheus /etc/prometheus/rules
install -d -o alertmanager -g alertmanager -m 750 /var/lib/alertmanager /etc/alertmanager
install -d -o root -g root -m 755 /var/lib/node_exporter /var/lib/node_exporter/textfile
install -d -m 755 /etc/blackbox_exporter /etc/process-exporter /etc/prometheus-nginxlog-exporter
install -d -o wa-relay -g wa-relay -m 750 /etc/wa-relay

put prometheus/prometheus.yml           /etc/prometheus/prometheus.yml            640 root:prometheus
put prometheus/rules/alerts.yml         /etc/prometheus/rules/alerts.yml          640 root:prometheus
put alertmanager/alertmanager.yml       /etc/alertmanager/alertmanager.yml        640 root:alertmanager
put blackbox/blackbox.yml               /etc/blackbox_exporter/blackbox.yml       644
put process-exporter/config.yml         /etc/process-exporter/config.yml          644
put nginxlog-exporter/config.hcl        /etc/prometheus-nginxlog-exporter/config.hcl 644
put textfile/vm-textfile-metrics.sh     /usr/local/bin/vm-textfile-metrics        755
[[ -e /etc/wa-relay/env ]] || install -m 600 -o wa-relay -g wa-relay wa-relay/env.sample /etc/wa-relay/env

for f in systemd/*.service systemd/*.timer; do put "$f" "/etc/systemd/system/$(basename "$f")" 644; done
put systemd/grafana-server.service.d/override.conf /etc/systemd/system/grafana-server.service.d/override.conf 644

# Grafana
put grafana/grafana.ini.override /etc/grafana/grafana.ini 640 root:grafana
put grafana/provisioning/datasources/prometheus.yml /etc/grafana/provisioning/datasources/prometheus.yml 640 root:grafana
put grafana/provisioning/dashboards/vm.yml /etc/grafana/provisioning/dashboards/vm.yml 640 root:grafana
install -d -o root -g grafana -m 750 /var/lib/grafana/dashboards
for d in grafana/dashboards/*.json; do put "$d" "/var/lib/grafana/dashboards/$(basename "$d")" 640 root:grafana; done

systemctl daemon-reload
/usr/local/bin/promtool check config /etc/prometheus/prometheus.yml
/usr/local/bin/amtool check-config /etc/alertmanager/alertmanager.yml
echo "deploy ok ($TS)"
