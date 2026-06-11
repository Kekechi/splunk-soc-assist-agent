#!/usr/bin/env bash
# Load the sample brute-force dataset into your Splunk instance (SPEC §5.2).
#
# Verified against Splunk Enterprise 10.2.2: the simple receiver endpoint takes
# the raw file in one POST — no HEC token, no file copy to the server.
#
# Usage:
#   SPLUNK_URL=https://your-splunk:8089 SPLUNK_AUTH=admin:changeme ./scripts/load_sample.sh
#
# Notes:
# - The dataset's syslog timestamps carry no year; Splunk infers the current
#   year and parses them as the server's timezone. The detection and dashboard
#   use a -24h window, so load shortly before you run the demo.
# - Re-running this script ingests the events again (Splunk does not dedupe).

set -euo pipefail

SPLUNK_URL="${SPLUNK_URL:?set SPLUNK_URL to the management URL, e.g. https://localhost:8089}"
SPLUNK_AUTH="${SPLUNK_AUTH:?set SPLUNK_AUTH to user:password (rights: create index + ingest)}"
INDEX="${SOC_ASSIST_INDEX:-auth}"

HERE="$(cd "$(dirname "$0")/.." && pwd)"
FILE="$HERE/sample-data/auth_bruteforce.log"

echo "-> ensuring index '$INDEX' exists (409 means it already does)"
curl -ks -u "$SPLUNK_AUTH" -X POST "$SPLUNK_URL/services/data/indexes" -d "name=$INDEX" \
  -o /dev/null -w "   HTTP %{http_code}\n"

echo "-> ingesting $(wc -l < "$FILE") events (index=$INDEX sourcetype=linux_secure host=web-01)"
curl -ksf -u "$SPLUNK_AUTH" -X POST \
  "$SPLUNK_URL/services/receivers/simple?index=$INDEX&sourcetype=linux_secure&host=web-01" \
  --data-binary @"$FILE"
echo
echo "Done. Verify with: index=$INDEX sourcetype=linux_secure earliest=-24h | stats count"
