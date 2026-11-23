import sqlite3
from typing import Literal

from jobhunt.db import get_state, set_state
from jobhunt.enums import AppState
from jobhunt.models import JobSummary
from jobhunt.queries import get_job

Decision = Literal["shortlist", "skip", "applied", "dismiss"]
DECISION_STATE: dict[str, AppState] = {
    "shortlist": AppState.manual,
    "skip": AppState.skipped,
    "applied": AppState.applied_manually,
    "dismiss": AppState.dismissed,
}


class DecisionError(Exception):
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
    if get_state(conn, job_id) is None:
        set_state(conn, job_id, AppState.found)
    set_state(conn, job_id, DECISION_STATE[decision], reason, score)
    return get_job(conn, job_id)
