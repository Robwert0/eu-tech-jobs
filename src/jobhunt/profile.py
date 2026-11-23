import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jobhunt.models import Fact, Profile


class ProfileError(Exception):
    pass


def profile_dir() -> Path:
    return Path(os.environ.get("JOBHUNT_PROFILE", "profile"))


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ProfileError(f"{path} is not valid YAML: {e}") from e


def load_profile(path: Path) -> Profile:
    facts_file = path / "facts.yaml"
    answers_file = path / "answers.yaml"

    if not facts_file.exists():
        raise ProfileError(f"{facts_file} does not exist")
    if not answers_file.exists():
        raise ProfileError(f"{answers_file} does not exist")

    seen: set[str] = set()
    facts: list[Fact] = []
    entries = _read_yaml(facts_file)
    if not isinstance(entries, list):
        raise ProfileError(f"{facts_file} must be a list of facts")
    for entry in entries:
        try:
            fact = Fact(**entry)
        except ValidationError as e:
            raise ProfileError(f"{facts_file}: {e}") from e

        if fact.id in seen:
            raise ProfileError(f"{fact.id} is already in {facts_file}")
        seen.add(fact.id)
        facts.append(fact)

    answers = _read_yaml(answers_file)
    if not isinstance(answers, dict):
        raise ProfileError(f"{answers_file} must map each question to an answer")
    try:
        return Profile(facts=facts, answers=answers)
    except ValidationError as e:
        raise ProfileError(f"{answers_file}: every answer must be text. {e}") from e
