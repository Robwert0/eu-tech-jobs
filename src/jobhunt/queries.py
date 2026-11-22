"""Searching stored jobs: the logic behind the `find_jobs` MCP tool."""

import sqlite3
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from jobhunt.enums import KEPT_CLASSES, LocationClass
from jobhunt.models import Job


class JobSummary(BaseModel):
    id: str
    title: str
    company: str
    offices: list[str]
    location_class: str
    url: str
    apply_url: str
    posted_at: datetime | None
    score: int | None
    state: str | None


def search_jobs(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    location_class: list[str] | None = None,
    country: str | None = None,
    posted_since_days: int | None = None,
    min_score: int | None = None,
    limit: int = 20,
    now: datetime | None = None,
) -> list[JobSummary]:
    """Open jobs matching every given filter, best score first, then newest."""
    now = now or datetime.now(UTC)
    rows = conn.execute(
        """SELECT j.json, a.state, a.score
           FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
           WHERE j.closed_at IS NULL"""
    )

    matches: list[tuple[Job, str | None, int | None]] = []
    for job_json, state, score in rows:
        job = Job.model_validate_json(job_json)
        if query and not _mentions(job, query):
            continue
        if location_class and not any(o.location_class in location_class for o in job.offices):
            continue
        if country and not any(o.country == country for o in job.offices):
            continue
        if posted_since_days is not None and (
            job.posted_at is None or job.posted_at < now - timedelta(days=posted_since_days)
        ):
            continue
        if min_score is not None and (score is None or score < min_score):
            continue
        matches.append((job, state, score))

    matches.sort(key=_best_first)
    return [_summary(job, state, score) for job, state, score in matches[:limit]]


def _mentions(job: Job, query: str) -> bool:
    needle = query.lower()
    return needle in job.title.lower() or needle in job.description.lower()


def _best_first(match: tuple[Job, str | None, int | None]) -> tuple[bool, int, bool, float]:
    job, _, score = match
    posted = job.posted_at.timestamp() if job.posted_at else 0.0
    return (score is None, -(score or 0), job.posted_at is None, -posted)


def _summary(job: Job, state: str | None, score: int | None) -> JobSummary:
    kept = [o.location_class for o in job.offices if o.location_class in KEPT_CLASSES]
    return JobSummary(
        id=job.id,
        title=job.title,
        company=job.board.company,
        offices=[o.raw for o in job.offices],
        location_class=str(kept[0] if kept else LocationClass.other),
        url=job.url,
        apply_url=job.apply_url,
        posted_at=job.posted_at,
        score=score,
        state=state,
    )
