# jobhunt

A daily pipeline that finds tech jobs in Romania / remote-EU / remote-global on
company job boards (Greenhouse, Lever, Ashby), scores them against my real CV,
tailors a CV and cover letter per job, and applies where the fit is high —
exposed as MCP tools.

## Run

```bash
uv sync
uv run pytest
uv run jobhunt --help
```

Personal data (`profile/`, `data/`, `.env`) is gitignored and never committed.

## Credits

Crawling and ATS-parsing logic is ported from
[spanish-top-tech-companies](https://github.com/pablougarte/spanish-top-tech-companies)
(MIT, © 2026 Pablo Ugarte). Its `data/` directory is under a separate licence and
is not used here.
