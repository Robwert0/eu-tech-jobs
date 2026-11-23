import sqlite3
from datetime import UTC, datetime, timedelta

from jobhunt import filters
from jobhunt.enums import KEPT_CLASSES, AppState, LocationClass
from jobhunt.models import ApplicationRow, Job, JobDetail, JobSummary

BEST_FIRST = [
    LocationClass.romania,
    LocationClass.remote_romania,
    LocationClass.remote_europe,
    LocationClass.remote_global,
    LocationClass.remote_europe_country,
    LocationClass.remote_needs_visa,
]


class JobNotFound(Exception):
    pass


def search_jobs(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    location_class: list[str] | None = None,
    country: str | None = None,
    posted_since_days: int | None = None,
    min_score: int | None = None,
    contractor_ok: bool | None = None,
    new_only: bool = False,
    limit: int = 20,
    now: datetime | None = None,
) -> list[JobSummary]:
    now = now or datetime.now(UTC)
    rows = conn.execute(
        """SELECT j.json, a.state, a.score
           FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
           WHERE j.closed_at IS NULL"""
    )

    matches: list[tuple[Job, str | None, int | None]] = []
    for job_json, state, score in rows:
        if new_only and state not in (None, AppState.found):
            continue
        job = Job.model_validate_json(job_json)
        if query and not _mentions(job, query):
            continue
        if location_class and not any(o.location_class in location_class for o in job.offices):
            continue
        if country and not any(o.country == country.upper() for o in job.offices):
            continue
        if posted_since_days is not None and (
            job.posted_at is None or job.posted_at < now - timedelta(days=posted_since_days)
        ):
            continue
        if min_score is not None and (score is None or score < min_score):
            continue
        if contractor_ok is not None and filters.contractor_ok(_text(job)) != contractor_ok:
            continue
        matches.append((job, state, score))

    matches.sort(key=_best_first)
    return [_summary(job, state, score) for job, state, score in matches[:limit]]


def get_job(conn: sqlite3.Connection, job_id: str) -> JobDetail:
    row = conn.execute(
        """SELECT j.json, a.state, a.reason, a.score, j.closed_at
        FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
        WHERE j.id = ?""",
        (job_id,),
    ).fetchone()
    if row is None:
        raise JobNotFound(f"{job_id} was not found.")

    job_json, state, reason, score, closed_at = row
    job = Job.model_validate_json(job_json)
    summary = _summary(job, state, score)
    return JobDetail(
        **summary.model_dump(),
        description=job.description,
        reason=reason,
        closed=closed_at is not None,
    )


def _text(job: Job) -> str:
    return f"{job.title}\n{job.description}"


def _mentions(job: Job, query: str) -> bool:
    haystack = f"{job.title} {job.description}".lower()
    return all(word in haystack for word in query.lower().split())


def _best_first(match: tuple[Job, str | None, int | None]) -> tuple[bool, int, bool, float]:
    job, _, score = match
    posted = job.posted_at.timestamp() if job.posted_at else 0.0
    return (score is None, -(score or 0), job.posted_at is None, -posted)


def list_applications(
    conn: sqlite3.Connection,
    *,
    state: list[str] | None = None,
    company: str | None = None,
    since_days: int | None = None,
    now: datetime | None = None,
) -> list[ApplicationRow]:
    now = now or datetime.now(UTC)
    rows = conn.execute(
        """SELECT j.json, a.state, a.reason, a.score, a.updated_at
           FROM applications a JOIN jobs j ON j.id = a.job_id
           ORDER BY a.updated_at DESC"""
    )
    found: list[ApplicationRow] = []
    for job_json, row_state, reason, score, updated in rows:
        if state is None and row_state == AppState.found:
            continue
        if state is not None and row_state not in state:
            continue
        updated_at = datetime.fromisoformat(updated)
        if since_days is not None and updated_at < now - timedelta(days=since_days):
            continue
        job = Job.model_validate_json(job_json)
        if company is not None and job.board.company.lower() != company.lower():
            continue
        found.append(
            ApplicationRow(
                job=_summary(job, row_state, score),
                state=row_state,
                reason=reason,
                updated_at=updated_at,
            )
        )
    return found


def _summary(job: Job, state: str | None, score: int | None) -> JobSummary:
    kept = sorted(
        (o.location_class for o in job.offices if o.location_class in KEPT_CLASSES),
        key=BEST_FIRST.index,
    )
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
        visa_sponsorship=filters.visa_sponsorship(_text(job)),
        contractor_ok=filters.contractor_ok(_text(job)),
    )
