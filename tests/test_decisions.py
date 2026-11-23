from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection

import pytest

from jobhunt import db
from jobhunt.decisions import DecisionError, record_decision, record_external_job
from jobhunt.enums import AppState, Ats
from jobhunt.location import classify
from jobhunt.models import Board, Job
from jobhunt.queries import JobNotFound, get_job, list_applications, search_jobs

NOW = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
BOARD = Board(company="Acme", ats=Ats.greenhouse, slug="acme")


def make_job(external_id: str = "1") -> Job:
    return Job(
        id=f"greenhouse:acme:{external_id}",
        board=BOARD,
        external_id=external_id,
        title="Backend Engineer",
        url=f"https://example.com/{external_id}",
        apply_url=f"https://example.com/{external_id}/apply",
        offices=[classify("Bucharest, Romania")],
        description="Python",
        posted_at=None,
    )


@pytest.fixture
def conn(tmp_path: Path) -> Connection:
    c = db.connect(tmp_path / "test.db")
    db.upsert_board(c, BOARD)
    return c


def crawled(conn: Connection, external_id: str = "1") -> str:
    job = make_job(external_id)
    db.upsert_job(conn, job, NOW)
    db.set_state(conn, job.id, AppState.found)
    return job.id


def events(conn: Connection, job_id: str) -> list[tuple[str | None, str, str | None]]:
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT from_state, to_state, reason FROM events WHERE job_id = ? ORDER BY rowid",
            (job_id,),
        )
    ]


def test_shortlist_sets_manual_with_score(conn: Connection) -> None:
    job_id = crawled(conn)
    summary = record_decision(conn, job_id, "shortlist", score=82)
    assert (summary.state, summary.score) == ("manual", 82)
    assert events(conn, job_id)[-1] == ("found", "manual", "shortlisted")


def test_shortlist_keeps_a_given_reason(conn: Connection) -> None:
    job_id = crawled(conn)
    record_decision(conn, job_id, "shortlist", score=70, reason="strong Python match")
    assert events(conn, job_id)[-1][2] == "strong Python match"


def test_skip_requires_reason(conn: Connection) -> None:
    job_id = crawled(conn)
    with pytest.raises(DecisionError, match="reason"):
        record_decision(conn, job_id, "skip", score=30)
    assert db.get_state(conn, job_id) == AppState.found


def test_skip_records_reason(conn: Connection) -> None:
    job_id = crawled(conn)
    summary = record_decision(conn, job_id, "skip", score=30, reason="low_fit")
    assert summary.state == "skipped"
    assert events(conn, job_id)[-1] == ("found", "skipped", "low_fit")


def test_applied_straight_from_found(conn: Connection) -> None:
    job_id = crawled(conn)
    assert record_decision(conn, job_id, "applied").state == "applied_manually"


def test_dismiss_with_reason(conn: Connection) -> None:
    job_id = crawled(conn)
    summary = record_decision(conn, job_id, "dismiss", reason="on-site only")
    assert summary.state == "dismissed"
    assert events(conn, job_id)[-1] == ("found", "dismissed", "on-site only")


def test_forbidden_transition_leaves_db_unchanged(conn: Connection) -> None:
    job_id = crawled(conn)
    record_decision(conn, job_id, "dismiss", reason="not interested")
    before = events(conn, job_id)
    with pytest.raises(db.InvalidTransition, match="dismissed"):
        record_decision(conn, job_id, "applied")
    assert db.get_state(conn, job_id) == AppState.dismissed
    assert events(conn, job_id) == before


@pytest.mark.parametrize("score", [-1, 101])
def test_score_out_of_range_rejected(conn: Connection, score: int) -> None:
    job_id = crawled(conn)
    with pytest.raises(DecisionError, match="0"):
        record_decision(conn, job_id, "shortlist", score=score)
    assert db.get_state(conn, job_id) == AppState.found


def test_unknown_job_raises(conn: Connection) -> None:
    with pytest.raises(JobNotFound, match="greenhouse:acme:nope"):
        record_decision(conn, "greenhouse:acme:nope", "shortlist")


def test_job_without_state_is_treated_as_found(conn: Connection) -> None:
    job = make_job("x")
    db.upsert_job(conn, job, NOW)
    assert record_decision(conn, job.id, "shortlist", score=60).state == "manual"


def save_everai(conn: Connection, **overrides: str) -> str:
    fields = {
        "source": "levels.fyi",
        "external_id": "lv-123",
        "company": "EverAI",
        "title": "LLM Engineer",
        "url": "https://www.levels.fyi/jobs?jobId=lv-123",
        "location": "Remote - Europe",
        "description": "Fine-tune open-weight LLMs.",
    } | overrides
    return record_external_job(conn, **fields).id


def test_record_external_job_is_tracked(conn: Connection) -> None:
    job_id = save_everai(conn)

    assert job_id == "external:levels.fyi:lv-123"
    detail = get_job(conn, job_id)
    assert (detail.company, detail.title, detail.state) == ("EverAI", "LLM Engineer", "found")
    assert detail.location_class == "remote-europe"
    assert detail.description == "Fine-tune open-weight LLMs."
    assert job_id in [j.id for j in search_jobs(conn, new_only=True, now=NOW)]

    record_decision(conn, job_id, "applied")
    rows = list_applications(conn, company="EverAI", since_days=60)
    assert [(r.job.id, r.state) for r in rows] == [(job_id, "applied_manually")]


def test_record_external_job_twice_is_idempotent(conn: Connection) -> None:
    job_id = save_everai(conn)
    record_decision(conn, job_id, "shortlist", score=80)
    before = events(conn, job_id)

    again = save_everai(conn, title="Changed title")

    assert again == job_id
    assert get_job(conn, job_id).title == "LLM Engineer"
    assert db.get_state(conn, job_id) == AppState.manual
    assert events(conn, job_id) == before


def test_external_jobs_keep_their_own_company(conn: Connection) -> None:
    first = save_everai(conn)
    second = save_everai(conn, external_id="lv-456", company="Kraken")
    assert get_job(conn, first).company == "EverAI"
    assert get_job(conn, second).company == "Kraken"
