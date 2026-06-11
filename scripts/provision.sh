#!/usr/bin/env bash
# One-time provisioning on your Splunk instance (SPEC §3/§5/§6 server side):
#   - app `soc_assist` (owns generated dashboards + the detection)
#   - saved search `soc_assist_bf_detect` (the brute-force detection)
#   - index `soc_audit` (the agent audit trail; HEC token left to you — see notes)
#
# Endpoints verified against Splunk Enterprise 10.2.2.
#
# Usage:
#   SPLUNK_URL=https://your-splunk:8089 SPLUNK_AUTH=admin:changeme ./scripts/provision.sh
#
# Least-privilege write identity (recommended, manual — choices are yours):
#   1. role with NO capabilities, e.g. soc_writer
#   2. add it to the app's write ACL:
#      POST $SPLUNK_URL/services/apps/local/soc_assist/acl \
#        -d perms.write=admin,power,soc_writer -d sharing=app -d owner=nobody
#   3. user in that role; mint its token (absolute ISO expiry):
#      POST $SPLUNK_URL/services/authorization/tokens \
#        -d name=<user> -d audience=soc-assist -d expires_on=2026-12-31T00:00:00+0000
#   4. put that token in .env as SPLUNK_WRITE_TOKEN (never the admin one).
# HEC for the audit plane: create a token targeting index soc_audit and set
#   SPLUNK_AUDIT_HEC_URL / SPLUNK_AUDIT_HEC_TOKEN / SPLUNK_AUDIT_INDEX in .env.

set -euo pipefail

SPLUNK_URL="${SPLUNK_URL:?set SPLUNK_URL to the management URL, e.g. https://localhost:8089}"
SPLUNK_AUTH="${SPLUNK_AUTH:?set SPLUNK_AUTH to user:password}"
APP="${SPLUNK_WRITE_APP:-soc_assist}"
AUTH_INDEX="${SOC_ASSIST_INDEX:-auth}"
AUDIT_INDEX="${SPLUNK_AUDIT_INDEX:-soc_audit}"

post() { # path, then -d args; 409 (already exists) is fine
  local path="$1"; shift
  curl -ks -u "$SPLUNK_AUTH" -X POST "$SPLUNK_URL$path" "$@" \
    -o /dev/null -w "   POST $path -> HTTP %{http_code}\n"
}

echo "-> app $APP"
post /services/apps/local -d "name=$APP" -d "label=SOC Assist"

echo "-> audit index $AUDIT_INDEX"
post /services/data/indexes -d "name=$AUDIT_INDEX"

echo "-> detection saved search soc_assist_bf_detect (app-shared, dispatched on demand)"
# Keep in sync with DETECTION_SPL in src/soc_assist/alert.py.
SPL='index='"$AUTH_INDEX"' sourcetype=linux_secure ("Failed password" OR "Accepted password")
| rex "(?<action_raw>Failed|Accepted) password for (?<user>\S+) from (?<src_ip>\S+) port"
| eval action=if(action_raw="Failed","failure","success")
| stats count(eval(action="failure")) as failure_count, values(action) as actions, min(_time) as earliest_time, max(_time) as latest_time by src_ip, user
| where failure_count > 20'
post "/servicesNS/nobody/$APP/saved/searches" \
  -d name=soc_assist_bf_detect \
  --data-urlencode "search=$SPL" \
  -d dispatch.earliest_time=-24h@h \
  -d dispatch.latest_time=now \
  -d is_scheduled=0

echo "Done. Load data with scripts/load_sample.sh, then: python -m soc_assist.run --live"
