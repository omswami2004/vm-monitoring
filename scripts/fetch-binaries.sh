#!/usr/bin/env bash
# Download official release binaries (linux-arm64), verify sha256, install to /usr/local/bin.
# Re-run with new versions to upgrade; then `systemctl restart <unit>` one at a time.
set -euo pipefail

ARCH="${ARCH:-arm64}"
# name|repo|version|asset template|checksum file template
COMPONENTS=(
  "prometheus|prometheus/prometheus|3.15.0|prometheus-{v}.linux-${ARCH}.tar.gz|sha256sums.txt"
  "alertmanager|prometheus/alertmanager|0.34.1|alertmanager-{v}.linux-${ARCH}.tar.gz|sha256sums.txt"
  "node_exporter|prometheus/node_exporter|1.12.1|node_exporter-{v}.linux-${ARCH}.tar.gz|sha256sums.txt"
  "blackbox_exporter|prometheus/blackbox_exporter|0.29.0|blackbox_exporter-{v}.linux-${ARCH}.tar.gz|sha256sums.txt"
  "nginx-prometheus-exporter|nginx/nginx-prometheus-exporter|1.5.3|nginx-prometheus-exporter_{v}_linux_${ARCH}.tar.gz|nginx-prometheus-exporter_{v}_checksums.txt"
  "process-exporter|ncabatoff/process-exporter|0.8.7|process-exporter-{v}.linux-${ARCH}.tar.gz|checksums.txt"
  "prometheus-nginxlog-exporter|martin-helmich/prometheus-nginxlog-exporter|1.11.0|prometheus-nginxlog-exporter_{v}_linux_${ARCH}.tar.gz|checksums.txt"
)
ONLY="${1:-}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

for entry in "${COMPONENTS[@]}"; do
  IFS='|' read -r name repo ver asset_t sums_t <<<"$entry"
  [[ -n "$ONLY" && "$ONLY" != "$name" ]] && continue
  asset="${asset_t//\{v\}/$ver}"
  sums="${sums_t//\{v\}/$ver}"
  base="https://github.com/${repo}/releases/download/v${ver}"
  d="$work/$name"; mkdir -p "$d"
  echo "==> $name $ver"
  curl -fsSL -o "$d/$asset" "$base/$asset"
  curl -fsSL -o "$d/sums" "$base/$sums"
  want="$(awk -v a="$asset" '$2==a || $2=="*"a {print $1}' "$d/sums")"
  [[ -n "$want" ]] || { echo "no checksum for $asset" >&2; exit 1; }
  echo "$want  $d/$asset" | sha256sum -c --quiet - || { echo "CHECKSUM MISMATCH $asset" >&2; exit 1; }
  mkdir "$d/x"; tar -xzf "$d/$asset" -C "$d/x"
  for bin in "$name" promtool amtool; do
    f="$(find "$d/x" -type f -name "$bin" | head -1)"
    [[ -n "$f" ]] && install -m 0755 -o root -g root "$f" "/usr/local/bin/$bin"
  done
  echo "    sha256 ok, installed"
done
