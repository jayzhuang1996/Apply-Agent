# Apply Agent

An AI agent that takes your resume and a job URL, fills out the entire application form, shows you everything it filled in with resume improvement suggestions, and waits for your confirmation before submitting — nothing goes out without you.

Built as a response to Opendoor's AI hiring challenge: *"Apply to this role ONLY using AI (including filling out the forms + creating the documents) and tell us how you did it."*

---

## What it does

```
You provide:  resume (.docx or .pdf)  +  job posting URL
                          ↓
        Reads your resume — extracts name, contact info,
        work history, education, skills automatically.
        Kimi fixes any parsing errors (e.g. swapped title/company).
                          ↓
        Only asks ~12 questions your resume can't answer:
        work authorization, salary, EEO, SMS consent, etc.
        Saves everything to profile.yaml — never asked again.
                          ↓
        Fetches the job description from the URL.
        Handles both Rippling URL formats automatically.
                          ↓
        Playwright opens the application form in a real browser.
        Fills every field — text, dropdowns, EEO, file upload —
        using your profile and Kimi's judgment.
                          ↓
        PAUSES — shows you:
          • Every field filled and its value
          • Resume optimization suggestions (BEFORE/AFTER/WHY per bullet)
                          ↓
        Decision menu:
          [s] Submit  [e] Edit a field  [r] Replace resume
          [v] View screenshot  [q] Quit
                          ↓
        Submits only when you say so.
```

---

## Supported job URL formats

| Format | Example | Status |
|--------|---------|--------|
| Rippling job post | `ats.rippling.com/{org}/jobs/{uuid}` | Phase 1 — working |
| Rippling apply form | `ats.rippling.com/{org}/jobs/{uuid}/apply?...` | Phase 1 — working |
| LinkedIn public job | `linkedin.com/jobs/view/{id}` | Phase 2 |
| Greenhouse | `boards.greenhouse.io/...` | Phase 2 |
| Lever | `jobs.lever.co/...` | Phase 2 |

---

## How the agent handles unknown form fields

Most ATS forms have fields you can't predict — "Are you legally authorized to work in Canada?", "Why do you want to work here?", "Describe your experience with AI tools."

Instead of maintaining a lookup table, the agent uses **profile-as-context injection**:

Your entire `profile.yaml` is loaded into Kimi's context. For every field on the form, Kimi reads the label and figures out the right answer from your profile. No pre-mapping required.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Set your API key

```bash
# Add to .env file:
MOONSHOT_API_KEY=your-key-here
```

Get a key at [platform.moonshot.ai](https://platform.moonshot.ai).

### 3. First-time setup — build your profile

```bash
python main.py setup --resume /path/to/resume.docx
```

Reads your resume, extracts everything it can, then asks only what it couldn't find. Saves to `profile.yaml`. Run once, reuse forever.

### 4. Option A: Run via Web UI (Dashboard)

```bash
python app.py
# Opens http://localhost:8000
```

Drop your resume, paste the job URL, click Run Agent. The browser opens, you watch it fill the form, then review and decide before anything submits.

### 4. Option B: Run via CLI

```bash
# Dry run first — fills form but doesn't submit, saves screenshot
python main.py apply --job-url "https://ats.rippling.com/..." --dry-run

# Submit for real
python main.py apply --job-url "https://ats.rippling.com/..."
```

### 4. Option C: Run via AI Agent Skill (Fully Autonomous)

If you use an agentic IDE like Claude Desktop, Claude Code, or Antigravity IDE, you can install the included `SKILL.md` so your AI can operate this codebase for you directly in the chat window.

1. Navigate to your local agent skills folder (e.g. `~/.agent/skills/`)
2. Copy the `agent-skill` folder into it:
   ```bash
   cp -r apply-agent/agent-skill ~/.agent/skills/job-application-agent
   ```
3. Open a chat with your AI and prompt:
   *"Apply to this job [URL] using the job-application-agent skill with my standard resume."*
   
Your AI will automatically spin up the backend, use its terminal tools to securely upload your documents, monitor the background browser automation, and present you with a "Submit / Edit / Cancel" decision natively in the chat interface. **How do you know it actually submitted?** Right before closing the browser, the agent takes a full-page confirmation screenshot (saved to `output/confirmation.png`). Your AI will point you to this file as 100% visual proof that the ATS accepted your application!

---

## Project structure

```
apply-agent/
├── main.py                  # CLI — setup + apply commands
├── app.py                   # FastAPI backend
├── index.html               # Frontend — zero-dependency single HTML file
├── profile.yaml             # Your profile (written once by setup)
├── .env                     # MOONSHOT_API_KEY
├── requirements.txt
├── architecture.md          # Full system design
├── todo.md                  # Build progress
└── agent/
    ├── llm_client.py        # Shared Kimi client (moonshot-v1-auto)
    ├── docx_reader.py       # Extracts structured data from .docx resume
    ├── jd_fetcher.py        # Fetches JD from any Rippling URL
    ├── form_filler.py       # Playwright: fills form, review pause, submit
    └── field_answerer.py    # Kimi answers arbitrary form fields from profile
```

---

## Tech stack

| Component | Tool | Why |
|-----------|------|-----|
| Resume parsing | `python-docx` | Read Word files directly |
| LLM — field answering + feedback | Kimi (Moonshot API, OpenAI-compatible) | Fast, cheap, works via OpenAI SDK |
| Browser automation | Playwright | Handles JS-rendered React forms |
| .docx → PDF conversion | `docx2pdf` | Preserves original formatting exactly |
| Backend | FastAPI + uvicorn | Full control over API + SSE streaming |
| Frontend | Vanilla HTML/CSS/JS | Zero dependencies, full design control |

---

## The "tell us how you did it" story

Ben Booi asked applicants to use AI end-to-end and explain the process. Here's the pipeline:

1. **Resume extraction** — `python-docx` reads the .docx, extracts structured data. Kimi fixes any parsing quirks (swapped title/company fields from unusual formatting).

2. **Profile building** — One-time setup flow asks only what the resume can't answer (work authorization, salary expectations, EEO preferences). Saved to `profile.yaml` and reused.

3. **JD fetching** — Rippling's apply URL is a React SPA. The agent strips `/apply?...` to get the job detail page, fetches the JD text, then reconstructs the apply URL from the org slug and job UUID.

4. **Form filling** — Playwright opens the form in a real browser. Known fields (name, email, phone) are filled from the profile directly. Unknown fields — every EEO question, every motivational question, every dropdown — are answered by Kimi with the full profile and JD as context.

5. **Human review** — Before anything submits, the agent pauses and shows: every field it filled, a screenshot of the form, and Kimi's before/after suggestions for improving the resume for this specific role.

6. **Submission** — Only happens after explicit confirmation.
