import sqlite3
from datetime import UTC, datetime
from typing import Literal

from jobhunt.db import get_state, set_state, upsert_board, upsert_job
from jobhunt.documents import load_documents
from jobhunt.enums import AppState, Ats
from jobhunt.location import classify
from jobhunt.models import Board, Job, JobSummary
from jobhunt.queries import JobNotFound, get_job
from jobhunt.seed.discover import slug_candidates

Decision = Literal["shortlist", "skip", "applied", "dismiss", "auto_apply"]
DECISION_STATE: dict[str, AppState] = {
    "shortlist": AppState.manual,
    "skip": AppState.skipped,
    "applied": AppState.applied_manually,
    "dismiss": AppState.dismissed,
    "auto_apply": AppState.queued,
}


class DecisionError(Exception):
    pass


class MissingDocuments(DecisionError):
    pass


def record_decision(
    conn: sqlite3.Connection,
    job_id: str,
    decision: Decision,
    *,
    score: int | None = None,
    reason: str | None = None,
) -> JobSummary:
    if score is not None and (score < 0 or score > 100):
        raise DecisionError("Score must be between 0 and 100")
    if decision == "skip" and not reason:
        raise DecisionError("skip needs a reason, e.g. low_fit, language, or location")
    if decision == "shortlist" and reason is None:
        reason = "shortlisted"
    get_job(conn, job_id)
    if decision == "auto_apply" and load_documents(job_id) is None:
        raise MissingDocuments(
            f"{job_id} has no saved CV and cover letter; call save_documents first"
        )
    if get_state(conn, job_id) is None:
        set_state(conn, job_id, AppState.found)
    set_state(conn, job_id, DECISION_STATE[decision], reason, score)
    return get_job(conn, job_id)


def record_external_job(
    conn: sqlite3.Connection,
    *,
    source: str,
    external_id: str,
    company: str,
    title: str,
    url: str,
    location: str,
    description: str = "",
    posted_at: datetime | None = None,
) -> JobSummary:
    job_id = f"{Ats.external}:{source}:{external_id}"
    try:
        return get_job(conn, job_id)
    except JobNotFound:
        pass
    board = Board(company=company, ats=Ats.external, slug=f"{source}/{slug_candidates(company)[0]}")
    upsert_board(conn, board)
    job = Job(
        id=job_id,
        board=board,
        external_id=external_id,
        title=title,
        url=url,
        apply_url=url,
        offices=[classify(location)],
        description=description,
        posted_at=posted_at,
    )
    upsert_job(conn, job, datetime.now(UTC))
    set_state(conn, job_id, AppState.found)
    return get_job(conn, job_id)
