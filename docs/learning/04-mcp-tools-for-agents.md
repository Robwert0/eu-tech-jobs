# Designing MCP tools for an agent

## What it is (one paragraph)

Milestone 1 gave Claude one read-only tool. Milestone 2 turns the server into a **copilot**:
Claude reads the profile and full postings, scores each job, and writes decisions back. Designing
tools for an LLM is API design where the caller reads only the **name, description and JSON
Schema**, cannot ask a follow-up question, and learns about failures only from the error text it
gets back. A second MCP feature, the **prompt**, holds the session playbook: the order of calls and
the rules. The SQLite database is the agent's **memory between sessions**: nothing Claude decides
survives the conversation unless a tool writes it down.

## How it works in this project (with file paths)

- `src/jobhunt/mcp_server.py` — tools `get_profile`, `get_job`, `record_decision`,
  `my_applications`, `record_external_job` (plus `find_jobs(new_only=True)`), each a thin wrapper:
  open the DB, call a plain function in `profile.py`, `queries.py` or `decisions.py`, close it.
- **Only `ToolError` text reaches Claude.** The SDK (`mcpserver/tools/base.py`) treats any other
  exception as a crash: the model sees just `Error executing tool <name>`. So wrappers translate
  expected failures — `except (JobNotFound, db.InvalidTransition, DecisionError) as e: raise
  ToolError(str(e)) from e` — and Claude reads e.g. "<job>: dismissed -> applied_manually is
  not allowed" and can act on it. Tested in `tests/test_mcp.py`
  (`test_mcp_record_decision_forbidden_move_is_a_clear_error`).
- **Import aliasing.** The tool `record_decision` has the same name as the library function it
  wraps. A plain import is rebound by the `def`, so the tool called itself until `RecursionError`
  — which, being an unexpected exception, showed up as a bare "Error executing tool". Fix:
  `from jobhunt.decisions import record_decision as save_decision` (likewise `get_job as
  fetch_job`, `record_external_job as save_external_job`, `save_documents as write_documents`).
- **Literal → enum.** `Decision = Literal["shortlist", "skip", "applied", "dismiss", ...]` in
  `src/jobhunt/decisions.py` becomes `"enum": [...]` in the tool's JSON Schema, so Claude sees the
  exact legal values and the SDK rejects anything else before my code runs.
- **Validate before write.** `record_decision` checks the score range (0–100), that `skip` has a
  reason, and that the job exists *before* calling `db.set_state`, which itself checks `ALLOWED`
  before its transaction. `test_forbidden_transition_leaves_db_unchanged` in
  `tests/test_decisions.py` asserts state and events are untouched after a refused move.
- **Idempotent external jobs.** `record_external_job` stores a job Claude found through another
  MCP server (Levels.fyi) under id `external:{source}:{external_id}` on a board with
  `Ats.external` (no adapter, so the crawl never fetches it). Saving it twice returns the existing
  row (`test_record_external_job_twice_is_idempotent`).
- **The prompt.** `@mcp.prompt() review_jobs(days, limit)` renders `REVIEW_JOBS`: profile first,
  `find_jobs(new_only=true)` + `get_job`, the fit-score rubric (requirements as must = 2 / nice = 1,
  met = 1 / partial = 0.5 / missing = 0, each backed by a fact id; below 50 → skip `low_fit`), the
  60-day same-company check via `my_applications`, and "record applied only after confirmation".
- **Never invent facts, by design.** `src/jobhunt/profile.py` `load_profile` reads
  `facts.yaml` (each `Fact` has an `id` and a `kind` Literal; duplicate ids raise `ProfileError`)
  and `answers.yaml`. Every tool description that writes text (`get_profile`, `save_documents`,
  `job_questions`) says these are the only facts allowed; in the rubric a requirement with no
  backing fact id counts as missing.

## Trade-offs I made and why

- **Rules in a prompt + human review, not a grounding checker in code.** A checker that verifies
  every CV sentence against `facts.yaml` is hard to make reliable. The prompt states the rules and
  Robert reviews every CV and decision. Lost: a guarantee — a model can ignore a prompt, so the
  writes that matter (`applied`, `auto_apply`) still need a human or a code-level check.
- **Prompt, not a tool, for the workflow:** a prompt is user-started and only text; tools stay
  small, single-purpose and testable. Tests assert the rendered prompt contains the rules
  (`test_review_jobs_prompt_carries_the_rules`).
- **Descriptions over docstrings:** `description=` on `@mcp.tool` keeps model-facing wording
  separate from the Python signature.
- **Gap still open:** `get_profile` and `record_external_job` don't translate their errors, so a
  missing `facts.yaml` reaches Claude as a generic error instead of the clear `ProfileError` text.

## Interview questions and my answers (5)

1. **Why convert exceptions to `ToolError`?** — The SDK hides unexpected exception text from the
   model (it could leak internals). For expected failures I want the message to reach Claude so it
   can recover: retry with a reason, pick another job, or tell the user.
2. **Tools vs prompts?** — Tools are model-called actions with a schema; prompts are templates a
   user picks (a slash command in Claude Code). Logic in tools, workflow and policy in a prompt.
3. **How do you stop an agent from writing bad state?** — Enums in the schema, validation before
   any write, a state machine that refuses illegal moves in one transaction, and a test that the
   DB is unchanged after a refusal.
4. **How does an agent remember across sessions?** — It doesn't; the database does.
   `new_only` gives "what I haven't seen", `my_applications` gives history and the 60-day check.
5. **How do you stop an LLM inventing CV content?** — One source of truth (`facts.yaml` with ids),
   instructions in every relevant tool description, a rubric that cites fact ids, and a human
   reviewing output. A prompt reduces the risk; only review removes it.

## Words to use

agent tool design · tool description as interface · JSON Schema enum from `Literal` · `ToolError`
vs unexpected error · actionable errors · name shadowing / import alias · validate before write ·
idempotent create · MCP prompt (playbook) · grounding · single source of truth · human in the loop
· agent memory (external state)
