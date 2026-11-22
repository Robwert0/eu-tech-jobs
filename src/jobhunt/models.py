from datetime import datetime

from pydantic import BaseModel

from jobhunt.enums import Ats, LocationClass


class Board(BaseModel):
    company: str
    ats: Ats
    slug: str
    host: str | None = None

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.slug}"


class Company(BaseModel):
    name: str
    domain: str | None = None
    careers_url: str | None = None
    source: str
    board: Board | None = None


class Office(BaseModel):
    raw: str
    country: str | None
    location_class: LocationClass


class Job(BaseModel):
    id: str
    board: Board
    external_id: str
    title: str
    url: str
    apply_url: str
    offices: list[Office]
    description: str
    posted_at: datetime | None
