import asyncio
import sqlite3
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel

from jobhunt import db
from jobhunt.ats.base import ADAPTERS, AtsAdapter
from jobhunt.enums import AppState, Ats
from jobhunt.filters import keep_job
from jobhunt.http import FetchError, PoliteClient
from jobhunt.models import Board, Job

MAX_CONCURRENT_BOARDS = 8


class CrawlReport(BaseModel):
    boards_ok: int = 0
    boards_failed: dict[str, str] = {}
    new_jobs: list[str] = []
    seen: int = 0
    closed: int = 0


async def crawl(
    conn: sqlite3.Connection,
    client: PoliteClient,
    *,
    now: datetime,
    adapters: Mapping[Ats, AtsAdapter] = ADAPTERS,
) -> CrawlReport:
    boards = db.list_boards(conn)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BOARDS)

    async def fetch(board: Board) -> list[Job] | Exception:
        try:
            async with semaphore:
                return await adapters[board.ats].fetch_jobs(client, board)
        except Exception as e:
            return e

    results = await asyncio.gather(*(fetch(board) for board in boards))
    report = CrawlReport()
    for board, result in zip(boards, results, strict=True):
        if isinstance(result, Exception):
            if isinstance(result, FetchError):
                report.boards_failed[board.key] = str(result)
            else:
                report.boards_failed[board.key] = f"{type(result).__name__}: {result}"
            continue
        report.boards_ok += 1
        kept = [job for job in result if keep_job(job)]
        for job in kept:
            is_new = db.upsert_job(conn, job, now)
            if db.get_state(conn, job.id) is None:
                db.set_state(conn, job.id, AppState.found)
            if is_new:
                report.new_jobs.append(job.id)

        report.seen += len(kept)
        report.closed += db.close_missing(conn, board.key, {job.id for job in kept}, now)

    return report
