# SQLite and the application state machine

## What it is (one paragraph)

SQLite is a full SQL database stored in a single file, embedded in the program (Python's
`sqlite3` module) — no server to run. A **state machine** describes the states something can be
in and which moves between them are allowed; anything else is rejected. An **events table** (an
append-only audit log) records every move, so the current state is explained by its history.
Together they make a pipeline safe to re-run: stages read state, decide, and write a recorded,
validated transition.

## How it works in this project (with file paths)

- `src/jobhunt/db.py` — schema in five tables: `companies`, `boards` (key `ats:slug`), `jobs`
  (id, full `Job` as JSON, `first_seen_at`, `last_seen_at`, `closed_at`), `applications`
  (`job_id`, `state`, `reason`, `score`) and `events` (`job_id`, `at`, `from_state`, `to_state`,
  `reason`). `connect()` turns on WAL and foreign keys; the schema version lives in
  `PRAGMA user_version`.
- `ALLOWED` maps each state to its legal next states (`found → skipped | queued | manual | …`;
  `submitted` has none). `set_state()` checks it, raises `InvalidTransition` otherwise, and
  writes the new state **and** an event in one transaction.
- `upsert_job()` returns whether the job is new; `crawl()` (`src/jobhunt/crawl.py`) sets `found`
  only then. `close_missing()` marks vanished jobs with `closed_at` instead of deleting them.
- `src/jobhunt/queries.py` — `search_jobs()` reads open jobs with a `LEFT JOIN` on applications
  (jobs with no decision still appear) and `?` placeholders.
- States are `StrEnum`s in `src/jobhunt/enums.py`, stored as plain strings.

## Trade-offs I made and why

- **Facts and decisions in separate tables:** re-crawling rewrites `jobs` freely but never
  touches `applications`, so a `queued` or `submitted` job can't be reset by a crawl.
- **Job stored as JSON, filtered in Python:** the model evolves without migrations, and a few
  thousand rows filter instantly. At larger scale I'd move filters into SQL with indexes.
- **Soft delete (`closed_at`):** history survives, a reappearing posting re-opens, and the
  report can show what closed.
- **Transitions in code, not just a CHECK constraint:** gives a clear exception with the job id
  and both states; the event log records the reason.
- **SQLite, not Postgres:** one user, one machine, one file to back up; concurrency is a single
  writer by design.

## Interview questions and my answers (5)

1. **Why an events table if you already store the state?** — The state says *where* a job is;
   events say *how it got there* and *why* — debugging, the daily report, and trust in automation.
2. **What stops a re-run from re-applying?** — Only legal transitions are possible (`submitted`
   is terminal), new jobs are detected by `upsert_job`, and the crawl never writes state for
   known jobs.
3. **Why `LEFT JOIN`?** — To include jobs that have no application row yet; an inner join would
   silently hide them.
4. **How do you prevent SQL injection?** — Parameter placeholders (`?`) for every value; SQL text
   is never built from input.
5. **How do you evolve the schema?** — A version number in `PRAGMA user_version`; `connect()`
   applies the steps from the stored version up to the current one.

## Words to use

state machine · allowed transitions · terminal state · audit log / event sourcing (light) ·
idempotency · upsert (`INSERT … ON CONFLICT DO UPDATE`) · soft delete · LEFT JOIN · parameterised
query · WAL · transaction · schema version / migration
