"""
Extracts structured profile information from a resume .docx file.
Handles tab-separated date ranges on job header lines, lowercase section headers,
and stops reading when a cover letter or second document begins.
"""

import json
import os
import re
from docx import Document


# Section header keywords (lowercase)
WORK_HEADERS = {"experience", "professional experience", "work experience", "employment"}
EDUCATION_HEADERS = {"education", "academic background", "qualifications"}
SKILLS_HEADERS = {"skills", "technical skills", "additional information", "competencies", "tools"}
PROJECT_HEADERS = {"projects", "ai projects", "personal projects", "portfolio"}
ALL_HEADERS = WORK_HEADERS | EDUCATION_HEADERS | SKILLS_HEADERS | PROJECT_HEADERS

# Regex for contact info
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"[\+]?1?[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}")
LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[\w-]+/?")
GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/[\w-]+/?")

# Date range: handles "2025.03 – Present", "2022.09 – 2023.12", "Jan 2023 - Jul 2023"
DATE_RANGE_RE = re.compile(
    r"(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)?\.?\s?\d{4}(?:\.\d{2})?)"
    r"\s*[–—\-]+\s*"
    r"(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)?\.?\s?\d{4}(?:\.\d{2})?|[Pp]resent)",
    re.IGNORECASE,
)

# Cover letter / second-document signals — stop reading here
STOP_SIGNALS = re.compile(
    r"^(dear |hi [a-z]+,|sincerely|re:\s|to whom it may|hiring manager|just applied|knowing [a-z])",
    re.IGNORECASE,
)


def read_docx(path: str, use_claude: bool = False) -> dict:
    """
    Reads a resume .docx and returns structured data.

    Args:
        path: Path to the .docx file
        use_claude: If True, passes raw-extracted work history through Claude
                    to fix title/company swaps and other parsing errors.
                    Requires ANTHROPIC_API_KEY to be set.

    Returns:
        {
          "personal": { first_name, last_name, email, phone, location,
                        linkedin_url, github_url, portfolio_url, pronouns },
          "work_history": [ { company, title, start_date, end_date, location, description } ],
          "education": [ { institution, degree, field, graduation_year, location } ],
          "skills": { technical, tools, languages },
          "raw_text": str
        }
    """
    doc = Document(path)
    paragraphs = _get_resume_paragraphs(doc)
    full_text = "\n".join(paragraphs)

    work_history = _extract_work_history(paragraphs)
    if use_claude and work_history:
        work_history = _normalize_work_history(work_history)

    return {
        "personal": _extract_personal(paragraphs, full_text),
        "work_history": work_history,
        "education": _extract_education(paragraphs),
        "skills": _extract_skills(paragraphs),
        "raw_text": full_text,
    }


def _normalize_work_history(jobs: list[dict]) -> list[dict]:
    """
    Passes raw-extracted jobs through Kimi to fix title/company swaps,
    merged location fields, and other quirks that regex can't reliably handle.
    One API call for the entire work history.
    """
    if not os.environ.get("MOONSHOT_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        return jobs  # skip silently if no key set

    from agent.llm_client import chat, MODEL_FAST

    raw = json.dumps(jobs, indent=2)

    system = (
        "You are correcting extracted resume data. "
        "Fix any swapped title/company fields, merged location values, or other parsing errors. "
        "Rules: keep all original text verbatim — only move values between fields, never rewrite them. "
        "Return ONLY a valid JSON array with the same structure. No explanation."
    )
    user = (
        "Here is the extracted work history. "
        "Fix any title/company swaps or location parsing errors:\n\n"
        f"{raw}"
    )

    try:
        text = chat(system=system, user=user, model=MODEL_FAST, max_tokens=1024)
        text = re.sub(r"^```(?:json)?\n?", "", text.strip())
        text = re.sub(r"\n?```$", "", text)
        normalized = json.loads(text)
        if isinstance(normalized, list) and len(normalized) == len(jobs):
            return normalized
    except Exception:
        pass

    return jobs  # fall back to original if anything fails


def _get_resume_paragraphs(doc: Document) -> list[str]:
    """
    Return only the resume portion.
    Stops at:
    - Cover letter signals (Dear Hiring, Re:, Sincerely)
    - A second occurrence of the candidate's name (cover letter header)
    - A second contact-info line (email | phone | linkedin pattern)
    """
    lines = []
    name_line = None
    contact_line_count = 0
    contact_pattern = re.compile(r".+@.+\|.+\|.+")  # "email | phone | url" pattern

    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue

        # Hard stop signals
        if STOP_SIGNALS.match(text):
            break

        # Track the name (first line)
        if name_line is None:
            name_line = text
            lines.append(text)
            continue

        # Count contact-info lines — stop if we see a second one
        if contact_pattern.search(text):
            contact_line_count += 1
            if contact_line_count > 1:
                break

        # Stop if the candidate's own name reappears (cover letter header)
        if name_line and text.strip() == name_line.strip() and len(lines) > 5:
            break

        lines.append(text)

    return lines


# ── Personal ───────────────────────────────────────────────────────────────────

def _extract_personal(paragraphs: list[str], full_text: str) -> dict:
    # Name: first non-empty line
    name = paragraphs[0] if paragraphs else ""
    first_name, last_name = _split_name(name)

    # Contact line is usually line 1: "email | phone | linkedin"
    contact_line = paragraphs[1] if len(paragraphs) > 1 else full_text

    email = _find(EMAIL_RE, contact_line) or _find(EMAIL_RE, full_text)
    phone = _find(PHONE_RE, contact_line) or _find(PHONE_RE, full_text)
    linkedin = _find(LINKEDIN_RE, contact_line) or _find(LINKEDIN_RE, full_text)
    github = _find(GITHUB_RE, full_text)

    location = _extract_location(paragraphs)

    return {
        "first_name": first_name.title(),
        "last_name": last_name.title(),
        "email": email,
        "phone": phone,
        "location": location,
        "linkedin_url": linkedin,
        "github_url": github,
        "portfolio_url": "",
        "pronouns": "",
    }


def _split_name(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return name, ""


def _extract_location(paragraphs: list[str]) -> str:
    loc_re = re.compile(
        r"\b([A-Z][a-zA-Z\s]+,\s*(?:ON|BC|AB|QC|NS|MB|SK|NB|NL|PE|NT|NU|YT"
        r"|Ontario|British Columbia|Alberta|Quebec|Canada))\b"
    )
    for p in paragraphs[:5]:
        m = loc_re.search(p)
        if m:
            return m.group(1).strip()
    return ""


# ── Work history ───────────────────────────────────────────────────────────────

def _extract_work_history(paragraphs: list[str]) -> list[dict]:
    section = _get_section(paragraphs, WORK_HEADERS)
    jobs = []
    current_job = None
    bullets = []

    for line in section:
        date_match = DATE_RANGE_RE.search(line)

        if date_match:
            # Save previous
            if current_job:
                current_job["description"] = " ".join(bullets).strip()
                jobs.append(current_job)
                bullets = []

            # Split on tab — header is before the tab, dates after
            if "\t" in line:
                header_part = line[:line.index("\t")].strip()
            else:
                header_part = line[:date_match.start()].strip().rstrip(",—–- ")

            start = date_match.group(1).strip()
            end = date_match.group(2).strip()
            title, company, loc = _parse_job_header(header_part)

            current_job = {
                "company": company,
                "title": title,
                "start_date": start,
                "end_date": end,
                "location": loc,
                "description": "",
            }

        elif current_job and line and not line.lower() in ALL_HEADERS:
            bullets.append(line.lstrip("•–-· ").strip())

    if current_job:
        current_job["description"] = " ".join(bullets).strip()
        jobs.append(current_job)

    return jobs


def _parse_job_header(header: str) -> tuple[str, str, str]:
    """
    Parses "Title, Company, Location" → (title, company, location).
    Handles both comma and em-dash separators.
    """
    parts = re.split(r"\s*,\s*", header, maxsplit=2)
    if len(parts) >= 3:
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), ""
    return header.strip(), "", ""


# ── Education ──────────────────────────────────────────────────────────────────

def _extract_education(paragraphs: list[str]) -> list[dict]:
    section = _get_section(paragraphs, EDUCATION_HEADERS)
    entries = []
    current = None

    for line in section:
        date_match = DATE_RANGE_RE.search(line)
        year_match = re.search(r"\b(20\d{2})\b", line)

        has_edu_keyword = any(
            kw in line.lower()
            for kw in ["university", "college", "school", "bachelor", "master", "diploma", "mba", "bfa"]
        )

        if (date_match or year_match) and has_edu_keyword:
            if current:
                entries.append(current)

            if date_match and "\t" in line:
                header_part = line[:line.index("\t")].strip()
                grad_year = date_match.group(2).strip()
            elif date_match:
                header_part = line[:date_match.start()].strip().rstrip(", ")
                grad_year = date_match.group(2).strip()
            elif year_match and "\t" in line:
                header_part = line[:line.index("\t")].strip()
                grad_year = year_match.group(1)
            else:
                header_part = line[:year_match.start()].strip().rstrip(", ") if year_match else line
                grad_year = year_match.group(1) if year_match else ""

            degree, institution, field, location = _parse_edu_header(header_part)
            current = {
                "institution": institution,
                "degree": degree,
                "field": field,
                "graduation_year": grad_year,
                "location": location,
            }

    if current:
        entries.append(current)

    return entries


def _parse_edu_header(header: str) -> tuple[str, str, str, str]:
    """
    Parses "Degree, Field, Institution, City, Province" variants.
    Returns (degree, institution, field, location).
    Examples:
      "Master of Business Administration, Smith School of Business, Queen's University, Kingston, ON"
      "Bachelor of Fine Arts, New Media, Toronto Metropolitan University, Toronto, ON"
    """
    parts = [p.strip() for p in re.split(r"\s*,\s*", header)]
    degree = parts[0] if len(parts) > 0 else ""

    # Identify which parts are institution vs field vs location
    # Heuristic: parts containing "University", "College", "School" are institution
    institution_parts = []
    field_parts = []
    location_parts = []

    inst_kw = {"university", "college", "school", "institute", "polytechnic"}
    province_kw = {"on", "bc", "ab", "qc", "ontario", "canada"}

    for part in parts[1:]:
        part_lower = part.lower()
        if any(kw in part_lower for kw in province_kw) and len(part) < 30:
            location_parts.append(part)
        elif any(kw in part_lower for kw in inst_kw):
            institution_parts.append(part)
        else:
            field_parts.append(part)

    institution = ", ".join(institution_parts)
    field = ", ".join(field_parts)
    location = ", ".join(location_parts)

    return degree, institution, field, location


# ── Skills ─────────────────────────────────────────────────────────────────────

def _extract_skills(paragraphs: list[str]) -> dict:
    section = _get_section(paragraphs, SKILLS_HEADERS)
    technical, tools, languages = [], [], []

    tool_kw = {"power bi", "excel", "sql", "python", "playwright", "claude", "anthropic", "git", "power query"}
    lang_kw = {"english", "mandarin", "cantonese", "french", "spanish", "japanese"}

    for line in section:
        if not line or line.lower().strip() in ALL_HEADERS:
            continue

        # Skip long prose sentences (narrative paragraphs, not skill lists)
        # A skills line is typically short or a comma-separated list
        # A prose sentence contains spaces-per-word ratio suggesting full sentences
        if len(line) > 120 and line.count(" ") / max(len(line.split(",")), 1) > 8:
            continue

        # Strip bullet prefix
        clean = re.sub(r"^[•–\-·]\s*", "", line)
        # Remove "Label: " prefix like "Data & Analytics: " or "Language: "
        clean = re.sub(r"^[^:]+:\s*", "", clean)
        items = re.split(r"[,;]\s*", clean)
        for item in items:
            item = item.strip().strip(".")
            if not item or len(item) < 2:
                continue
            # Skip items that look like prose (contain multiple words without being a skill)
            if len(item.split()) > 6 and not any(kw in item.lower() for kw in tool_kw | lang_kw):
                continue
            item_lower = item.lower()
            if any(kw in item_lower for kw in lang_kw):
                languages.append(item)
            elif any(kw in item_lower for kw in tool_kw):
                tools.append(item)
            else:
                technical.append(item)

    return {"technical": technical, "tools": tools, "languages": languages}


# ── Section extractor ──────────────────────────────────────────────────────────

def _get_section(paragraphs: list[str], target_headers: set) -> list[str]:
    """Returns lines belonging to the first matching section, stops at next section header."""
    in_section = False
    lines = []

    for p in paragraphs:
        p_lower = p.lower().strip()

        if p_lower in target_headers:
            in_section = True
            continue

        if in_section:
            if p_lower in ALL_HEADERS and p_lower not in target_headers:
                break
            lines.append(p)

    return lines


# ── Utility ────────────────────────────────────────────────────────────────────

def _find(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return m.group(0).strip() if m else ""


# ── CLI test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m agent.docx_reader <path/to/resume.docx>")
        sys.exit(1)

    result = read_docx(sys.argv[1])
    display = {k: v for k, v in result.items() if k != "raw_text"}
    print(json.dumps(display, indent=2, ensure_ascii=False))
