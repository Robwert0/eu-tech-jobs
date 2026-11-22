# Design

A crawler finds tech jobs that can be done from Romania and keeps them in a local SQLite database.
When I sit down to job-hunt, Claude (in Claude Code) connects to that database through an MCP
server, goes through the new jobs with me, judges each one against my real CV, tailors a CV and
cover letter for the good ones, and keeps track of what I applied to. I submit every application
myself.

## Goals

1. **Find** — a daily crawl of company job boards, keeping only postings workable from Romania.
2. **Review with Claude** — through MCP tools Claude lists new jobs, reads full postings and the
   profile, and gives each a fit score with a short reason.
3. **Tailor** — for the jobs I pick, Claude writes a CV and cover letter from real facts only.
4. **Track** — every decision (shortlisted, skipped, applied, dismissed) is saved, so a job is
   never reviewed twice and the history is always available.

## Non-goals

- No automatic submitting. No LLM calls from the Python code — Claude in Claude Code is the LLM.
- No LinkedIn, no email reading, no salary data stored.
- No hosted service: the MCP server runs locally over stdio.

## Anti-goals (failure even if it "works")

- **Inventing anything** on a CV, cover letter or form answer: no skill, metric, date, employer or
  authorisation that isn't real.
- **Spamming**: many applications to one company, or to roles whose requirements clearly aren't met.
- **Losing track**: an application the database doesn't know about, or a reviewed job showing up
  as new again.
- **Personal data in the repo**: the profile, answers and database are gitignored.

## Approaches considered

1. **Crawler + MCP, Claude does the work (chosen).** Much smaller than a full pipeline, runs on a
   Claude subscription instead of API spend, and every application gets a human review anyway.
2. **Hands-off pipeline** — crawl → match → tailor → auto-submit in Python (LLM API calls, a
   deterministic grounding checker, Playwright form filling). Parked as too big; everything built
   for approach 1 is reusable if it is ever built.
3. **Automation platform (n8n etc.)** — rejected: the point is code that can be read and tested.

## Architecture: two halves, one database

```
cron (daily)                                   me + Claude Code (on demand)
companies ─► discover boards ─► crawl ─►       ┌──────────────────────────────────┐
  location / language / visa filters          │ MCP server (stdio)               │
  ─► dedupe ─► SQLite ◄────────────────────────┤  find_jobs   get_job             │
                                               │  get_profile record_decision     │
                                               │  my_applications                 │
                                               │  prompt: review_jobs             │
                                               └──────────────────────────────────┘
```

The crawler only writes jobs. Claude only reads jobs and writes decisions. The `applications`
table is the record of decisions, so it works as Claude's memory across sessions.

## Company sources

- A hand-edited `companies.json` (a name, optionally a careers URL or an explicit board).
- Companies found by Claude through the official Levels.fyi MCP server during a session.
- **Discovery** finds each company's board from its careers page (links and redirect target) or by
  guessing slugs and probing; companies on job systems without an adapter are reported, ranked by
  how many companies each system would unlock.
- Sites that refuse automated access (401/403, bot challenges) are skipped, never worked around.

## Location rules

Every office in a posting is classified; any one kept office keeps the job.

| Class | Kept when |
|---|---|
| `romania`, `remote-romania` | always |
| `remote-europe` | always |
| `remote-global` | always — only when the location has nothing but global words |
| `remote-europe-country` | remote or hybrid-remote in another European country, and no language besides English (or Romanian) is required |
| `remote-needs-visa` | remote in the US or UK, and the posting offers visa sponsorship, accepts contractors (B2B, Employer of Record) or hires abroad |
| `other` | never |

Each job also carries `visa_sponsorship` (yes / no / not stated) and `contractor_ok`, read from the
posting text with patterns tuned on real postings (links ignored; "sponsor" must concern a visa).

## Decisions and states

| Decision | State | Reason examples |
|---|---|---|
| crawled, not reviewed | `found` | |
| not a fit | `skipped` | `low_fit`, `language`, `location` |
| worth applying | `manual` | `shortlisted` |
| applied | `applied_manually` | |
| declined | `dismissed` | free text |

Every transition is checked against an allowed-transitions table and appended to an `events` audit
log. Unused states (`queued`, `dry_run`, `submitted`, `submit_unconfirmed`) are kept for the parked
pipeline.

## Matching (done by Claude, guided by a rubric)

Claude reads the full posting and the profile, lists requirements as must / nice, marks each
met / partial / missing with the fact ids that back it, and scores:
must = 2, nice = 1; met = 1, partial = 0.5, missing = 0; `round(100 · Σ(w·v) / Σw)`.
A requirement with no backing fact counts as missing. Below 50 → skip (`low_fit`). No seniority
penalty; years-of-experience requirements are judged against real dates.

## Tailoring and the no-invention rule

- `profile/facts.yaml` holds every true fact with an id; `profile/answers.yaml` holds form answers
  (authorisation, notice, location…). Both gitignored; `profile.example/` shows the format.
- Claude may only select, reorder and rephrase facts. Form questions about authorisation, notice or
  start date come only from the answers file; anything missing is asked, never guessed.

## Anti-spam

Before shortlisting, Claude checks the application history for the same company in the last 60
days and says so.

## Crawling etiquette

One shared HTTP client: at least 1 s between requests to the same host, 3 retries with exponential
backoff on 429/5xx/timeouts, a generic `jobhunt/0.1` user agent, at most 8 boards in flight. One
board's failure never stops the others, and a failed board never closes its jobs.

## Order of work

1. ✅ Find: models, location classifier, job-board adapters, discovery, SQLite, `find_jobs`.
2. Copilot: profile + `get_profile`, `get_job`, `record_decision`, `my_applications` / `new_only`,
   `record_external_job`, the `review_jobs` prompt.
3. More sources: Workday, SmartRecruiters, Workable, Amazon, Oracle, SuccessFactors.
4. Daily crawl schedule.
