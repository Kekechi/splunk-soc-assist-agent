"""Deployment profile — the one config bundle that points soc-assist at data.

Everything that used to be hardcoded for the brute-force demo (which index and
sourcetype hold the data, the on-demand detection SPL, the field-extraction rex,
which detection columns are entities, and a plain-language description of how the
log pipeline *looks on Splunk*) lives here, in one `DetectionProfile`.

Load order:
- `SOC_ASSIST_PROFILE` set  -> read that TOML file (a real deployment's profile).
- unset                     -> `DEFAULT_PROFILE`, the synthetic auth/brute-force
                               demo, so the offline run needs zero config.

`.env` stays what it is — secrets and endpoints, grouped by plane. The profile is
*what to detect and where*, kept separate so a real (deployment-specific) profile
is gitignored while `profiles/example.toml` ships as a public, env-agnostic sample.

The detection SPL must emit, per fired row: the `entity_fields` columns plus the
fixed columns `failure_count`, `actions`, `earliest_time`, `latest_time` (this is
the contract `alert.alert_from_detection_row` reads).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class DetectionProfile:
    """Where the data lives, what the detection is, and how the pipeline looks."""

    index: str
    sourcetype: str
    detection_spl: str  # emits entity_fields + failure_count/actions/earliest_time/latest_time
    rex: str  # inline field extraction reused by the dashboard panels
    rule_name: str
    rule_id: str
    # How the data shows up on Splunk, in plain language: which sourcetypes are
    # signal vs noise, the key fields, where the auth text lives. Injected into
    # the agent's system prompt so it queries narrowly instead of dragnetting.
    pipeline_profile: str
    noise_filter: str = ""  # extra base constraint, e.g. "appname=sshd" ("" = none)
    entity_fields: tuple[str, ...] = ("src_ip", "user")
    severity_hint: str = "medium"
    earliest: str = "-24h"
    latest: str = "now"
    saved_search: str | None = None  # parked: dispatch a provisioned alert instead

    def windowed_detection_spl(self) -> str:
        """`detection_spl` with the time window applied as inline modifiers.

        Inserts `earliest=/latest=` into the base search (before the first pipe),
        deterministically — so the on-demand run is bounded without depending on
        the agent to add the window itself. A profile whose SPL already sets a
        window is left untouched.
        """
        spl = self.detection_spl
        if "earliest=" in spl:
            return spl
        window = f" earliest={self.earliest} latest={self.latest} "
        pipe = spl.find("|")
        if pipe == -1:
            return spl + window
        return spl[:pipe].rstrip() + window + spl[pipe:]


# Required keys a TOML profile must define (the rest have dataclass defaults).
_REQUIRED = ("index", "sourcetype", "detection_spl", "rex", "rule_name", "rule_id",
             "pipeline_profile")
# Keys we accept from TOML — anything else is a typo we want to surface, not ignore.
_OPTIONAL = ("noise_filter", "entity_fields", "severity_hint", "earliest", "latest",
             "saved_search")


def load_profile() -> DetectionProfile:
    """The active deployment profile: the `SOC_ASSIST_PROFILE` TOML, or the default."""
    load_dotenv()
    path = os.environ.get("SOC_ASSIST_PROFILE", "").strip()
    if not path:
        return DEFAULT_PROFILE
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    missing = [k for k in _REQUIRED if k not in data]
    if missing:
        raise ValueError(f"{path}: profile is missing required keys: {missing}")
    unknown = set(data) - set(_REQUIRED) - set(_OPTIONAL)
    if unknown:
        raise ValueError(f"{path}: unknown profile keys (typo?): {sorted(unknown)}")

    if "entity_fields" in data:
        data["entity_fields"] = tuple(data["entity_fields"])
    # Fresh build (not replace(DEFAULT_PROFILE, ...)) so omitted optional keys take the
    # dataclass defaults — e.g. no `saved_search` means on-demand, not the demo's search.
    return DetectionProfile(**data)


# ── The built-in default: the synthetic brute-force demo (SPEC §2.1, §5.3) ─────
# Matches BRUTE_FORCE_FIXTURE and the provisioned soc_assist_bf_detect saved
# search. Search-time rex, so no field-extraction add-on is required.
DEFAULT_PROFILE = DetectionProfile(
    index="auth",
    sourcetype="linux_secure",
    detection_spl=(
        'index=auth sourcetype=linux_secure ("Failed password" OR "Accepted password") '
        '| rex "(?<action_raw>Failed|Accepted) password for (invalid user )?(?<user>\\S+) '
        'from (?<src_ip>\\S+) port" '
        '| eval action=if(action_raw="Failed","failure","success") '
        '| stats count(eval(action="failure")) as failure_count, '
        "values(action) as actions, min(_time) as earliest_time, "
        "max(_time) as latest_time by src_ip, user "
        "| where failure_count > 20"
    ),
    rex=(
        'rex "(?<action_raw>Failed|Accepted) password for (invalid user )?(?<user>\\S+) '
        'from (?<src_ip>\\S+) port"'
    ),
    rule_name="Auth brute force: failed-login burst followed by a success",
    rule_id="soc-assist-bf-001",
    severity_hint="medium",
    entity_fields=("src_ip", "user"),
    saved_search="soc_assist_bf_detect",
    pipeline_profile=(
        "Auth events are in `index=auth sourcetype=linux_secure` — raw sshd syslog. "
        "Fields are NOT auto-extracted; the username, source IP and outcome only exist "
        "after applying this rex to the raw text:\n"
        '    rex "(?<action_raw>Failed|Accepted) password for (invalid user )?'
        '(?<user>\\S+) from (?<src_ip>\\S+) port"\n'
        "Use that exact rex in your own corroboration queries before filtering on "
        "`user`/`src_ip`/`action_raw`, or they will silently return zero rows. "
        '"Failed password" = a failed login, "Accepted password" = a success.'
    ),
)
