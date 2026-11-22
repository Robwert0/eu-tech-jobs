"""Fixed vocabularies shared by every pipeline stage."""

from enum import StrEnum


class Ats(StrEnum):
    """Applicant-tracking system hosting a company's job board."""

    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    softgarden = "softgarden"
    other = "other"


class LocationClass(StrEnum):
    romania = "romania"
    remote_romania = "remote-romania"
    remote_europe = "remote-europe"
    remote_global = "remote-global"
    # Remote (or hybrid-remote) in a European country other than Romania, UK included
    # (e.g. "Remote - Germany"): worth applying, but the employer may want local residence or a
    # B2B contract. Manual list, not auto-applied (spec amendment 2026-11-22).
    remote_europe_country = "remote-europe-country"
    # Remote in a country where a Romanian needs a visa (US, UK). Kept only when the posting
    # signals visa sponsorship, contractors (Robert has a Romanian firm) or hiring abroad.
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
    found = "found"  # crawled, not yet scored
    skipped = "skipped"
    queued = "queued"
    dry_run = "dry_run"
    manual = "manual"
    submitted = "submitted"
    submit_unconfirmed = "submit_unconfirmed"
    applied_manually = "applied_manually"
    dismissed = "dismissed"
