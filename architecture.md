# Apply Agent — Architecture

## What This Is

A Python agent that takes a user profile + resume + job URL and:
1. Fetches and understands the job description
2. Opens the job application form with Playwright
3. Fills every field using Kimi's judgment against the user profile
4. Uploads the original resume (as-is, no editing)
5. Shows the user a review table + resume optimization suggestions
6. Waits for explicit user confirmation before submitting

Phase 1 target: Opendoor's Rippling ATS form (Ben Booi's challenge)
Phase 2: Any ATS form (Greenhouse, Lever, Workday, etc.)

---

## Directory Structure

```
apply-agent/
├── architecture.md          # This file
├── todo.md                  # Build progress
├── README.md                # Project overview for external readers
├── requirements.txt         # Python dependencies
├── profile.yaml             # User profile — written by setup, read by apply
├── .env                     # MOONSHOT_API_KEY
├── main.py                  # CLI entry point (setup + apply commands)
├── app.py                   # FastAPI backend — serves index.html + pipeline API
├── index.html               # Frontend — single HTML file, zero dependencies
├── agent/
│   ├── __init__.py
│   ├── llm_client.py        # Shared Moonshot (Kimi) client setup
│   ├── docx_reader.py       # Extracts structured info from user's .docx resume
│   ├── jd_fetcher.py        # Fetches JD + constructs apply URL from any Rippling URL
│   ├── form_filler.py       # Playwright: fills form, review pause, decision loop, submits
│   └── field_answerer.py    # Kimi answers any form field using profile as context
└── output/
    ├── uploads/             # Uploaded resumes saved here during pipeline runs
    ├── resume_upload.pdf    # .docx converted to PDF for upload (if needed)
    ├── form_screenshot.png  # Full-page screenshot of filled form
    └── confirmation.png     # Screenshot after submit
```

---

## Pipeline

```
User provides: resume (.docx or .pdf) + job URL + optional tailoring notes
(via web UI: index.html → app.py, or via CLI: python main.py apply)
        │
        ▼
  FIRST TIME ONLY: [setup flow — python main.py setup --resume <path>]
  docx_reader.py extracts everything it can from resume.docx:
    name, email, phone, location, LinkedIn, work history, education, skills
  Kimi normalises any parsing errors (e.g. swapped title/company)
  Agent only asks what the resume can't answer:
    work auth, salary, EEO, SMS consent, pronouns, tailoring defaults, etc.
  Writes profile.yaml — never asked again
        │
        ▼
  [jd_fetcher.py]
  Accepts either Rippling URL format:
  - Job post URL:   ats.rippling.com/{org}/jobs/{uuid}
    → fetches JD text, constructs apply URL automatically
  - Apply form URL: ats.rippling.com/{org}/jobs/{uuid}/apply?...
    → strips to get job post, fetches JD, returns original apply URL
  Also handles LinkedIn job URLs (Playwright, extracts external apply link)
        │
        ▼
  [prepare resume for upload]
  - .pdf → upload as-is
  - .docx → convert to PDF via docx2pdf (~15s), save to output/resume_upload.pdf
  Original file is never modified — formatting always preserved
        │
        ▼
  [form_filler.py]  ←──────────────────────────────────┐
  Playwright opens the apply URL (headless=False)       │
  For each field on the page:                           │
    - Extract label text, field type, options           │
    - Check DIRECT_MAP first (name, email, phone…)      │
    - Fall back to field_answerer.py for unknowns       │
    - Fill / select / upload                            │
  Take full-page screenshot                             │
        │                                               │
        ▼                                               │
  *** HUMAN REVIEW PAUSE ***                            │
  Print table: every field + value filled               │
  Kimi generates resume suggestions: BEFORE/AFTER/WHY  │
  per bullet point, tied to JD language                 │
        │                                               │
        ▼                                               │
  Decision menu:                                        │
    [s] Submit     [e] Edit a field                     │
    [r] Replace resume file                             │
    [v] View screenshot   [q] Quit                      │
        │                                               │
        ▼                                               │
  Submit form → save confirmation screenshot            │
        │                                               │
        ▼                                               │
  [field_answerer.py] ───────────────────────────────────┘
  Kimi (moonshot-v1-auto, fast) per unknown field:
  - System: full profile.yaml + JD text (profile-as-context injection)
  - User: field label + type + options
  - Returns: exact value to fill/select
```

---

## Frontend Architecture

```
index.html (zero-dependency single HTML file)
        ↕ fetch / SSE
app.py (FastAPI)
  POST /run        → starts pipeline in background thread, returns session_id
  GET  /stream/:id → SSE stream of log lines + final result
  POST /submit     → calls submit_stored_form() on active browser session
  POST /cancel     → closes browser session without submitting
  GET  /screenshot → serves form_screenshot.png
  GET  /profile    → returns name + email from profile.yaml
  GET  /           → serves index.html
```

UI layout (index.html):
- Left panel: bold DM Serif Display headline, description, "How it works" link
- Right panel: resume upload drop zone, job URL input, tailoring notes, Run Agent button
- Results modal (slides up): live log stream, form screenshot, fields table, resume suggestions, Submit/Cancel decision bar

---

## The Profile-as-Context Injection Pattern

Every unknown form field is answered by Kimi with the full profile as context:

```
SYSTEM:
You are filling out a job application on behalf of this candidate.
Use only information from their profile. Never fabricate.

Profile:
[full profile.yaml content]

Job Description:
[JD text]

USER:
Field label: "Are you legally authorized to work in Canada?"
Field type: dropdown
Options: ["Yes", "No"]

ASSISTANT:
Yes
```

Kimi sees the label and field type — never the DOM. This is intentional:
DOM structure varies across ATS systems. Label text is stable.

Handles: Yes/No dropdowns, free-text motivation fields, numeric experience fields,
EEO opt-out selections, multi-select skill lists.

---

## LLM Setup — Moonshot (Kimi)

All LLM calls use the Kimi API via the OpenAI-compatible endpoint:

```python
from openai import OpenAI
client = OpenAI(
    base_url="https://api.moonshot.ai/v1",
    api_key=os.getenv("MOONSHOT_API_KEY"),
)
```

Model: `moonshot-v1-auto` for all calls (Kimi auto-routes by context length).
Key stored in `.env` as `MOONSHOT_API_KEY`.

Kimi is used for three tasks:
1. Work history normalisation — fix title/company swaps from regex parsing
2. Field answering — answer arbitrary ATS form fields from profile context
3. Resume feedback — generate BEFORE/AFTER/WHY suggestions per bullet

---

## Key Technical Decisions

### No resume editing
Earlier versions tried to rewrite the resume content (Kimi rewrites .docx → new PDF).
This was abandoned because:
- Multi-run paragraph formatting in .docx is fragile to reconstruct
- The ATS parses uploaded PDF text anyway — formatting doesn't affect parsing
- The original resume uploaded as-is is always visually correct

Kimi's intelligence is now focused entirely on answering form fields and generating
improvement suggestions — not on modifying the file.

### Why Playwright over Selenium
- Better async support
- Native `set_input_files()` for file uploads
- Better handling of JS-rendered React SPAs (Rippling)

### Why FastAPI + raw HTML over Gradio
- Gradio has no styling control — produces generic UI
- FastAPI + single HTML file = full design control, zero frontend build tools
- SSE streaming from FastAPI maps cleanly to EventSource in the browser

### Rippling URL handling
Rippling has two URL formats in the wild:
- Job post:   `ats.rippling.com/en-CA/{org}/jobs/{uuid}`
- Apply form: `ats.rippling.com/en-CA/{org}/jobs/{uuid}/apply?jobBoardSlug={org}&jobId={uuid}&step=application`

The fetcher accepts either. Given a job post URL, it constructs the apply URL by
extracting org slug and UUID and appending the query params. Given an apply URL,
it strips back to the job post to fetch the JD, then returns the original apply URL.

---

## Phase 2 Scope (after Phase 1 submitted)

- Support Greenhouse, Lever, Ashby ATS
- LinkedIn URL → detect and follow external apply link
- Multi-step form handling (next page detection)
- CAPTCHA detection — pause and ask user to solve manually
- Application deduplication — don't apply twice to the same company
- Hosted version — user uploads resume once, applies from any browser
