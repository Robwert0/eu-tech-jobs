from datetime import date
from pathlib import Path

import pytest

from jobhunt.profile import Fact, ProfileError, load_profile, profile_dir

EXAMPLE = Path(__file__).parent.parent / "profile.example"


def test_example_profile_loads() -> None:
    profile = load_profile(EXAMPLE)
    by_id = {f.id: f for f in profile.facts}
    assert by_id["role-acme"] == Fact(
        id="role-acme",
        kind="role",
        text="Backend Engineer at Acme Payments",
        employer="Acme Payments",
        start=date(2022, 3, 1),
        end=None,
        skills=["Python", "PostgreSQL", "AWS"],
    )
    assert by_id["link-github"].skills == []
    assert profile.answers["notice_period"] == "1 month"


def test_duplicate_fact_id_rejected(tmp_path: Path) -> None:
    (tmp_path / "facts.yaml").write_text(
        "- {id: a, kind: skill, text: Python}\n- {id: a, kind: skill, text: Go}\n"
    )
    (tmp_path / "answers.yaml").write_text("location: Bucharest\n")
    with pytest.raises(ProfileError, match=f"a is already in {tmp_path}/facts.yaml"):
        load_profile(tmp_path)


def test_unknown_fact_kind_rejected(tmp_path: Path) -> None:
    (tmp_path / "facts.yaml").write_text("- {id: a, kind: hobby, text: Chess}\n")
    (tmp_path / "answers.yaml").write_text("location: Bucharest\n")
    with pytest.raises(ProfileError, match="facts.yaml"):
        load_profile(tmp_path)


def test_missing_answers_file_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "facts.yaml").write_text("- {id: a, kind: skill, text: Python}\n")
    with pytest.raises(ProfileError, match="answers.yaml"):
        load_profile(tmp_path)


def test_missing_facts_file_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "answers.yaml").write_text("location: Bucharest\n")
    with pytest.raises(ProfileError, match="facts.yaml"):
        load_profile(tmp_path)


def test_profile_dir_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBHUNT_PROFILE", raising=False)
    assert profile_dir() == Path("profile")
    monkeypatch.setenv("JOBHUNT_PROFILE", "/tmp/elsewhere")
    assert profile_dir() == Path("/tmp/elsewhere")
