# ATS public APIs, polite crawling and idempotency

## What it is (one paragraph)

An ATS (applicant-tracking system) such as Greenhouse, Lever or Ashby hosts a company's job
board. Each publishes an unauthenticated read API so jobs can be syndicated: one GET returns
every open posting as JSON. A **crawler** reads those boards on a schedule and turns each
vendor's format into one internal model. **Polite** crawling means limiting how fast and how
often you hit each host and backing off when told to. **Idempotent** means running the crawl
twice gives the same database state as running it once — no duplicates, no reset decisions.

## How it works in this project (with file paths)

- `src/jobhunt/http.py` — `PoliteClient`: ≥ 1 s between requests to the same host (per-host
  `asyncio.Lock`), up to 3 retries with exponential backoff (0.5 s, 1 s, 2 s) on 429/5xx and
  timeouts, no retry on 404, generic User-Agent `jobhunt/0.1`, raises `FetchError(url, status,
  reason)`. Clock and sleep are injected, so tests run instantly.
- `src/jobhunt/ats/{greenhouse,lever,ashby}.py` — one adapter per vendor, each with
  `fetch_jobs` and `probe`, mapping vendor JSON to `Job` (vendor quirks: Greenhouse `;`-joined
  locations and double-escaped HTML; Lever's EU host, `categories` nesting and epoch-ms dates;
  Ashby's `isRemote` flag). `ats/base.py` holds the `AtsAdapter` Protocol, the `ADAPTERS`
  registry and `detect_board` (exact-host allowlist). `ats/text.py` turns HTML into text.
- `src/jobhunt/location.py` — `classify` + `keep`: only Romania / remote-Romania /
  remote-Europe / remote-global jobs are stored.
- `src/jobhunt/crawl.py` — `crawl()` fetches all boards concurrently (`asyncio.gather`, at most
  8 at once via a semaphore), turns errors into values per board, then writes sequentially:
  `upsert_job`, `found` only for new jobs, `close_missing` only for boards fetched successfully.
- `src/jobhunt/seed/discover.py` — company → board: scan the careers page for board links, else
  guess slugs (`slug_candidates`) and `probe` each adapter.

## Trade-offs I made and why

- **Primary sources over aggregators:** the board is the source of truth, so "gone from the
  board" reliably means closed, and it gives the real application form for later. Aggregators
  (Levels.fyi jobs, LinkedIn) need a login and violate or risk terms of use.
- **Allowlists:** board hosts, careers-page links and "global" location words are exact lists;
  unknown input fails closed. The first live crawl proved why — "Remote - Seoul" was wrongly
  treated as worldwide until location scope became an allowlist.
- **Fetch in parallel, write sequentially:** concurrency where it helps (network waits), simple
  single-writer SQLite.
- **Recorded fixtures + one live run:** fixtures make tests fast and offline; the live run finds
  the inputs nobody imagined.

## Interview questions and my answers (5)

1. **How do you avoid hammering a site?** — Per-host minimum interval, a global concurrency cap,
   bounded retries with exponential backoff, and only retrying transient errors (429/5xx).
2. **What happens when one board is down?** — Its error is recorded per board and the others
   finish; its jobs are *not* closed, because a failed fetch says nothing about what exists.
3. **How do you make the crawl idempotent?** — Upsert by a stable id (`ats:slug:external_id`);
   jobs and decisions live in separate tables, so a re-crawl refreshes facts and never resets state.
4. **Why the adapter pattern?** — Each vendor's quirks stay in one file; everything downstream
   sees one `Job` shape. Adding a source = one class + one registry line.
5. **How do you test code that calls external APIs?** — Record real responses as fixtures, mock
   HTTP with respx, inject fakes for orchestration tests; then verify against live data.

## Words to use

ATS · adapter pattern · registry · Protocol (structural typing) · idempotent upsert · rate
limiting · exponential backoff · bounded concurrency (semaphore) · errors as values · fail
closed · allowlist vs blocklist · source of truth · recorded fixtures · respx
