import json
from pathlib import Path

from jobhunt.models import Board, Company


def load_companies(path: Path) -> list[Company]:
    entries = json.loads(path.read_text())
    seen = set()
    companies = []
    for entry in entries:
        if entry["name"] in seen:
            raise ValueError(f"Duplicate company in {path}: {entry['name']}")
        seen.add(entry["name"])
        fields = {**entry, "source": "manual"}
        if "board" in entry:
            fields["board"] = Board(company=entry["name"], **entry["board"])
        companies.append(Company(**fields))

    return companies
