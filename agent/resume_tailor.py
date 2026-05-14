"""
Uses Kimi to tailor a resume .docx to a specific job description.
Supports optional user-provided tailoring notes that guide how the resume
should be adapted — injected directly into the prompt.
"""

from docx import Document
from agent.llm_client import chat, MODEL_LARGE


SYSTEM_PROMPT = """You are a professional resume writer helping a candidate tailor their resume
for a specific job. Follow every rule exactly.

CONTENT RULES:
1. NEVER fabricate, invent, or add facts not in the original resume.
2. Keep all dates, company names, job titles, and metrics word-for-word.
3. You MAY reorder bullet points within a section to surface the most relevant ones first.
4. You MAY rewrite bullet point wording to mirror job description language where it genuinely applies.
5. You MAY adjust the professional summary to speak to this role.

FORMAT RULES (these override tailoring instructions):
6. Preserve the EXACT section order from the original — do not move sections around.
7. Preserve the EXACT number of bullet points per job — never add or remove bullets.
8. Never move content from one section into another section — each bullet stays in its original section.
9. Keep section headers exactly as they appear in the original (same capitalisation, same style).
10. Keep the same Markdown: # for name, ## for section headers, - for bullets.
11. Final resume must be within 10% of the original word count — do not add or remove content significantly.

OUTPUT:
Output ONLY the tailored resume in Markdown. No preamble, no explanation, no commentary."""


def tailor_resume(
    resume_path: str,
    jd_text: str,
    profile: dict,
    tailoring_notes: str = "",
) -> str:
    """
    Reads the candidate's .docx, tailors it to the JD using Kimi.
    Returns the tailored resume as a Markdown string.

    Args:
        resume_path: Path to the .docx file
        jd_text: Job description text
        profile: Parsed profile.yaml (used for narrative/headline context)
        tailoring_notes: Optional user instructions for how to adapt the resume
                         (e.g. "Emphasize AI projects, de-emphasize insurance role")
    """
    resume_text = _read_docx_text(resume_path)
    headline = profile.get("narrative", {}).get("headline", "")

    notes_block = ""
    if tailoring_notes and tailoring_notes.strip():
        notes_block = f"""
The candidate has provided tailoring guidance below. Apply it ONLY by reordering or rewriting
bullet points — NEVER by removing jobs, sections, or bullet points entirely:

<tailoring_instructions>
{tailoring_notes.strip()}
</tailoring_instructions>
"""

    headline_block = ""
    if headline:
        headline_block = f"\nThe candidate's career headline: {headline}\n"

    user_message = f"""Here is the candidate's current resume:

<resume>
{resume_text}
</resume>

Here is the job description they are applying to:

<job_description>
{jd_text[:4000]}
</job_description>
{headline_block}{notes_block}
Please tailor the resume for this role. Reframe and emphasize only — never fabricate."""

    return chat(
        system=SYSTEM_PROMPT,
        user=user_message,
        model=MODEL_LARGE,
        max_tokens=4096,
    )


def _read_docx_text(path: str) -> str:
    """
    Extract resume text from a .docx for passing to the LLM.
    Stops at cover letter / second document boundaries (same logic as docx_reader).
    """
    import re
    doc = Document(path)

    stop_signals = re.compile(
        r"^(dear |hi [a-z]+,|sincerely|re:\s|to whom it may|hiring manager|just applied|knowing [a-z])",
        re.IGNORECASE,
    )
    contact_pattern = re.compile(r".+@.+\|.+\|.+")

    lines = []
    name_line = None
    contact_count = 0

    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        if stop_signals.match(text):
            break
        if name_line is None:
            name_line = text
            lines.append(text)
            continue
        if contact_pattern.search(text):
            contact_count += 1
            if contact_count > 1:
                break
        if name_line and text == name_line and len(lines) > 5:
            break
        lines.append(text)

    return "\n".join(lines)
