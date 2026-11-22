import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection

import pytest

from jobhunt import db
from jobhunt.crawl import CrawlReport, crawl
from jobhunt.enums import AppState, Ats
from jobhunt.http import FetchError, PoliteClient
from jobhunt.location import classify
from jobhunt.models import Board, Job

T1 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)


def board(slug: str) -> Board:
    return Board(company=slug.title(), ats=Ats.greenhouse, slug=slug)


def job(slug: str, external_id: str, location: str = "Bucharest, Romania") -> Job:
    return Job(
        id=f"greenhouse:{slug}:{external_id}",
        board=board(slug),
        external_id=external_id,
        title="Engineer",
        url=f"https://example.com/{slug}/{external_id}",
        apply_url=f"https://example.com/{slug}/{external_id}/apply",
        offices=[classify(location)],
        description="",
        posted_at=None,
    )


class FakeAdapter:
    ats = Ats.greenhouse

    def __init__(self, results: dict[str, list[Job] | Exception]) -> None:
        self.results = results

    async def fetch_jobs(self, client: PoliteClient, board: Board) -> list[Job]:
        result = self.results[board.slug]
        if isinstance(result, Exception):
            raise result
        return result

    async def probe(self, client: PoliteClient, slug: str) -> Board | None:
        return None


@pytest.fixture
def conn(tmp_path: Path) -> Connection:
    return db.connect(tmp_path / "test.db")


@pytest.fixture
async def client() -> AsyncIterator[PoliteClient]:
    async with PoliteClient(min_interval=0) as c:
        yield c


def add_boards(conn: Connection, *slugs: str) -> None:
    for slug in slugs:
        db.upsert_board(conn, board(slug))


async def run(
    conn: Connection, client: PoliteClient, results: dict[str, list[Job] | Exception], now: datetime
) -> CrawlReport:
    return await crawl(conn, client, now=now, adapters={Ats.greenhouse: FakeAdapter(results)})


async def test_new_kept_jobs_get_found_state(conn: Connection, client: PoliteClient) -> None:
    add_boards(conn, "a")
    report = await run(conn, client, {"a": [job("a", "1"), job("a", "2")]}, T1)

    assert sorted(report.new_jobs) == ["greenhouse:a:1", "greenhouse:a:2"]
    assert report.boards_ok == 1
    assert report.seen == 2
    assert db.get_state(conn, "greenhouse:a:1") == AppState.found
    assert db.get_state(conn, "greenhouse:a:2") == AppState.found


async def test_location_dropped_jobs_not_stored(conn: Connection, client: PoliteClient) -> None:
    add_boards(conn, "a")
    jobs = [job("a", "1", "Bucharest, Romania"), job("a", "2", "New York, NY")]
    report = await run(conn, client, {"a": jobs}, T1)

    assert report.new_jobs == ["greenhouse:a:1"]
    assert report.seen == 1
    stored = [r[0] for r in conn.execute("SELECT id FROM jobs")]
    assert stored == ["greenhouse:a:1"]


async def test_second_run_reports_no_new_jobs(conn: Connection, client: PoliteClient) -> None:
    add_boards(conn, "a")
    await run(conn, client, {"a": [job("a", "1")]}, T1)
    db.set_state(conn, "greenhouse:a:1", AppState.queued)

    report = await run(conn, client, {"a": [job("a", "1")]}, T2)

    assert report.new_jobs == []
    assert report.seen == 1
    assert db.get_state(conn, "greenhouse:a:1") == AppState.queued
    rows = conn.execute("SELECT last_seen_at FROM jobs").fetchall()
    assert rows == [(T2.isoformat(),)]


async def test_failing_board_does_not_stop_crawl(conn: Connection, client: PoliteClient) -> None:
    add_boards(conn, "a", "b")
    results: dict[str, list[Job] | Exception] = {
        "a": FetchError("https://x/a", 429, "Too Many Requests"),
        "b": [job("b", "1")],
    }
    report = await run(conn, client, results, T1)

    assert report.boards_ok == 1
    assert list(report.boards_failed) == ["greenhouse:a"]
    assert report.boards_failed["greenhouse:a"].startswith("429")
    assert report.new_jobs == ["greenhouse:b:1"]


async def test_unexpected_error_in_one_board_is_recorded(
    conn: Connection, client: PoliteClient
) -> None:
    add_boards(conn, "a", "b")
    results: dict[str, list[Job] | Exception] = {"a": KeyError("title"), "b": [job("b", "1")]}
    report = await run(conn, client, results, T1)

    assert "KeyError" in report.boards_failed["greenhouse:a"]
    assert report.new_jobs == ["greenhouse:b:1"]


async def test_missing_jobs_closed_only_for_successful_boards(
    conn: Connection, client: PoliteClient
) -> None:
    add_boards(conn, "a", "b")
    await run(conn, client, {"a": [job("a", "1"), job("a", "2")], "b": [job("b", "1")]}, T1)

    results: dict[str, list[Job] | Exception] = {
        "a": [job("a", "1")],
        "b": FetchError("https://x/b", 503, "Service Unavailable"),
    }
    report = await run(conn, client, results, T2)

    assert report.closed == 1
    closed = dict(conn.execute("SELECT id, closed_at FROM jobs").fetchall())
    assert closed == {
        "greenhouse:a:1": None,
        "greenhouse:a:2": T2.isoformat(),
        "greenhouse:b:1": None,
    }


async def test_at_most_eight_boards_fetched_at_once(conn: Connection, client: PoliteClient) -> None:
    slugs = [f"b{i}" for i in range(20)]
    add_boards(conn, *slugs)
    active = peak = 0

    class SlowAdapter(FakeAdapter):
        async def fetch_jobs(self, client: PoliteClient, board: Board) -> list[Job]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return []

    report = await crawl(conn, client, now=T1, adapters={Ats.greenhouse: SlowAdapter({})})

    assert report.boards_ok == 20
    assert 1 < peak <= 8


async def test_stored_job_without_state_gets_found(conn: Connection, client: PoliteClient) -> None:
    add_boards(conn, "a")
    db.upsert_job(conn, job("a", "1"), T1)
    report = await run(conn, client, {"a": [job("a", "1")]}, T2)
    assert db.get_state(conn, "greenhouse:a:1") == AppState.found
    assert report.new_jobs == []


async def test_remote_country_jobs_kept_unless_other_language(
    conn: Connection, client: PoliteClient
) -> None:
    add_boards(conn, "a")
    english = job("a", "1", "Remote Germany")
    german = job("a", "2", "Remote Germany")
    german.description = "Fluent German is required."
    report = await run(conn, client, {"a": [english, german]}, T1)
    assert report.new_jobs == ["greenhouse:a:1"]
