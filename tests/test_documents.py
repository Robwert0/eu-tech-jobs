from pathlib import Path

import pytest
import yaml
from pypdf import PdfReader

from jobhunt.documents import application_dir, load_documents, save_documents

CV = "# Jane Doe\n\nBackend Engineer\n\n## Experience\n\n- Built a payment service in **Python**\n"
LETTER = "Dear Acme team,\n\nI would like to apply for the Backend Engineer role.\n"


@pytest.fixture(autouse=True)
def applications(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "applications"
    monkeypatch.setenv("JOBHUNT_APPLICATIONS", str(root))
    return root


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def test_application_dir_is_safe_for_job_ids(applications: Path) -> None:
    assert application_dir("greenhouse:acme:123") == applications / "greenhouse_acme_123"
    assert application_dir("external:levels.fyi:a/b") == applications / "external_levels.fyi_a_b"


async def test_save_documents_writes_markdown_answers_and_pdfs() -> None:
    docs = await save_documents(
        "greenhouse:acme:1", CV, LETTER, answers={"why_us": "I like payments."}
    )

    folder = application_dir("greenhouse:acme:1")
    assert (folder / "cv.md").read_text() == CV
    assert (folder / "cover_letter.md").read_text() == LETTER
    assert yaml.safe_load((folder / "answers.yaml").read_text()) == {"why_us": "I like payments."}
    assert docs.cv_pdf == folder / "cv.pdf"
    assert docs.cover_letter_pdf == folder / "cover_letter.pdf"
    assert docs.cv_pdf.read_bytes().startswith(b"%PDF")
    assert docs.cover_letter_pdf.read_bytes().startswith(b"%PDF")
    assert docs.answers == {"why_us": "I like payments."}


async def test_pdf_shows_the_rendered_markdown() -> None:
    docs = await save_documents("greenhouse:acme:1", CV, LETTER)
    text = pdf_text(docs.cv_pdf)
    assert "Jane Doe" in text
    assert "Built a payment service in Python" in text
    assert "**" not in text and "#" not in text


async def test_saving_again_replaces_the_documents() -> None:
    await save_documents("greenhouse:acme:1", CV, LETTER)
    await save_documents("greenhouse:acme:1", "# John Roe\n", "Hello\n")
    assert "John Roe" in pdf_text(application_dir("greenhouse:acme:1") / "cv.pdf")


def test_load_documents_is_none_before_saving() -> None:
    assert load_documents("greenhouse:acme:nope") is None


async def test_load_documents_after_saving() -> None:
    saved = await save_documents("greenhouse:acme:1", CV, LETTER)
    assert load_documents("greenhouse:acme:1") == saved


async def test_answers_default_to_empty() -> None:
    docs = await save_documents("greenhouse:acme:1", CV, LETTER)
    assert docs.answers == {}
    assert load_documents("greenhouse:acme:1") == docs
