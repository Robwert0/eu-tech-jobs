"""Core domain models shared by every pipeline stage."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


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


class Company(BaseModel):
    name: str
    domain: str | None = None
    careers_url: str | None = None
    source: str


class Board(BaseModel):
    company: str
    ats: Ats
    slug: str
    host: str | None = None  # e.g. api.eu.lever.co for Lever's EU region

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.slug}"


class Office(BaseModel):
    raw: str
    country: str | None
    location_class: LocationClass


class Job(BaseModel):
    id: str  # f"{ats}:{slug}:{external_id}"
    board: Board
    external_id: str
    title: str
    url: str
    apply_url: str
    offices: list[Office]
    description: str
    posted_at: datetime | None
