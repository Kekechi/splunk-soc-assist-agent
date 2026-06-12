"""Alert context + severity rubric (SPEC §2.1, §2.3).

`AlertContext` is the frozen payload a fired Splunk detection hands the agent.
What/where the detection is now lives in a `DetectionProfile` (profile.py);
`BRUTE_FORCE_FIXTURE` is the offline stand-in built off the default profile.
"""

from __future__ import annotations

from dataclasses import dataclass

from .profile import DEFAULT_PROFILE, DetectionProfile

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


def alert_from_detection_row(row: dict, profile: DetectionProfile) -> AlertContext:
    """Build the AlertContext from one fired row of the profile's detection.

    The row is expected to carry the profile's `entity_fields` columns plus the
    fixed `failure_count`, `actions`, `earliest_time`, `latest_time` columns.
    """

    def iso(epoch: object) -> str:
        from datetime import datetime, timezone

        try:
            return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            return str(epoch)

    actions = row.get("actions", [])
    if isinstance(actions, str):
        actions = [actions]
    entities = {f: str(row.get(f, "")) for f in profile.entity_fields}
    entities["actions"] = ", ".join(actions)
    return AlertContext(
        rule_name=profile.rule_name,
        rule_id=profile.rule_id,
        detection_spl=profile.detection_spl,
        fired_at=iso(row.get("latest_time")),
        index=profile.index,
        severity_hint=profile.severity_hint,
        entities=entities,
        observed_count=int(float(row.get("failure_count", 0))),
        earliest=iso(row.get("earliest_time")),
        latest=iso(row.get("latest_time")),
    )


# ~50 failed SSH logins from one src_ip against one user, then one success.
# Offline stand-in for a fired detection, carrying the default profile's identity
# and (rex-bearing) detection SPL so the agent copies a pattern that actually
# extracts fields. IP from TEST-NET-3 (RFC 5737) so the fixture stays env-agnostic.
BRUTE_FORCE_FIXTURE = AlertContext(
    rule_name=DEFAULT_PROFILE.rule_name,
    rule_id=DEFAULT_PROFILE.rule_id,
    detection_spl=DEFAULT_PROFILE.detection_spl,
    fired_at="2026-06-10T14:03:27Z",
    index=DEFAULT_PROFILE.index,
    severity_hint=DEFAULT_PROFILE.severity_hint,
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
