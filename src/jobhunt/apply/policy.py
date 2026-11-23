import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from jobhunt.enums import AppState, Ats
from jobhunt.models import Job

Verdict = Literal["submit", "dry_run", "manual", "wait"]

SUPPORTED_ATS = frozenset({Ats.greenhouse, Ats.lever, Ats.ashby})
APPLIED_STATES = (AppState.submitted, AppState.applied_manually)
COOLDOWN_DAYS = 60


class NotQueued(Exception):
    pass


def decide(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    today: date,
    submit_enabled: bool,
    cap: int = 10,
) -> tuple[Verdict, str | None]:
    row = conn.execute(
        """SELECT j.json, j.closed_at, a.state
           FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
           WHERE j.id = ?""",
        (job_id,),
    ).fetchone()
    if row is None or row[2] != AppState.queued:
        state = row[2] if row else "unknown"
        raise NotQueued(f"{job_id} is {state}, not queued")
    job_json, closed_at, _ = row
    job = Job.model_validate_json(job_json)
    if job.board.ats not in SUPPORTED_ATS:
        return "manual", "unsupported_form"
    if closed_at is not None:
        return "manual", "closed"
    if _applied_recently(conn, job.board.company, today):
        return "manual", "applied_recently"
    if _submitted_on(conn, today) >= cap:
        return "wait", None
    if not submit_enabled:
        return "dry_run", None
    return "submit", None


def _day(at: str) -> date:
    return datetime.fromisoformat(at).astimezone(UTC).date()


def _applied_recently(conn: sqlite3.Connection, company: str, today: date) -> bool:
    since = today - timedelta(days=COOLDOWN_DAYS)
    placeholders = ", ".join("?" for _ in APPLIED_STATES)
    rows = conn.execute(
        f"""SELECT j.json, e.at
            FROM events e JOIN jobs j ON j.id = e.job_id
            WHERE e.to_state IN ({placeholders})""",
        [str(s) for s in APPLIED_STATES],
    )
    return any(
        _day(at) >= since
        and Job.model_validate_json(job_json).board.company.lower() == company.lower()
        for job_json, at in rows
    )


def _submitted_on(conn: sqlite3.Connection, day: date) -> int:
    rows = conn.execute(
        "SELECT job_id, at FROM events WHERE to_state = ?", (str(AppState.submitted),)
    )
    return len({job_id for job_id, at in rows if _day(at) == day})
