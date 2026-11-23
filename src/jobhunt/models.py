from datetime import date, datetime
from typing import Literal

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


class Fact(BaseModel):
    id: str
    kind: Literal["role", "bullet", "metric", "skill", "project", "education", "link"]
    text: str
    employer: str | None = None
    start: date | None = None
    end: date | None = None
    skills: list[str] = []


class Profile(BaseModel):
    facts: list[Fact]
    answers: dict[str, str]
