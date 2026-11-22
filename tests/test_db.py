from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobhunt import db
from jobhunt.models import AppState, Ats, Board, Job, LocationClass, Office

T1 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
BOARD = Board(company="Acme", ats=Ats.greenhouse, slug="acme")


def make_job(external_id: str = "1") -> Job:
    return Job(
        id=f"greenhouse:acme:{external_id}",
        board=BOARD,
        external_id=external_id,
        title="Backend Engineer",
        url=f"https://example.com/jobs/{external_id}",
        apply_url=f"https://example.com/jobs/{external_id}/apply",
        offices=[Office(raw="Bucharest, Romania", country="RO",
                        location_class=LocationClass.romania)],
        description="Python",
        posted_at=None,
    )


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    c = db.connect(tmp_path / "test.db")
    db.upsert_board(c, BOARD)
    yield c
    c.close()


def test_upsert_job_is_idempotent(conn) -> None:  # type: ignore[no-untyped-def]
    job = make_job()
    assert db.upsert_job(conn, job, T1) is True
    assert db.upsert_job(conn, job, T2) is False
    rows = conn.execute("SELECT first_seen_at, last_seen_at FROM jobs").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == T1.isoformat()
    assert rows[0][1] == T2.isoformat()


def test_upsert_preserves_application_state(conn) -> None:  # type: ignore[no-untyped-def]
    job = make_job()
    db.upsert_job(conn, job, T1)
    db.set_state(conn, job.id, AppState.found)
    db.set_state(conn, job.id, AppState.queued)
    db.upsert_job(conn, job, T2)
    state = conn.execute("SELECT state FROM applications WHERE job_id=?", (job.id,)).fetchone()
    assert state[0] == "queued"


def test_invalid_transition_rejected(conn) -> None:  # type: ignore[no-untyped-def]
    job = make_job()
    db.upsert_job(conn, job, T1)
    db.set_state(conn, job.id, AppState.found)
    db.set_state(conn, job.id, AppState.submitted)
    with pytest.raises(db.InvalidTransition):
        db.set_state(conn, job.id, AppState.queued)


def test_set_state_writes_event(conn) -> None:  # type: ignore[no-untyped-def]
    job = make_job()
    db.upsert_job(conn, job, T1)
    db.set_state(conn, job.id, AppState.found)
    db.set_state(conn, job.id, AppState.skipped, reason="low_fit")
    events = conn.execute(
        "SELECT from_state, to_state, reason FROM events ORDER BY rowid").fetchall()
    assert [tuple(e) for e in events] == [(None, "found", None), ("found", "skipped", "low_fit")]


def test_close_missing_marks_only_absent_jobs(conn) -> None:  # type: ignore[no-untyped-def]
    a, b = make_job("1"), make_job("2")
    db.upsert_job(conn, a, T1)
    db.upsert_job(conn, b, T1)
    assert db.close_missing(conn, "greenhouse:acme", {a.id}, T2) == 1
    closed = dict(conn.execute("SELECT id, closed_at FROM jobs").fetchall())
    assert closed[a.id] is None
    assert closed[b.id] == T2.isoformat()
