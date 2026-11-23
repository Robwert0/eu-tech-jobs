from typing import Any, get_args

from playwright.async_api import Page, async_playwright

from jobhunt.enums import Ats
from jobhunt.http import PoliteClient
from jobhunt.models import FieldType, FormField, Job


class UnsupportedForm(Exception):
    pass


FIELD_TYPES: frozenset[str] = frozenset(get_args(FieldType))

STANDARD_FIELDS: dict[str, str] = {
    "first name": "first_name",
    "last name": "last_name",
    "name": "full_name",
    "full name": "full_name",
    "email": "email",
    "email address": "email",
    "phone": "phone",
    "phone number": "phone",
    "linkedin": "linkedin",
    "linkedin url": "linkedin",
    "linkedin profile": "linkedin",
    "linkedin profile url": "linkedin",
    "github": "github",
    "github url": "github",
    "github profile": "github",
    "github profile url": "github",
    "resume": "resume",
    "cv": "resume",
    "resume/cv": "resume",
    "cover letter": "cover_letter",
    "location": "location",
    "current location": "location",
}

GREENHOUSE_TYPES: dict[str, FieldType] = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "checkbox",
}

LEVER_JS = """
items => items.map(item => {
  const label = item.querySelector('.application-label');
  const inputs = [...item.querySelectorAll('input:not([type=hidden]), textarea, select')];
  if (!label || inputs.length === 0) return null;
  const first = inputs[0];
  const type = first.tagName === 'TEXTAREA' ? 'textarea'
    : first.tagName === 'SELECT' ? 'select' : first.type;
  const options = type === 'select'
    ? [...first.options].filter(o => o.value).map(o => o.textContent.trim())
    : (type === 'radio' || type === 'checkbox') ? inputs.map(i => i.value) : [];
  return {
    name: first.name,
    label: label.textContent,
    type,
    required: label.textContent.includes('✱') || inputs.some(i => i.required),
    options,
  };
})
"""

ASHBY_JS = """
entries => entries.map(entry => {
  const title = entry.querySelector('.ashby-application-form-question-title');
  const inputs = [...entry.querySelectorAll('input:not([type=hidden]), textarea, select')];
  const buttons = [...entry.querySelectorAll('button')].map(b => b.textContent.trim());
  const first = inputs[0];
  let type = !first ? 'text' : first.tagName === 'TEXTAREA' ? 'textarea'
    : first.tagName === 'SELECT' ? 'select' : first.type;
  let options = [];
  const yesNo = buttons.includes('Yes') && buttons.includes('No');
  if (type === 'checkbox' && inputs.length === 1 && yesNo) {
    type = 'radio';
    options = ['Yes', 'No'];
  } else if (type === 'radio' || type === 'checkbox') {
    options = inputs.map(i => (i.closest('label')?.textContent || i.value).trim());
  } else if (type === 'select') {
    options = [...first.options].filter(o => o.value).map(o => o.textContent.trim());
  }
  return {
    name: entry.dataset.fieldPath,
    label: title ? title.textContent : '',
    type,
    required: (title?.className || '').includes('required') || inputs.some(i => i.required),
    options,
  };
})
"""

READERS: dict[Ats, tuple[str, str]] = {
    Ats.lever: ("li.application-question", LEVER_JS),
    Ats.ashby: (".ashby-application-form-field-entry", ASHBY_JS),
}


def _label(text: str) -> str:
    return " ".join(text.replace("✱", "").split()).rstrip("*").strip()


def _field(raw: dict[str, Any]) -> FormField:
    kind = raw["type"] if raw["type"] in FIELD_TYPES else "text"
    return FormField.model_validate({**raw, "label": _label(raw["label"]), "type": kind})


def standard_key(field: FormField) -> str | None:
    return STANDARD_FIELDS.get(field.label.lower())


def custom_questions(fields: list[FormField]) -> list[FormField]:
    return [f for f in fields if standard_key(f) is None]


def greenhouse_fields(payload: dict[str, Any]) -> list[FormField]:
    fields = []
    for question in payload["questions"]:
        field = next((f for f in question["fields"] if f["type"] in GREENHOUSE_TYPES), None)
        if field is None:
            continue
        fields.append(
            FormField(
                name=field["name"],
                label=_label(question["label"]),
                type=GREENHOUSE_TYPES[field["type"]],
                required=question["required"],
                options=[v["label"] for v in field.get("values", [])],
            )
        )
    return fields


async def read_form(page: Page, ats: Ats) -> list[FormField]:
    if ats not in READERS:
        raise UnsupportedForm(f"Reading {ats} application forms is not supported; apply manually.")
    selector, script = READERS[ats]
    raw = await page.eval_on_selector_all(selector, script)
    return [_field(item) for item in raw if item]


async def _greenhouse_questions(job: Job) -> list[FormField]:
    host = job.board.host or "boards-api.greenhouse.io"
    url = f"https://{host}/v1/boards/{job.board.slug}/jobs/{job.external_id}"
    async with PoliteClient() as client:
        payload = await client.get_json(url, questions="true")
    return greenhouse_fields(payload)


async def job_fields(job: Job) -> list[FormField]:
    ats = job.board.ats
    if ats is Ats.greenhouse:
        return await _greenhouse_questions(job)
    if ats not in READERS:
        raise UnsupportedForm(f"Reading {ats} application forms is not supported; apply manually.")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(job.apply_url)
            await page.wait_for_selector(READERS[ats][0])
            return await read_form(page, ats)
        finally:
            await browser.close()
