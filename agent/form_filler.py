"""
Playwright-based form filler for Rippling ATS.
Opens the form, fills every field using profile + Kimi judgment,
then pauses for human review before submitting.
"""

import time
import re
from pathlib import Path
from openai import OpenAI
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout
from playwright_stealth import stealth_sync

from agent.field_answerer import answer_field
from agent.llm_client import get_client


# Fields filled directly from profile — no Kimi call needed
DIRECT_MAP = {
    "first name":       lambda p: p["personal"]["first_name"],
    "last name":        lambda p: p["personal"]["last_name"],
    "email":            lambda p: p["personal"]["email"],
    "phone":            lambda p: p["personal"]["phone"],
    "phone number":     lambda p: p["personal"]["phone"],
    "location":         lambda p: p["personal"]["location"],
    "city":             lambda p: p["personal"]["location"],
    "linkedin":         lambda p: p["personal"]["linkedin_url"],
    "linkedin profile": lambda p: p["personal"]["linkedin_url"],
    "linkedin url":     lambda p: p["personal"]["linkedin_url"],
    "linkedin link":    lambda p: p["personal"]["linkedin_url"],
    "current company":  lambda p: p["current"]["company"],
    "company":          lambda p: p["current"]["company"],
    "pronouns":         lambda p: p["personal"].get("pronouns", ""),
    "salary":           lambda p: p["compensation"].get("desired_salary_cad", ""),
    "desired salary":   lambda p: p["compensation"].get("desired_salary_cad", ""),
    "compensation":     lambda p: p["compensation"].get("desired_salary_cad", ""),
}

# Generic/meaningless labels that Rippling renders for custom components —
# skip these entirely rather than letting Kimi guess wrong values
SKIP_LABELS = {"search", "select...", "select", "textbox", "combobox", "input"}


# Module-level store for the active browser session (used by the Gradio frontend)
_active_session: dict = {}


def fill_and_submit_headless(
    apply_url: str,
    resume_pdf_path: str,
    cover_letter_path: str | None,
    profile: dict,
    jd_text: str,
    overrides: dict | None = None,
    screenshot_path: str = "output/screenshot.png",
    log_fn=print,
) -> dict:
    """
    Frontend version: fills the form, keeps the browser open, and returns
    a result dict for the UI to display. Does NOT submit or close the browser.
    The UI calls submit_stored_form() when the user clicks Submit.

    Returns:
        {
          "filled_ok": bool,
          "filled_fields": [(label, value), ...],
          "feedback": str,
          "screenshot_path": str,
        }
    """
    kimi_client = get_client()
    filled_fields: list[tuple[str, str]] = []
    
    # Merge overrides into profile for this run
    if overrides:
        if overrides.get("notice_period"): profile["current"]["notice_period"] = overrides["notice_period"]
        if overrides.get("desired_salary"): profile["compensation"]["desired_salary_cad"] = overrides["desired_salary"]
        if overrides.get("authorized_canada") is not None:
            profile["work_authorization"]["canada"] = str(overrides["authorized_canada"]).lower().startswith("y")
        if overrides.get("requires_sponsorship") is not None:
            profile["work_authorization"]["requires_sponsorship"] = str(overrides["requires_sponsorship"]).lower().startswith("y")
        if overrides.get("gender"): profile["eeo"]["gender"] = overrides["gender"]
        if overrides.get("race_ethnicity"): profile["eeo"]["race_ethnicity"] = overrides["race_ethnicity"]
        if overrides.get("veteran_status"): profile["eeo"]["veteran_status"] = overrides["veteran_status"]
        if overrides.get("disability_status"): profile["eeo"]["disability_status"] = overrides["disability_status"]

    playwright_ctx = sync_playwright().start()
    browser = playwright_ctx.chromium.launch(headless=False)
    context = browser.new_context(no_viewport=True)
    page = context.new_page()
    stealth_sync(page)

    log_fn(f"Opening form: {apply_url}")
    page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("input, [role='combobox'], button[type='submit']", timeout=20000)
    except Exception:
        pass
    time.sleep(2)

    _upload_resume(page, resume_pdf_path, filled_fields)
    log_fn("  Resume uploaded.")

    if cover_letter_path:
        _upload_cover_letter(page, cover_letter_path, filled_fields)
        log_fn("  Cover Letter uploaded.")

    _fill_text_fields(page, profile, jd_text, kimi_client, filled_fields)
    _fill_generic_radios(page, profile, jd_text, kimi_client, filled_fields)
    _fill_generic_selects(page, profile, jd_text, kimi_client, filled_fields)
    _fill_pronouns(page, profile, filled_fields)

    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)

    _fill_eeo_dropdowns(page, profile, jd_text, kimi_client, filled_fields)
    _fill_sms_consent(page, profile, filled_fields)

    # Scroll to the absolute bottom so the screenshot captures the submit button
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.keyboard.press("End")
    time.sleep(1)

    Path(screenshot_path).parent.mkdir(exist_ok=True)
    page.screenshot(path=screenshot_path, full_page=True)
    log_fn(f"  Screenshot saved: {screenshot_path}")

    # Generate resume feedback
    feedback = _get_resume_feedback(profile, jd_text, kimi_client)

    # Store browser session so submit_stored_form can reach it
    _active_session.clear()
    _active_session.update({
        "playwright": playwright_ctx,
        "browser": browser,
        "page": page,
        "screenshot_path": screenshot_path,
        "filled_fields": filled_fields,
    })

    return {
        "filled_ok": True,
        "filled_fields": filled_fields,
        "feedback": feedback,
        "screenshot_path": screenshot_path,
    }


def submit_stored_form() -> bool:
    """Called by the Gradio frontend when the user clicks Submit."""
    if not _active_session.get("page"):
        return False
    page = _active_session["page"]
    browser = _active_session["browser"]
    playwright_ctx = _active_session["playwright"]

    submitted = _submit_form(page)
    if submitted:
        ss = _active_session.get("screenshot_path", "output/confirmation.png").replace(
            "form_screenshot", "confirmation"
        )
        page.screenshot(path=ss, full_page=True)

    browser.close()
    playwright_ctx.stop()
    _active_session.clear()
    return submitted


def fill_and_submit(
    apply_url: str,
    resume_pdf_path: str,
    profile: dict,
    jd_text: str,
    dry_run: bool = False,
    overrides: dict | None = None,
    screenshot_path: str = "output/screenshot.png",
) -> bool:
    """CLI version: fills, maybe submits, then closes browser."""
    kimi_client = get_client()

    # Merge overrides into profile
    if overrides:
        if overrides.get("notice_period"): profile["current"]["notice_period"] = overrides["notice_period"]
        if overrides.get("desired_salary"): profile["compensation"]["desired_salary_cad"] = overrides["desired_salary"]
        if overrides.get("authorized_canada") is not None:
            profile["work_authorization"]["canada"] = str(overrides["authorized_canada"]).lower().startswith("y")
        if overrides.get("requires_sponsorship") is not None:
            profile["work_authorization"]["requires_sponsorship"] = str(overrides["requires_sponsorship"]).lower().startswith("y")
        if overrides.get("gender"): profile["eeo"]["gender"] = overrides["gender"]
        if overrides.get("race_ethnicity"): profile["eeo"]["race_ethnicity"] = overrides["race_ethnicity"]
        if overrides.get("veteran_status"): profile["eeo"]["veteran_status"] = overrides["veteran_status"]
        if overrides.get("disability_status"): profile["eeo"]["disability_status"] = overrides["disability_status"]
    filled_fields: list[tuple[str, str]] = []  # tracks (label, value) for review

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        stealth_sync(page)

        print(f"Opening form: {apply_url}")
        page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
        # Rippling is a React SPA — wait for the form to actually render
        try:
            page.wait_for_selector("input, [role='combobox'], button[type='submit']",
                                   timeout=20000)
        except Exception:
            pass  # proceed anyway, individual field fills will handle timeouts
        time.sleep(2)

        # ── Resume upload ──────────────────────────────────────────────────────
        _upload_resume(page, resume_pdf_path, filled_fields)

        # ── Text, Radio, and standard Select fields ────────────────────────────
        _fill_text_fields(page, profile, jd_text, kimi_client, filled_fields)
        _fill_generic_radios(page, profile, jd_text, kimi_client, filled_fields)
        _fill_generic_selects(page, profile, jd_text, kimi_client, filled_fields)

        # ── Pronouns dropdown ──────────────────────────────────────────────────
        _fill_pronouns(page, profile, filled_fields)

        # ── Scroll to reveal EEO section ──────────────────────────────────────
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)

        # ── EEO dropdowns ─────────────────────────────────────────────────────
        _fill_eeo_dropdowns(page, profile, jd_text, kimi_client, filled_fields)

        # ── SMS consent ───────────────────────────────────────────────────────
        _fill_sms_consent(page, profile, filled_fields)

        # ── Screenshot ────────────────────────────────────────────────────────
        Path(screenshot_path).parent.mkdir(exist_ok=True)
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"\nScreenshot saved: {screenshot_path}")

        # ── Human review ──────────────────────────────────────────────────────
        _print_review_table(filled_fields)

        # ── Resume feedback ───────────────────────────────────────────────────
        _print_resume_feedback(profile, jd_text, kimi_client)

        if dry_run:
            print("\nDRY RUN — form not submitted.")
            print("Check output/form_screenshot.png then run without --dry-run to submit.")
            try:
                input("Press Enter to close the browser...")
            except EOFError:
                pass
            browser.close()
            return True

        # ── Interactive decision loop ──────────────────────────────────────────
        submitted = _decision_loop(page, filled_fields, resume_pdf_path, screenshot_path)
        browser.close()
        return submitted


# ── Private helpers ────────────────────────────────────────────────────────────

def _upload_resume(page: Page, resume_pdf_path: str, filled: list) -> None:
    print("Uploading resume...")
    try:
        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(resume_pdf_path)
        time.sleep(1)
        filled.append(("Resume", resume_pdf_path))
        print("  Resume uploaded.")
    except PWTimeout:
        print("  WARNING: Could not find file input — skipping resume upload.")


def _upload_cover_letter(page: Page, cl_path: str, filled: list) -> None:
    print("Uploading cover letter...")
    try:
        # Some forms have a single input that accepts multiple, some have multiple inputs.
        # Find all file inputs
        file_inputs = page.locator("input[type='file']")
        count = file_inputs.count()
        if count > 1:
            # Assume second file input is cover letter
            file_input = file_inputs.nth(1)
        else:
            # If there's only one, it's usually Resume, but we might try to find a label
            cl_label = page.locator("label", has_text=re.compile(r"cover letter", re.IGNORECASE))
            if cl_label.count() > 0:
                file_input = cl_label.locator("..").locator("input[type='file']").first
            else:
                print("  WARNING: No secondary file input found for Cover Letter.")
                return
                
        file_input.set_input_files(cl_path)
        time.sleep(1)
        filled.append(("Cover Letter", cl_path))
        print("  Cover letter uploaded.")
    except Exception as e:
        print(f"  WARNING: Could not upload cover letter - {e}")


def _fill_text_fields(
    page: Page,
    profile: dict,
    jd_text: str,
    client: OpenAI,
    filled: list,
) -> None:
    print("Filling text fields...")
    inputs = page.locator(
        "input[type='text'], input[type='email'], input[type='tel'], input:not([type])"
    )
    count = inputs.count()
    label = ""

    for i in range(count):
        field = inputs.nth(i)
        try:
            label = _get_label(page, field)
            if not label:
                continue

            label_lower = label.lower().strip()

            # Skip generic labels — these are custom React components we can't
            # reliably fill as plain text inputs
            if label_lower in SKIP_LABELS:
                continue

            # Direct map first — no Kimi call
            value = None
            for key, getter in DIRECT_MAP.items():
                if key in label_lower:
                    value = getter(profile)
                    break

            # Fall back to Kimi for unknown fields
            if value is None:
                value = answer_field(label, "text", [], profile, jd_text, client)

            if value:
                field.fill(str(value))
                filled.append((label, str(value)))
                print(f"  {label}: {value}")
                time.sleep(0.3)

        except Exception as e:
            print(f"  WARNING: Could not fill '{label}': {e}")


def _fill_generic_radios(page: Page, profile: dict, jd_text: str, client: OpenAI, filled: list) -> None:
    print("Checking for unexpected radio button questions...")
    try:
        # Ask the browser to group all radio buttons by their name attribute
        groups = page.evaluate("""() => {
            const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
            const groups = {};
            for (const r of radios) {
                const name = r.name;
                if (!name) continue;
                if (!groups[name]) groups[name] = { name: name, options: [], question: '' };
                
                let labelText = '';
                if (r.id) {
                    const lbl = document.querySelector(`label[for="${r.id}"]`);
                    if (lbl) labelText = lbl.innerText;
                }
                if (!labelText) {
                    const parent = r.closest('label');
                    if (parent) labelText = parent.innerText;
                }
                if (!labelText) labelText = r.value;
                
                groups[name].options.push({ value: r.value, text: labelText.trim() });
                
                if (!groups[name].question) {
                    const fieldset = r.closest('fieldset');
                    if (fieldset) {
                        const legend = fieldset.querySelector('legend');
                        if (legend) groups[name].question = legend.innerText.trim();
                    }
                }
            }
            return Object.values(groups);
        }""")
        
        # To avoid duplicating the hardcoded SMS consent, keep track of handled ones
        handled_labels = {f[0].lower() for f in filled}
        
        for g in groups:
            options_text = [o['text'] for o in g['options'] if o['text']]
            if not options_text:
                continue
                
            question = g['question'] or f"Radio selection ({g['name']})"
            # Skip if it looks like the SMS consent that we already handle explicitly, 
            # though it's fine if Kimi handles it too! We'll just let Kimi handle it if not already filled.
            
            chosen_text = answer_field(question, "dropdown", options_text, profile, jd_text, client)
            if chosen_text:
                for o in g['options']:
                    if o['text'] == chosen_text:
                        # Click the radio button input using JS to bypass hidden/display:none constraints
                        page.locator(f"input[type='radio'][name='{g['name']}'][value='{o['value']}']").first.evaluate("el => el.click()")
                        filled.append((question, chosen_text))
                        print(f"  {question}: {chosen_text}")
                        time.sleep(0.3)
                        break
    except Exception as e:
        print(f"  WARNING: Generic radio filler encountered an error: {e}")


def _fill_generic_selects(page: Page, profile: dict, jd_text: str, client: OpenAI, filled: list) -> None:
    print("Checking for standard dropdown questions...")
    try:
        selects = page.locator("select")
        for i in range(selects.count()):
            select = selects.nth(i)
            if not select.is_visible():
                continue
            
            label = _get_label(page, select) or "Select option"
            options_els = select.locator("option")
            options = []
            for j in range(options_els.count()):
                text = options_els.nth(j).inner_text().strip()
                if text and text.lower() not in ['select', 'choose', '---']:
                    options.append(text)
                    
            if not options:
                continue
                
            chosen = answer_field(label, "dropdown", options, profile, jd_text, client)
            if chosen:
                # Playwright's select_option is resilient and can select by text or value
                try:
                    select.select_option(label=chosen)
                    filled.append((label, chosen))
                    print(f"  {label}: {chosen}")
                    time.sleep(0.3)
                except Exception:
                    pass
    except Exception as e:
        print(f"  WARNING: Generic select filler encountered an error: {e}")


def _fill_pronouns(page: Page, profile: dict, filled: list) -> None:
    pronouns = profile["personal"].get("pronouns", "")
    if not pronouns:
        return
    try:
        container = page.locator(
            "[class*='pronouns'], [data-testid*='pronouns']"
        ).first
        if container.count() == 0:
            return
        container.click()
        time.sleep(0.5)
        page.locator(f"text='{pronouns}'").first.click()
        filled.append(("Pronouns", pronouns))
        print(f"  Pronouns: {pronouns}")
    except Exception:
        pass


def _fill_eeo_dropdowns(
    page: Page,
    profile: dict,
    jd_text: str,
    client: OpenAI,
    filled: list,
) -> None:
    print("Filling custom React dropdowns (EEO and others)...")
    custom_labels = [
        "Gender",
        "Please identify your race",
        "Are you Hispanic/Latino?",
        "Veteran Status",
        "Disability Status",
        "How did you hear about this job?",
        "Where did you hear about this job?",
        "How did you hear about us?"
    ]
    for label in custom_labels:
        try:
            _fill_react_dropdown(page, label, profile, jd_text, client, filled)
        except Exception as e:
            print(f"  WARNING: Could not fill '{label}': {e}")


def _fill_react_dropdown(
    page: Page,
    label: str,
    profile: dict,
    jd_text: str,
    client: OpenAI,
    filled: list,
) -> None:
    container = page.locator(f"text='{label}'").locator("..").locator("..")
    select_el = container.locator(
        "[class*='select'], [role='combobox'], [role='listbox']"
    ).first

    if select_el.count() == 0:
        print(f"  Skipping '{label}': dropdown not found")
        return

    select_el.click()
    time.sleep(0.5)

    option_els = page.locator("[role='option']")
    options = [option_els.nth(i).inner_text() for i in range(option_els.count())]

    if not options:
        page.keyboard.press("Escape")
        return

    chosen = answer_field(label, "dropdown", options, profile, jd_text, client)
    print(f"  {label}: {chosen}")

    for opt in options:
        if opt.strip().lower() == chosen.strip().lower():
            _click_option(page, opt)
            filled.append((label, chosen))
            time.sleep(0.3)
            return

    # Fallback: pick first opt-out option
    for opt in options:
        if any(kw in opt.lower() for kw in ["prefer not", "decline", "i don't", "i don’t"]):
            _click_option(page, opt)
            filled.append((label, opt))
            time.sleep(0.3)
            return

    page.keyboard.press("Escape")
    print(f"  WARNING: No matching option for '{label}' — skipped.")


def _click_option(page: Page, opt_text: str) -> None:
    """
    Click a [role='option'] element whose text matches opt_text.
    Uses filter(has_text=) instead of a CSS :has-text() string so
    apostrophes and other special characters don't break the selector.
    """
    page.locator("[role='option']").filter(has_text=opt_text).first.click()


def _fill_sms_consent(page: Page, profile: dict, filled: list) -> None:
    import re
    consent = profile.get("sms_consent", False)
    try:
        if consent:
            radio = page.locator("label").filter(has_text=re.compile(r"consent to receiving text messages", re.IGNORECASE)).first
        else:
            radio = page.locator("label").filter(has_text=re.compile(r"do not consent to receiving text messages", re.IGNORECASE)).first

        if radio.count() > 0:
            radio.click(force=True)
            filled.append(("SMS consent", "Yes" if consent else "No"))
            print(f"  SMS consent: {'Yes' if consent else 'No'}")
    except Exception as e:
        print(f"  WARNING: Could not set SMS consent: {e}")


def _print_review_table(filled: list[tuple[str, str]]) -> None:
    print("\n" + "=" * 60)
    print("REVIEW — here is everything the agent filled in:")
    print("=" * 60)
    for label, value in filled:
        # Truncate long values for display
        display = value if len(value) <= 60 else value[:57] + "..."
        print(f"  {label:<28} {display}")
    print("=" * 60)


def _decision_loop(
    page: Page,
    filled_fields: list[tuple[str, str]],
    resume_pdf_path: str,
    screenshot_path: str,
) -> bool:
    """
    Interactive menu shown after review table and feedback.
    Loops until user submits or quits.
    """
    import subprocess

    while True:
        print("\nWhat would you like to do?")
        print("  [s] Submit the application")
        print("  [e] Edit a field answer manually")
        print("  [r] Replace the resume file")
        print("  [v] View the form screenshot")
        print("  [q] Quit without submitting")
        choice = input("Choice: ").strip().lower()

        if choice == "s":
            submitted = _submit_form(page)
            if submitted:
                page.screenshot(path="output/confirmation.png", full_page=True)
                print("Confirmation screenshot saved to output/confirmation.png")
            return submitted

        elif choice == "e":
            _edit_field(page, filled_fields)
            # Refresh screenshot after edit
            page.screenshot(path=screenshot_path, full_page=True)
            print(f"Screenshot updated: {screenshot_path}")
            _print_review_table(filled_fields)

        elif choice == "r":
            print(f"  Current resume: {resume_pdf_path}")
            new_path = input("Path to new resume file (.docx or .pdf): ").strip()
            if not new_path or not __import__("os").path.exists(new_path):
                print("  File not found — keeping current resume.")
                continue
            # Convert .docx → .pdf if needed
            if new_path.lower().endswith(".docx"):
                from docx2pdf import convert
                pdf_out = "output/resume_upload_new.pdf"
                print("  Converting .docx → PDF...")
                convert(new_path, pdf_out)
                new_path = pdf_out
            try:
                file_input = page.locator("input[type='file']").first
                file_input.set_input_files(new_path)
                time.sleep(1)
                # Update the filled fields record
                for i, (label, _) in enumerate(filled_fields):
                    if label == "Resume":
                        filled_fields[i] = ("Resume", new_path)
                        break
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"  Resume replaced. Screenshot updated: {screenshot_path}")
            except Exception as e:
                print(f"  WARNING: Could not replace resume: {e}")

        elif choice == "v":
            try:
                subprocess.Popen(["open", screenshot_path])
            except Exception:
                print(f"  Screenshot is at: {screenshot_path}")

        elif choice == "q":
            print("Quit — application was NOT submitted.")
            return False

        else:
            print("  Invalid choice — enter s, e, r, v, or q.")


def _edit_field(
    page: Page,
    filled_fields: list[tuple[str, str]],
) -> None:
    """Let the user pick a filled field and manually correct its value."""
    print("\nFilled fields:")
    for i, (label, value) in enumerate(filled_fields):
        display = value if len(value) <= 50 else value[:47] + "..."
        print(f"  [{i}] {label:<28} {display}")

    raw = input("\nEnter field number to edit (or Enter to cancel): ").strip()
    if not raw.isdigit():
        return

    idx = int(raw)
    if idx < 0 or idx >= len(filled_fields):
        print("  Invalid number.")
        return

    label, current = filled_fields[idx]
    print(f"\n  Field: {label}")
    print(f"  Current value: {current}")
    new_value = input("  New value (or Enter to keep): ").strip()
    if not new_value:
        return

    # Try to update the field in the browser
    try:
        # Text input
        inputs = page.locator(
            "input[type='text'], input[type='email'], input[type='tel'], input:not([type])"
        )
        for i in range(inputs.count()):
            field = inputs.nth(i)
            lbl = _get_label(page, field)
            if lbl and lbl.lower().strip() == label.lower().strip():
                field.fill(new_value)
                filled_fields[idx] = (label, new_value)
                print(f"  Updated: {label} → {new_value}")
                return
        print("  Could not locate the field in the browser — update noted but not applied.")
        filled_fields[idx] = (label, f"{new_value} (manual — verify in screenshot)")
    except Exception as e:
        print(f"  WARNING: {e}")


def _get_resume_feedback(profile: dict, jd_text: str, client: OpenAI) -> str:
    """
    Asks Kimi to compare each resume bullet against the JD.
    Returns the feedback as a string (used by both CLI and frontend).
    """
    work_history = profile.get("work_history", [])
    resume_lines = []
    for job in work_history:
        header = (
            f"{job.get('title', '')} @ {job.get('company', '')} "
            f"({job.get('start_date', '')} – {job.get('end_date', '')})"
        )
        resume_lines.append(header)
        for bullet in job.get("description", "").split(". "):
            bullet = bullet.strip()
            if bullet:
                resume_lines.append(f"  - {bullet}")

    resume_text = "\n".join(resume_lines)

    system = """You are a resume coach giving specific, actionable feedback.
For each bullet point in the resume, compare it against the job description.
If a bullet could be reframed to better match the role, show:
  BEFORE: [original bullet]
  AFTER:  [rewritten version using JD language where it genuinely applies]
  WHY:    [one sentence — what specifically in the JD this targets]

Rules:
- Only suggest rewrites where there is a genuine, honest connection to the JD.
- Never fabricate facts, metrics, or experience.
- Skip bullets that already align well — only flag the ones worth improving.
- Keep AFTER bullets to one sentence.
- Group by job role."""

    user = f"""Job Description:
{jd_text[:3000]}

Candidate Resume:
{resume_text}

Give before/after suggestions for bullets worth improving."""

    try:
        from agent.llm_client import MODEL_FAST
        return client.chat.completions.create(
            model=MODEL_FAST,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        ).choices[0].message.content.strip()
    except Exception as e:
        return f"(Could not generate feedback: {e})"


def _print_resume_feedback(profile: dict, jd_text: str, client: OpenAI) -> None:
    """CLI version — prints the feedback with section headers."""
    print("\n" + "=" * 60)
    print("RESUME OPTIMIZATION SUGGESTIONS (for your next version)")
    print("=" * 60)
    print(_get_resume_feedback(profile, jd_text, client))
    print("=" * 60)


def _submit_form(page: Page) -> bool:
    print("Submitting...")
    try:
        # First, try standard hardcoded selectors for speed
        submit_btns = page.locator(
            "button[type='submit'], input[type='submit'], button:has-text('Apply'), button:has-text('Submit'), button:has-text('Next'), button:has-text('Continue')"
        )
        
        for i in range(submit_btns.count()):
            btn = submit_btns.nth(i)
            if btn.is_visible():
                print(f"  Attempting to click: {btn.inner_text()}")
                btn.scroll_into_view_if_needed()
                btn.click()
                
                # Wait for either navigation or a "success" message
                print("  Waiting for confirmation...")
                time.sleep(5) 
                
                # Check for common success indicators
                success_indicators = [
                    "thank you", "success", "received", "submitted", 
                    "application sent", "applied", "confirmed"
                ]
                page_text = page.evaluate("() => document.body.innerText.toLowerCase()")
                
                if any(ind in page_text for ind in success_indicators):
                    print("  Success indicator found in page text.")
                    return True
                
                # Check for common error indicators
                error_indicators = ["error", "required", "invalid", "missing"]
                if any(ind in page_text for ind in error_indicators):
                    print("  WARNING: Possible validation errors detected after click.")
                
                return True # Assume it worked if no obvious error

        # If standard selectors fail, use Kimi to reason about unusual interfaces
        print("  Standard submit button not found. Using LLM to reason about available actions...")
        buttons = page.locator("button, a.button, a.btn, [role='button'], input[type='submit'], input[type='button']")
        visible_options = []
        
        for i in range(buttons.count()):
            try:
                el = buttons.nth(i)
                if el.is_visible():
                    text = el.inner_text().strip()
                    if not text:
                        text = el.get_attribute("value") or el.get_attribute("aria-label") or ""
                    if text:
                        visible_options.append({"index": i, "text": text.strip()})
            except Exception:
                pass

        if not visible_options:
            print("  ERROR: No visible actionable buttons found on the page.")
            return False

        # Ask Kimi to pick the best button
        from agent.llm_client import get_client, MODEL_FAST
        import json
        client = get_client()
        
        system = (
            "You are an AI agent filling out a job application. The standard 'Apply' button was not found. "
            "You must select the button that advances the form or submits the application. "
            "Return a JSON object with a single key 'best_index' containing the integer index of the correct button. "
            "If none seem correct, return -1."
        )
        user = "Available buttons:\n" + "\n".join([f"Index {opt['index']}: '{opt['text']}'" for opt in visible_options])
        
        response = client.chat.completions.create(
            model=MODEL_FAST,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            response_format={"type": "json_object"},
            temperature=0
        )
        
        result = json.loads(response.choices[0].message.content)
        best_idx = result.get("best_index", -1)
        
        if best_idx != -1:
            chosen = next((opt for opt in visible_options if opt["index"] == best_idx), None)
            if chosen:
                print(f"  LLM selected button: '{chosen['text']}'")
                buttons.nth(best_idx).click()
                time.sleep(3)
                print("  Submitted via LLM reasoning.")
                return True

        print("  WARNING: LLM could not determine the correct submit button.")
        return False

    except Exception as e:
        print(f"  ERROR in submit: {e}")
        return False


def _get_label(page: Page, field) -> str:
    """Find the label text associated with a form field."""
    try:
        field_id = field.get_attribute("id")
        if field_id:
            label_el = page.locator(f"label[for='{field_id}']")
            if label_el.count() > 0:
                return label_el.first.inner_text().strip()

        aria = field.get_attribute("aria-label")
        if aria:
            return aria.strip()

        placeholder = field.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()

        parent_text = field.locator("..").inner_text()
        if parent_text and len(parent_text) < 80:
            return parent_text.strip()
    except Exception:
        pass
    return ""
