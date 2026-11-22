# MCP — Model Context Protocol

## What it is (one paragraph)

MCP is an open protocol that lets an AI application (the *client*, e.g. Claude Code) call
functions and read data in another program (the *server*). It is JSON-RPC 2.0 carried over
either **stdio** (the client starts the server as a subprocess and they exchange one JSON message
per line on stdin/stdout) or **streamable HTTP** (the server runs as a web service). A server
offers three kinds of things: **tools** (functions the model can call, with a JSON Schema for
their arguments), **resources** (data the client can read, like files) and **prompts** (reusable
templates). The model never talks to the server directly: the client lists the tools to the
model, the model asks for a call, and the client performs it and returns the result.

## How it works in this project (with file paths)

- `src/jobhunt/mcp_server.py` creates `mcp = MCPServer("jobhunt")` (mcp SDK 2.x; `FastMCP` in
  1.x) and registers one tool with `@mcp.tool()`: `find_jobs(query, location_class, country,
  posted_since_days, min_score, limit) -> list[JobSummary]`. The SDK builds the tool's JSON
  Schema from the **type hints** and its description from the **docstring**.
- `find_jobs` is a thin wrapper: it opens the database from `db.db_path()` (`$JOBHUNT_DB` or
  `data/jobhunt.db`, in `src/jobhunt/db.py`), calls `search_jobs` in `src/jobhunt/queries.py`,
  and closes the connection in `finally`.
- `src/jobhunt/cli.py` → `jobhunt serve` runs `mcp.run("stdio")`. Claude Code is configured with
  `claude mcp add jobhunt -- uv --directory ~/eu-tech-jobs run jobhunt serve`.
- On the wire: `initialize` → `notifications/initialized` → `tools/list` → `tools/call`
  (verified by hand with a scripted client).
- Tests in `tests/test_mcp.py` call `mcp.list_tools()` and `mcp.call_tool()` in-process.

## Trade-offs I made and why

- **Tool, not resource:** searching needs parameters and returns a computed result — that is an
  action, so a tool. A resource would fit a static document (e.g. "today's report").
- **stdio, not HTTP:** one user on one machine; stdio needs no port, no auth, and the client
  manages the process lifetime. HTTP would be needed for a remote or shared server.
- **Thin wrapper over a plain function:** all logic is in `search_jobs`, tested with a temp
  database and a fixed `now`; protocol code stays trivial. The tool omits `now` so the model
  cannot (and need not) set it.
- **Read-only tool for now:** submitting applications through MCP comes later, behind policy
  checks, because a model-triggered side effect must be guarded.

## Interview questions and my answers (5)

1. **What problem does MCP solve?** — Every AI app used to need custom glue per integration. MCP
   standardises it: write a server once and any MCP client can use its tools.
2. **How does the model know how to call your tool?** — Only from the name, the description
   (docstring) and the JSON Schema (type hints). So I list exact allowed values in the docstring;
   a vague description produces wrong calls.
3. **Why can't a stdio server print to stdout?** — stdout *is* the protocol channel; a stray
   `print` corrupts the JSON-RPC stream. Logging goes to stderr.
4. **Where is input validation?** — The SDK validates arguments against the schema before my
   function runs; inside, `search_jobs` treats every filter as optional and parameterises SQL
   (`?` placeholders), so model-supplied text can never become SQL.
5. **How would you test an MCP server?** — Test the domain function directly; then a few
   in-process protocol tests (`list_tools` schema, one `call_tool` against a temp DB) — no
   subprocess needed.

## Words to use

MCP client / server · tool vs resource vs prompt · JSON-RPC 2.0 · stdio transport · streamable
HTTP · JSON Schema from type hints · tool description as a prompt · thin adapter · structured
output · capability negotiation (`initialize`)
