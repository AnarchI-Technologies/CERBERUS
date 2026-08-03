#!/usr/bin/env bash
set -euo pipefail

production_url="${CERBERUS_PRODUCTION_HEALTH_URL:-http://127.0.0.1:10000/healthz}"
staging_url="${CERBERUS_STAGING_HEALTH_URL:-http://127.0.0.1:10001/healthz}"

systemctl is-active --quiet cerberus.service
systemctl is-active --quiet cerberus-staging.service
curl --fail --silent --show-error --max-time 5 "${production_url}" | grep --quiet '"ok":true'
curl --fail --silent --show-error --max-time 5 "${staging_url}" | grep --quiet '"ok":true'
systemctl is-active --quiet cerberus-evaluation.timer
systemctl is-active --quiet cerberus-claw-knowledge-sync.timer

echo "CERBERUS local smoke test passed"
