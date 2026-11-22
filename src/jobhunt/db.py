"""SQLite storage: the single source of truth for every pipeline stage.

Jobs (facts about the world, refreshed by each crawl) are kept apart from
applications (our decisions about a job), so re-crawling never touches state.
Every state change is appended to ``events`` as an audit log.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from jobhunt.enums import AppState
from jobhunt.models import Board, Company, Job

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE companies (
    name TEXT PRIMARY KEY,
    domain TEXT,
    careers_url TEXT,
    source TEXT NOT NULL
);
CREATE TABLE boards (
    key TEXT PRIMARY KEY,               -- ats:slug
    company TEXT NOT NULL,
    ats TEXT NOT NULL,
    slug TEXT NOT NULL,
    host TEXT
);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    board_key TEXT NOT NULL REFERENCES boards(key),
    json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX jobs_board ON jobs(board_key);
CREATE TABLE applications (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id),
    state TEXT NOT NULL,
    reason TEXT,
    score INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE events (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    at TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT
);
"""

S = AppState
# None = no application row yet. Terminal states (skipped, submitted,
# applied_manually, dismissed) have no outgoing transitions.
ALLOWED: dict[AppState | None, frozenset[AppState]] = {
    None: frozenset({S.found}),
    S.found: frozenset(
        {S.skipped, S.queued, S.dry_run, S.manual, S.submitted, S.submit_unconfirmed}
    ),
    S.queued: frozenset({S.dry_run, S.manual, S.submitted, S.submit_unconfirmed, S.skipped}),
    S.dry_run: frozenset({S.submitted, S.submit_unconfirmed, S.manual}),
    S.manual: frozenset({S.applied_manually, S.dismissed}),
    S.submit_unconfirmed: frozenset({S.submitted, S.applied_manually, S.dismissed}),
}


class InvalidTransition(Exception):
    def __init__(self, job_id: str, from_state: AppState | None, to_state: AppState) -> None:
        super().__init__(f"{job_id}: {from_state} -> {to_state} is not allowed")
        self.job_id, self.from_state, self.to_state = job_id, from_state, to_state


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the database and bring its schema up to date."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        with conn:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return conn


def upsert_company(conn: sqlite3.Connection, company: Company) -> None:
    with conn:
        conn.execute(
            """INSERT INTO companies (name, domain, careers_url, source) VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 domain = COALESCE(excluded.domain, domain),
                 careers_url = COALESCE(excluded.careers_url, careers_url)""",
            (company.name, company.domain, company.careers_url, company.source),
        )


def upsert_board(conn: sqlite3.Connection, board: Board) -> None:
    with conn:
        conn.execute(
            """INSERT INTO boards (key, company, ats, slug, host) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET company = excluded.company, host = excluded.host""",
            (board.key, board.company, str(board.ats), board.slug, board.host),
        )


def upsert_job(conn: sqlite3.Connection, job: Job, seen_at: datetime) -> bool:
    """Insert or refresh a job. Returns True if it was not stored before.

    Never touches ``applications``; a job seen again is re-opened if it had closed.
    """
    with conn:
        existed = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job.id,)).fetchone()
        conn.execute(
            """INSERT INTO jobs (id, board_key, json, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 json = excluded.json, last_seen_at = excluded.last_seen_at, closed_at = NULL""",
            (
                job.id,
                job.board.key,
                job.model_dump_json(),
                seen_at.isoformat(),
                seen_at.isoformat(),
            ),
        )
    return existed is None


def get_state(conn: sqlite3.Connection, job_id: str) -> AppState | None:
    row = conn.execute("SELECT state FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    return AppState(row[0]) if row else None


def set_state(
    conn: sqlite3.Connection, job_id: str, state: AppState, reason: str | None = None
) -> None:
    """Move a job's application to ``state``, recording an event. Raises InvalidTransition."""
    current = get_state(conn, job_id)
    if state not in ALLOWED.get(current, frozenset()):
        raise InvalidTransition(job_id, current, state)
    now = datetime.now(UTC).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO applications (job_id, state, reason, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                 state = excluded.state, reason = excluded.reason,
                 updated_at = excluded.updated_at""",
            (job_id, str(state), reason, now),
        )
        conn.execute(
            "INSERT INTO events (job_id, at, from_state, to_state, reason) VALUES (?, ?, ?, ?, ?)",
            (job_id, now, str(current) if current else None, str(state), reason),
        )


def close_missing(
    conn: sqlite3.Connection, board_key: str, seen_ids: set[str], at: datetime
) -> int:
    """Mark open jobs of ``board_key`` that were not in this crawl as closed."""
    open_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM jobs WHERE board_key = ? AND closed_at IS NULL", (board_key,)
        )
    ]
    missing = [i for i in open_ids if i not in seen_ids]
    with conn:
        conn.executemany(
            "UPDATE jobs SET closed_at = ? WHERE id = ?", [(at.isoformat(), i) for i in missing]
        )
    return len(missing)
