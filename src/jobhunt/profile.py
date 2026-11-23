import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from jobhunt.models import Fact, Profile


class ProfileError(Exception):
    pass


def profile_dir() -> Path:
    return Path(os.environ.get("JOBHUNT_PROFILE", "profile"))


def load_profile(path: Path) -> Profile:
    facts_file = path / "facts.yaml"
    answers_file = path / "answers.yaml"

    if not facts_file.exists():
        raise ProfileError(f"{facts_file} does not exist")
    if not answers_file.exists():
        raise ProfileError(f"{answers_file} does not exist")

    seen: set[str] = set()
    facts: list[Fact] = []
    for entry in yaml.safe_load(facts_file.read_text()):
        try:
            fact = Fact(**entry)
        except ValidationError as e:
            raise ProfileError(f"{facts_file}: {e}") from e

        if fact.id in seen:
            raise ProfileError(f"{fact.id} is already in {facts_file}")
        seen.add(fact.id)
        facts.append(fact)

    answers = yaml.safe_load(answers_file.read_text())
    return Profile(facts=facts, answers=answers)
