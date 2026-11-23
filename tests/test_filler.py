from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from playwright.async_api import Page, async_playwright

from jobhunt.apply.filler import answer_fields, fill_and_submit
from jobhunt.models import Documents, FormField, Profile, Unanswerable

ANSWERS = {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+40 700 000 000",
    "linkedin": "https://linkedin.com/in/janedoe",
}

FORM = """
<form onsubmit="event.preventDefault()">
  <input id="first_name" type="text">
  <input id="last_name" type="text">
  <input name="email" type="email">
  <input name="resume" type="file">
  <textarea name="cards[1][field0]"></textarea>
  <label><input type="radio" name="cards[2][field0]" value="Yes">Yes</label>
  <label><input type="radio" name="cards[2][field0]" value="No">No</label>
  <select name="cards[3][field0]">
    <option value="">Select...</option>
    <option value="friend">A friend</option>
    <option value="board">Job board</option>
  </select>
  <label><input type="checkbox" name="cards[4][field0]" value="English">English</label>
  <label><input type="checkbox" name="cards[4][field0]" value="German">German</label>
  <button type="submit" onclick="SUBMIT_ACTION">Submit application</button>
</form>
"""

FIELDS = [
    FormField(name="first_name", label="First Name", type="text", required=True),
    FormField(name="last_name", label="Last Name", type="text", required=True),
    FormField(name="email", label="Email", type="email", required=True),
    FormField(name="resume", label="Resume/CV", type="file", required=True),
    FormField(name="cards[1][field0]", label="Why us?", type="textarea", required=True),
    FormField(
        name="cards[2][field0]",
        label="Do you need a visa?",
        type="radio",
        required=True,
        options=["Yes", "No"],
    ),
    FormField(
        name="cards[3][field0]",
        label="How did you hear about us?",
        type="select",
        required=True,
        options=["A friend", "Job board"],
    ),
    FormField(
        name="cards[4][field0]",
        label="Languages",
        type="checkbox",
        required=False,
        options=["English", "German"],
    ),
]

CUSTOM = {
    "cards[1][field0]": "I like the product.",
    "cards[2][field0]": "No",
    "cards[3][field0]": "Job board",
    "cards[4][field0]": "English",
}


def form(action: str = "window.clicked = true") -> str:
    return FORM.replace("SUBMIT_ACTION", action)


@pytest.fixture
def documents(tmp_path: Path) -> Documents:
    folder = tmp_path / "job"
    folder.mkdir()
    (folder / "cv.pdf").write_bytes(b"%PDF-1.4 cv")
    (folder / "cover_letter.pdf").write_bytes(b"%PDF-1.4 letter")
    (folder / "cover_letter.md").write_text("Dear team, I would like to apply.")
    return Documents(
        cv_pdf=folder / "cv.pdf",
        cover_letter_pdf=folder / "cover_letter.pdf",
        answers=dict(CUSTOM),
    )


@pytest.fixture
def profile() -> Profile:
    return Profile(facts=[], answers=ANSWERS)


@pytest.fixture
async def page() -> AsyncIterator[Page]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            yield await browser.new_page()
        finally:
            await browser.close()


def answers_for(profile: Profile, documents: Documents) -> dict[str, str]:
    answers = answer_fields(FIELDS, profile, documents)
    assert isinstance(answers, dict)
    return answers


def test_standard_and_custom_answers(profile: Profile, documents: Documents) -> None:
    answers = answers_for(profile, documents)

    assert answers["first_name"] == "Jane"
    assert answers["last_name"] == "Doe"
    assert answers["email"] == "jane@example.com"
    assert answers["resume"] == str(documents.cv_pdf)
    assert answers["cards[1][field0]"] == "I like the product."
    assert answers["cards[2][field0]"] == "No"


def test_cover_letter_text_box_gets_the_letter_text(profile: Profile, documents: Documents) -> None:
    fields = [
        FormField(name="cl_file", label="Cover Letter", type="file", required=False),
        FormField(name="cl_text", label="Cover letter", type="textarea", required=True),
    ]

    answers = answer_fields(fields, profile, documents)

    assert answers == {
        "cl_file": str(documents.cover_letter_pdf),
        "cl_text": "Dear team, I would like to apply.",
    }


def test_missing_required_answer_is_unanswerable(profile: Profile, documents: Documents) -> None:
    fields = [
        *FIELDS,
        FormField(name="q9", label="Salary expectation in EUR?", type="text", required=True),
        FormField(name="q10", label="Pronouns", type="text", required=False),
        FormField(name="phone", label="Phone", type="tel", required=False),
    ]

    result = answer_fields(fields, profile, documents)

    assert result == Unanswerable(missing=["Salary expectation in EUR?"])


def test_answer_outside_the_options_is_unanswerable(profile: Profile, documents: Documents) -> None:
    documents.answers["cards[2][field0]"] = "Maybe"

    result = answer_fields(FIELDS, profile, documents)

    assert result == Unanswerable(missing=["Do you need a visa?"])


def test_optional_field_without_answer_is_left_out(profile: Profile, documents: Documents) -> None:
    del documents.answers["cards[4][field0]"]

    assert "cards[4][field0]" not in answers_for(profile, documents)


async def test_dry_run_never_clicks_submit(
    page: Page, profile: Profile, documents: Documents, tmp_path: Path
) -> None:
    await page.set_content(form())

    result = await fill_and_submit(
        page,
        FIELDS,
        answers_for(profile, documents),
        submit=False,
        evidence_dir=tmp_path / "evidence",
    )

    assert await page.evaluate("window.clicked") is None
    assert not result.confirmed
    assert result.blocked is None
    assert result.screenshot.exists()
    assert await page.input_value("#first_name") == "Jane"
    assert await page.input_value("[name='cards[1][field0]']") == "I like the product."
    assert await page.is_checked("[name='cards[2][field0]'][value='No']")
    assert await page.input_value("[name='cards[3][field0]']") == "board"
    assert await page.is_checked("[name='cards[4][field0]'][value='English']")
    assert not await page.is_checked("[name='cards[4][field0]'][value='German']")


async def test_files_uploaded(
    page: Page, profile: Profile, documents: Documents, tmp_path: Path
) -> None:
    await page.set_content(form())

    await fill_and_submit(
        page,
        FIELDS,
        answers_for(profile, documents),
        submit=False,
        evidence_dir=tmp_path / "evidence",
    )

    names = await page.eval_on_selector("[name=resume]", "e => [...e.files].map(f => f.name)")
    assert names == ["cv.pdf"]


@pytest.mark.parametrize(
    "captcha",
    [
        '<textarea name="g-recaptcha-response" style="display:none"></textarea>',
        '<div class="h-captcha"></div>',
        "<p>Please verify you are human</p>",
    ],
)
async def test_captcha_blocks(
    page: Page, profile: Profile, documents: Documents, tmp_path: Path, captcha: str
) -> None:
    await page.set_content(form() + captcha)

    result = await fill_and_submit(
        page,
        FIELDS,
        answers_for(profile, documents),
        submit=True,
        evidence_dir=tmp_path / "evidence",
    )

    assert result.blocked == "captcha"
    assert not result.confirmed
    assert await page.evaluate("window.clicked") is None
    assert result.screenshot.exists()


async def test_no_confirmation_is_unconfirmed(
    page: Page, profile: Profile, documents: Documents, tmp_path: Path
) -> None:
    await page.set_content(form())

    result = await fill_and_submit(
        page,
        FIELDS,
        answers_for(profile, documents),
        submit=True,
        evidence_dir=tmp_path / "evidence",
        confirm_timeout=0.5,
    )

    assert await page.evaluate("window.clicked") is True
    assert not result.confirmed
    assert result.confirmation_text is None
    assert result.blocked is None


async def test_confirmation_detected(
    page: Page, profile: Profile, documents: Documents, tmp_path: Path
) -> None:
    thanks = "document.body.innerHTML = '<h1>Thank you for applying!</h1>'"
    await page.set_content(form(thanks))

    result = await fill_and_submit(
        page,
        FIELDS,
        answers_for(profile, documents),
        submit=True,
        evidence_dir=tmp_path / "evidence",
    )

    assert result.confirmed
    assert result.confirmation_text is not None
    assert "Thank you" in result.confirmation_text
    assert result.screenshot.exists()


async def test_thank_you_text_already_on_the_form_is_not_a_confirmation(
    page: Page, profile: Profile, documents: Documents, tmp_path: Path
) -> None:
    await page.set_content("<p>Thank you for your interest in Acme!</p>" + form())

    result = await fill_and_submit(
        page,
        FIELDS,
        answers_for(profile, documents),
        submit=True,
        evidence_dir=tmp_path / "evidence",
        confirm_timeout=0.5,
    )

    assert await page.evaluate("window.clicked") is True
    assert not result.confirmed
