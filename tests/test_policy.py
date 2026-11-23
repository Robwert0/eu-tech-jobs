from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection

import pytest

from jobhunt import db
from jobhunt.apply.policy import NotQueued, decide, romanian_today
from jobhunt.enums import AppState, Ats
from jobhunt.location import classify
from jobhunt.models import Board, Job

TODAY = date(2026, 11, 23)
NOW = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path) -> Connection:
    return db.connect(tmp_path / "test.db")


def add_job(
    conn: Connection,
    external_id: str,
    *,
    company: str = "Acme",
    ats: Ats = Ats.greenhouse,
    states: tuple[AppState, ...] = (AppState.queued,),
    at: datetime | None = None,
) -> str:
    board = Board(company=company, ats=ats, slug=company.lower().replace(" ", "-"))
    db.upsert_board(conn, board)
    job = Job(
        id=f"{board.key}:{external_id}",
        board=board,
        external_id=external_id,
        title="Backend Engineer",
        url=f"https://example.com/{external_id}",
        apply_url=f"https://example.com/{external_id}/apply",
        offices=[classify("Bucharest, Romania")],
        description="Python",
        posted_at=None,
    )
    db.upsert_job(conn, job, NOW)
    db.set_state(conn, job.id, AppState.found)
    for state in states:
        db.set_state(conn, job.id, state)
    if at is not None:
        with conn:
            conn.execute("UPDATE events SET at = ? WHERE job_id = ?", (at.isoformat(), job.id))
            conn.execute(
                "UPDATE applications SET updated_at = ? WHERE job_id = ?",
                (at.isoformat(), job.id),
            )
    return job.id


def days_ago(days: int) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days), datetime.min.time(), UTC).replace(hour=12)


def snapshot(conn: Connection) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    return (
        list(conn.execute("SELECT * FROM applications ORDER BY job_id")),
        list(conn.execute("SELECT * FROM events ORDER BY rowid")),
    )


def test_closed_job_never_submitted(conn: Connection) -> None:
    job_id = add_job(conn, "1")
    with conn:
        conn.execute("UPDATE jobs SET closed_at = ? WHERE id = ?", (NOW.isoformat(), job_id))
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == ("manual", "closed")


def test_unsupported_board_goes_manual(conn: Connection) -> None:
    job_id = add_job(conn, "1", ats=Ats.softgarden)
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == (
        "manual",
        "unsupported_form",
    )


@pytest.mark.parametrize("ats", [Ats.greenhouse, Ats.lever, Ats.ashby])
def test_supported_boards_submit(conn: Connection, ats: Ats) -> None:
    job_id = add_job(conn, "1", ats=ats)
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == ("submit", None)


def test_unsupported_board_wins_over_closed(conn: Connection) -> None:
    job_id = add_job(conn, "1", ats=Ats.external)
    with conn:
        conn.execute("UPDATE jobs SET closed_at = ? WHERE id = ?", (NOW.isoformat(), job_id))
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == (
        "manual",
        "unsupported_form",
    )


def test_closed_wins_over_applied_recently(conn: Connection) -> None:
    add_job(conn, "old", states=(AppState.submitted,), at=days_ago(10))
    job_id = add_job(conn, "1")
    with conn:
        conn.execute("UPDATE jobs SET closed_at = ? WHERE id = ?", (NOW.isoformat(), job_id))
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == ("manual", "closed")


def test_company_applied_45_days_ago_goes_manual(conn: Connection) -> None:
    add_job(conn, "old", states=(AppState.submitted,), at=days_ago(45))
    job_id = add_job(conn, "1")
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == (
        "manual",
        "applied_recently",
    )


def test_manual_application_counts_for_60_days(conn: Connection) -> None:
    add_job(conn, "old", ats=Ats.lever, states=(AppState.applied_manually,), at=days_ago(60))
    job_id = add_job(conn, "1")
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == (
        "manual",
        "applied_recently",
    )


def test_company_applied_61_days_ago_does_not_block(conn: Connection) -> None:
    add_job(conn, "old", states=(AppState.applied_manually,), at=days_ago(61))
    job_id = add_job(conn, "1")
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == ("submit", None)


def test_company_match_ignores_case(conn: Connection) -> None:
    add_job(conn, "old", company="ACME", states=(AppState.submitted,), at=days_ago(5))
    job_id = add_job(conn, "1", company="Acme")
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == (
        "manual",
        "applied_recently",
    )


def test_other_company_does_not_block(conn: Connection) -> None:
    add_job(conn, "old", company="Globex", states=(AppState.submitted,), at=days_ago(5))
    job_id = add_job(conn, "1", company="Acme")
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == ("submit", None)


def test_dismissed_or_shortlisted_jobs_do_not_block(conn: Connection) -> None:
    add_job(conn, "a", states=(AppState.manual,), at=days_ago(5))
    add_job(conn, "b", states=(AppState.dismissed,), at=days_ago(5))
    job_id = add_job(conn, "1")
    assert decide(conn, job_id, today=TODAY, submit_enabled=True) == ("submit", None)


def test_cap_counts_calendar_day(conn: Connection) -> None:
    for i in range(2):
        add_job(
            conn,
            f"y{i}",
            company=f"Yesterday {i}",
            states=(AppState.submitted,),
            at=datetime(2026, 11, 22, 21, 30, tzinfo=UTC),
        )
    add_job(
        conn,
        "t0",
        company="Today 0",
        states=(AppState.submitted,),
        at=datetime(2026, 11, 22, 22, 30, tzinfo=UTC),
    )
    job_id = add_job(conn, "1")
    assert decide(conn, job_id, today=TODAY, submit_enabled=True, cap=2) == ("submit", None)
    add_job(
        conn,
        "t1",
        company="Today 1",
        states=(AppState.queued, AppState.submit_unconfirmed, AppState.submitted),
        at=datetime(2026, 11, 23, 9, 0, tzinfo=UTC),
    )
    assert decide(conn, job_id, today=TODAY, submit_enabled=True, cap=2) == ("wait", None)
    assert db.get_state(conn, job_id) == AppState.queued


def test_cap_applies_before_dry_run(conn: Connection) -> None:
    add_job(conn, "t0", company="Globex", states=(AppState.submitted,), at=NOW)
    job_id = add_job(conn, "1")
    assert decide(conn, job_id, today=TODAY, submit_enabled=False, cap=1) == ("wait", None)


def test_submit_disabled_gives_dry_run(conn: Connection) -> None:
    job_id = add_job(conn, "1")
    assert decide(conn, job_id, today=TODAY, submit_enabled=False) == ("dry_run", None)


@pytest.mark.parametrize(
    "states",
    [(), (AppState.manual,), (AppState.queued, AppState.dry_run), (AppState.submitted,)],
)
def test_not_queued_raises(conn: Connection, states: tuple[AppState, ...]) -> None:
    job_id = add_job(conn, "1", states=states)
    with pytest.raises(NotQueued, match=job_id):
        decide(conn, job_id, today=TODAY, submit_enabled=True)


def test_unknown_job_raises(conn: Connection) -> None:
    with pytest.raises(NotQueued):
        decide(conn, "greenhouse:acme:missing", today=TODAY, submit_enabled=True)


def test_decide_changes_nothing(conn: Connection) -> None:
    add_job(conn, "old", states=(AppState.submitted,), at=days_ago(5))
    targets = [
        add_job(conn, "1"),
        add_job(conn, "2", ats=Ats.softgarden),
        add_job(conn, "3", company="Globex"),
    ]
    before = snapshot(conn)
    for job_id in targets:
        for enabled in (True, False):
            decide(conn, job_id, today=TODAY, submit_enabled=enabled, cap=0)
            decide(conn, job_id, today=TODAY, submit_enabled=enabled)
    assert snapshot(conn) == before


@pytest.mark.parametrize(
    ("now", "day"),
    [
        (datetime(2026, 11, 22, 21, 59, tzinfo=UTC), date(2026, 11, 22)),
        (datetime(2026, 11, 22, 22, 0, tzinfo=UTC), date(2026, 11, 23)),
        (datetime(2026, 7, 15, 20, 59, tzinfo=UTC), date(2026, 7, 15)),
        (datetime(2026, 7, 15, 21, 0, tzinfo=UTC), date(2026, 7, 16)),
    ],
)
def test_day_turns_at_romanian_midnight(now: datetime, day: date) -> None:
    assert romanian_today(now) == day
