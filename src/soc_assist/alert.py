"""Alert context + severity rubric (SPEC §2.1, §2.3).

`AlertContext` is the frozen payload a fired Splunk detection hands the agent.
`BRUTE_FORCE_FIXTURE` is the Phase 2 stand-in; Phase 5 replaces it with the
context captured from a real fired saved search.
"""

from __future__ import annotations

from dataclasses import dataclass

# SPEC §2.3 — deterministic severity ladder, so severity isn't vibes. The agent
# must cite which rung applies and why. (Wording review is a Phase 7 human pass.)
SEVERITY_RUBRIC: tuple[tuple[str, str], ...] = (
    ("info", "Failed logins only, non-privileged account, isolated / low volume."),
    ("low", "Repeated failures from one source, no success, volume too low to be a determined attack."),
    ("medium", "Sustained burst of failures against a single account; no successful login observed."),
    ("high", "Failures followed by a SUCCESS from the same source against the same account — likely compromise."),
    ("critical", "Success involving a privileged/admin account, or signs of lateral movement after the success."),
)

SEVERITY_LEVELS: tuple[str, ...] = tuple(level for level, _ in SEVERITY_RUBRIC)


@dataclass(frozen=True)
class AlertContext:
    """Everything a fired detection hands the agent — nothing more."""

    rule_name: str
    rule_id: str
    detection_spl: str
    fired_at: str  # ISO-8601
    index: str
    severity_hint: str  # the detection rule's static guess; the agent re-triages
    entities: dict[str, str]
    observed_count: int
    earliest: str  # ISO-8601 window the detection searched
    latest: str


# The provisioned detection (Phase 5.3): a saved search in the soc_assist app,
# dispatched on demand for the demo. Search-time rex, so no field-extraction
# add-on is required on the instance.
DETECTION_RULE_NAME = "Auth brute force: failed-login burst followed by a success"
DETECTION_RULE_ID = "soc-assist-bf-001"
DETECTION_SAVED_SEARCH = "soc_assist_bf_detect"
DETECTION_SPL = (
    'index=auth sourcetype=linux_secure ("Failed password" OR "Accepted password") '
    '| rex "(?<action_raw>Failed|Accepted) password for (?<user>\\S+) '
    'from (?<src_ip>\\S+) port" '
    '| eval action=if(action_raw="Failed","failure","success") '
    '| stats count(eval(action="failure")) as failure_count, '
    "values(action) as actions, min(_time) as earliest_time, "
    "max(_time) as latest_time by src_ip, user "
    "| where failure_count > 20"
)


def alert_from_detection_row(row: dict) -> AlertContext:
    """Build the AlertContext from one fired row of the provisioned detection."""

    def iso(epoch: object) -> str:
        from datetime import datetime, timezone

        try:
            return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            return str(epoch)

    actions = row.get("actions", [])
    if isinstance(actions, str):
        actions = [actions]
    return AlertContext(
        rule_name=DETECTION_RULE_NAME,
        rule_id=DETECTION_RULE_ID,
        detection_spl=DETECTION_SPL,
        fired_at=iso(row.get("latest_time")),
        index="auth",
        severity_hint="medium",
        entities={
            "src_ip": str(row.get("src_ip", "")),
            "user": str(row.get("user", "")),
            "actions": ", ".join(actions),
        },
        observed_count=int(float(row.get("failure_count", 0))),
        earliest=iso(row.get("earliest_time")),
        latest=iso(row.get("latest_time")),
    )


# ~50 failed SSH logins from one src_ip against one user, then one success.
# IP from TEST-NET-3 (RFC 5737) so the fixture stays environment-agnostic.
BRUTE_FORCE_FIXTURE = AlertContext(
    rule_name="Auth brute force: failed-login burst followed by a success",
    rule_id="soc-assist-bf-001",
    detection_spl=(
        "index=auth sourcetype=linux_secure "
        '| stats count(eval(action="failure")) as failure_count, '
        "values(action) as actions by src_ip, user "
        "| where failure_count > 20"
    ),
    fired_at="2026-06-10T14:03:27Z",
    index="auth",
    severity_hint="medium",
    entities={
        "src_ip": "203.0.113.42",
        "user": "mwilliams",
        "dest_host": "web-01",
        "actions": "failure, success",
    },
    observed_count=52,
    earliest="2026-06-10T13:48:00Z",
    latest="2026-06-10T14:03:00Z",
)
