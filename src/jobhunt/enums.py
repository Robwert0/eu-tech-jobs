from enum import StrEnum


class Ats(StrEnum):
    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    softgarden = "softgarden"
    external = "external"
    other = "other"


class LocationClass(StrEnum):
    romania = "romania"
    remote_romania = "remote-romania"
    remote_europe = "remote-europe"
    remote_global = "remote-global"
    remote_europe_country = "remote-europe-country"
    remote_needs_visa = "remote-needs-visa"
    other = "other"


KEPT_CLASSES: frozenset[LocationClass] = frozenset(
    {
        LocationClass.romania,
        LocationClass.remote_romania,
        LocationClass.remote_europe,
        LocationClass.remote_global,
        LocationClass.remote_europe_country,
        LocationClass.remote_needs_visa,
    }
)


class AppState(StrEnum):
    found = "found"
    skipped = "skipped"
    queued = "queued"
    dry_run = "dry_run"
    manual = "manual"
    submitted = "submitted"
    submit_unconfirmed = "submit_unconfirmed"
    applied_manually = "applied_manually"
    dismissed = "dismissed"
