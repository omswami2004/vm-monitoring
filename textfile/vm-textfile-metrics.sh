#!/usr/bin/env bash
# Writes node_exporter textfile metrics that node_exporter can't collect itself:
#  - state/restarts of systemd *user* units (review-agent, moshi-hook) for user ubuntu
#  - seconds since the GitHub review agent last wrote to its log
#  - expiry of Let's Encrypt certs on disk (public probes see Cloudflare's edge cert instead)
# Run every minute by vm-textfile-metrics.timer as root.
set -uo pipefail
OUT=/var/lib/node_exporter/textfile/vm.prom
USER_NAME=ubuntu
USER_UNITS=(review-agent.service moshi-hook.service)
REVIEW_LOG=/home/ubuntu/github-review-agent/review.log
tmp="$(mktemp "${OUT}.XXXX")"

{
  echo "# HELP user_unit_active 1 if the systemd user unit is active."
  echo "# TYPE user_unit_active gauge"
  echo "# HELP user_unit_restarts_total Automatic restarts of the user unit (systemd NRestarts)."
  echo "# TYPE user_unit_restarts_total counter"
  for u in "${USER_UNITS[@]}"; do
    props="$(systemctl --user -M "${USER_NAME}@" show "$u" -p ActiveState -p NRestarts 2>/dev/null)"
    state="$(sed -n 's/^ActiveState=//p' <<<"$props")"
    nr="$(sed -n 's/^NRestarts=//p' <<<"$props")"
    [[ "$state" == active ]] && a=1 || a=0
    echo "user_unit_active{user=\"$USER_NAME\",unit=\"$u\"} $a"
    echo "user_unit_restarts_total{user=\"$USER_NAME\",unit=\"$u\"} ${nr:-0}"
  done

  if [[ -f "$REVIEW_LOG" ]]; then
    echo "# HELP review_agent_log_age_seconds Seconds since review.log was last written."
    echo "# TYPE review_agent_log_age_seconds gauge"
    echo "review_agent_log_age_seconds $(( $(date +%s) - $(stat -c %Y "$REVIEW_LOG") ))"
    echo "# HELP review_agent_log_lines_total Lines in review.log (rate = bot activity)."
    echo "# TYPE review_agent_log_lines_total counter"
    echo "review_agent_log_lines_total $(wc -l <"$REVIEW_LOG")"
    echo "# HELP review_agent_responses_posted_total Successful GitHub responses in review.log."
    echo "# TYPE review_agent_responses_posted_total counter"
    echo "review_agent_responses_posted_total $(grep -c 'Successfully posted' "$REVIEW_LOG")"
  fi

  echo "# HELP ssl_cert_file_not_after_seconds Expiry (unix time) of certificates under /etc/letsencrypt/live."
  echo "# TYPE ssl_cert_file_not_after_seconds gauge"
  for c in /etc/letsencrypt/live/*/cert.pem; do
    [[ -f "$c" ]] || continue
    end="$(openssl x509 -enddate -noout -in "$c" | cut -d= -f2)"
    echo "ssl_cert_file_not_after_seconds{cert=\"$(basename "$(dirname "$c")")\"} $(date -d "$end" +%s)"
  done
} >"$tmp"

chmod 644 "$tmp"
mv "$tmp" "$OUT"
