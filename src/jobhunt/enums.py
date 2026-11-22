"""Fixed vocabularies shared by every pipeline stage."""

from enum import StrEnum


class Ats(StrEnum):
    """Applicant-tracking system hosting a company's job board."""

    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    other = "other"


class LocationClass(StrEnum):
    romania = "romania"
    remote_romania = "remote-romania"
    remote_europe = "remote-europe"
    remote_global = "remote-global"
    other = "other"


KEPT_CLASSES: frozenset[LocationClass] = frozenset(
    {
        LocationClass.romania,
        LocationClass.remote_romania,
        LocationClass.remote_europe,
        LocationClass.remote_global,
    }
)


class AppState(StrEnum):
    found = "found"  # crawled, not yet scored
    skipped = "skipped"
    queued = "queued"
    dry_run = "dry_run"
    manual = "manual"
    submitted = "submitted"
    submit_unconfirmed = "submit_unconfirmed"
    applied_manually = "applied_manually"
    dismissed = "dismissed"
