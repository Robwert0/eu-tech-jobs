import json
from pathlib import Path

from jobhunt.models import Company


def load_companies(path: Path) -> list[Company]:
    jobs = json.loads(path.read_text())
    seen = set()
    companies = []
    for job in jobs:
        if job["name"] in seen:
            raise ValueError(f"Duplicate company in {path}: {job['name']}")
        seen.add(job["name"])
        companies.append(Company(**job, source="manual"))

    return companies
