"""
Apply Agent — entry point.

Usage:
  # First time: reads your resume, extracts what it can, asks only what's missing
  python main.py setup --resume /path/to/resume.docx

  # Apply to a job (dry run first — fills form but doesn't submit)
  python main.py apply --job-url <url> --dry-run

  # Apply for real
  python main.py apply --job-url <url>
"""

import argparse
import os
import sys
from typing import Any

import yaml

from agent.docx_reader import read_docx
from agent.jd_fetcher import fetch_jd
from agent.form_filler import fill_and_submit


PROFILE_PATH = "profile.yaml"
OUTPUT_DIR = "output"

DIVIDER = "-" * 50


# ── Setup command ──────────────────────────────────────────────────────────────

def cmd_setup(args: argparse.Namespace) -> None:
    """
    Reads resume.docx, shows what was extracted, confirms with user,
    then asks only the questions the resume couldn't answer.
    Writes profile.yaml.
    """
    resume_path = args.resume
    if not os.path.exists(resume_path):
        print(f"ERROR: Resume file not found: {resume_path}")
        sys.exit(1)

    print("\n=== Apply Agent — First-time Setup ===")
    print(f"Reading resume: {resume_path}\n")

    _check_env()
    extracted = read_docx(resume_path, use_claude=True)

    # ── Step 1: Show extracted info and let user correct it ────────────────────
    print(DIVIDER)
    print("EXTRACTED FROM YOUR RESUME — confirm or correct each field.")
    print("Press Enter to keep the value shown in brackets.\n")

    personal = extracted["personal"]
    work_history = extracted["work_history"]
    education = extracted["education"]
    skills = extracted["skills"]

    first_name  = _confirm("First name",   personal.get("first_name", ""))
    last_name   = _confirm("Last name",    personal.get("last_name", ""))
    email       = _confirm("Email",        personal.get("email", ""))
    phone       = _confirm("Phone",        personal.get("phone", ""))
    location    = _confirm("Location",     personal.get("location", "") or "Toronto, ON")
    linkedin    = _confirm("LinkedIn URL", personal.get("linkedin_url", ""))
    github      = _confirm("GitHub URL (Enter to skip)", personal.get("github_url", ""))
    portfolio   = _confirm("Portfolio URL (Enter to skip)", personal.get("portfolio_url", ""))

    print(f"\nWork history extracted: {len(work_history)} jobs")
    for job in work_history:
        print(f"  • {job['title']} @ {job['company']} ({job['start_date']} – {job['end_date']})")
    print(f"\nEducation extracted: {len(education)} entries")
    for edu in education:
        print(f"  • {edu['degree']} — {edu['institution']}")

    # ── Step 2: Ask what the resume can't answer ───────────────────────────────
    print(f"\n{DIVIDER}")
    print("A FEW QUESTIONS YOUR RESUME CAN'T ANSWER\n")

    pronouns        = _ask("Pronouns (e.g. He/Him, She/Her — Enter to skip)", "")
    current_title   = _confirm("Current job title", work_history[0]["title"] if work_history else "")
    current_company = _confirm("Current company",   work_history[0]["company"] if work_history else "")
    notice_period   = _ask("Notice period (e.g. '2 weeks', 'immediately available')", "2 weeks")
    open_to_start   = _ask("Earliest start date (e.g. 'immediately', '2026-06-01')", "immediately")

    print()
    authorized_canada  = _yes_no("Are you legally authorized to work in Canada?", default=True)
    needs_sponsorship  = _yes_no("Do you require visa sponsorship?", default=False)
    citizenship        = _ask("Citizenship status (e.g. Canadian Citizen, PR, Work Permit)", "Canadian Citizen")

    print()
    desired_salary = _ask("Desired salary in CAD (e.g. 130000)", "")
    salary_range   = _ask("Salary range in CAD (e.g. 120000-140000)", "")
    work_type      = _ask("Work preference (remote / hybrid / onsite)", "hybrid")
    willing_relocate = _yes_no("Willing to relocate?", default=False)
    preferred_loc  = _ask("Preferred work location", location)

    print()
    print("EEO — Self-identification (all voluntary, press Enter for 'Prefer not to say')")
    gender          = _ask("Gender", "")
    race_ethnicity  = _ask("Race / ethnicity", "")
    hispanic_latino = _ask("Hispanic/Latino? (Yes / No)", "")
    veteran_status  = _ask("Veteran status", "")
    disability      = _ask("Disability status", "")

    print()
    sms_consent = _yes_no("Consent to receive SMS messages from employers?", default=True)

    print()
    headline = _ask(
        "One-line career headline (used for 'Why do you want to work here?' fields)",
        "AI-native operator who builds agentic workflows and understands the business model they serve",
    )

    print()
    print("RESUME TAILORING")
    print("Default notes tell Kimi how to adapt your resume for any job.")
    print("You can override these per application at apply time.")
    default_tailoring = _ask(
        "Default tailoring notes (e.g. 'Lead with AI projects, de-emphasize insurance role')",
        "",
    )

    # ── Step 3: Build and save profile ────────────────────────────────────────
    profile: dict[str, Any] = {
        "resume_path": os.path.abspath(resume_path),
        "personal": {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
            "location": location,
            "linkedin_url": linkedin,
            "github_url": github,
            "portfolio_url": portfolio,
            "pronouns": pronouns,
        },
        "current": {
            "company": current_company,
            "title": current_title,
            "notice_period": notice_period,
            "open_to_start": open_to_start,
        },
        "work_authorization": {
            "canada": authorized_canada,
            "requires_sponsorship": needs_sponsorship,
            "citizenship": citizenship,
        },
        "compensation": {
            "desired_salary_cad": desired_salary,
            "salary_range_cad": salary_range,
            "open_to_negotiate": True,
        },
        "preferences": {
            "work_type": work_type,
            "willing_to_relocate": willing_relocate,
            "preferred_locations": [preferred_loc],
        },
        "eeo": {
            "gender": gender,
            "race_ethnicity": race_ethnicity,
            "hispanic_latino": hispanic_latino,
            "veteran_status": veteran_status,
            "disability_status": disability,
        },
        "sms_consent": sms_consent,
        "narrative": {
            "headline": headline,
        },
        "tailoring": {
            "default_notes": default_tailoring,
        },
        "work_history": work_history,
        "education": education,
        "skills": skills,
    }

    with open(PROFILE_PATH, "w") as f:
        yaml.dump(profile, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"\n{DIVIDER}")
    print(f"Profile saved to {PROFILE_PATH}")
    print("\nNext step — dry run to see the form filled without submitting:")
    print(f'  python main.py apply --job-url "<url>" --dry-run')


# ── Apply command ──────────────────────────────────────────────────────────────

def cmd_apply(args: argparse.Namespace) -> None:
    """Full pipeline: fetch JD → tailor resume → PDF → fill form → confirm → submit."""
    _check_env()

    if not os.path.exists(PROFILE_PATH):
        print("No profile found. Run first:")
        print("  python main.py setup --resume /path/to/resume.docx")
        sys.exit(1)

    with open(PROFILE_PATH) as f:
        profile = yaml.safe_load(f)

    resume_path = profile.get("resume_path", "")
    if not resume_path or not os.path.exists(resume_path):
        print(f"Resume file not found at path stored in profile: {resume_path}")
        print("Re-run setup or update 'resume_path' in profile.yaml")
        sys.exit(1)

    job_url = args.job_url
    dry_run = args.dry_run
    
    # Collect overrides from CLI if provided
    overrides = {}
    if args.salary: overrides["desired_salary"] = args.salary
    if args.notice: overrides["notice_period"] = args.notice
    if args.auth_canada: overrides["authorized_canada"] = args.auth_canada
    if args.sponsorship: overrides["requires_sponsorship"] = args.sponsorship
    if args.gender: overrides["gender"] = args.gender
    if args.race: overrides["race_ethnicity"] = args.race
    if args.veteran: overrides["veteran_status"] = args.veteran
    if args.disability: overrides["disability_status"] = args.disability

    print(f"\n=== Apply Agent ===")
    print(f"Job URL:  {job_url}")
    print(f"Resume:   {resume_path}")
    print(f"Dry run:  {dry_run}\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Step 1: Fetch JD
    print("[1/3] Fetching job description...")
    jd_data = fetch_jd(job_url)
    jd_text = jd_data["jd_text"]
    apply_url = jd_data["apply_url"]
    print(f"  Job: {jd_data['job_title']} @ {jd_data['company']}")
    print(f"  Fetched {len(jd_text)} chars.")

    # Step 2: Prepare resume file for upload
    # .pdf → upload as-is
    # .docx → convert to PDF so any ATS can parse it
    print("[2/3] Preparing resume for upload...")
    upload_path = _prepare_resume_for_upload(resume_path)
    print(f"  Upload file: {upload_path}")

    # Step 3: Fill form (Kimi answers fields using profile + JD context)
    print(f"[3/3] Opening form in browser...\n")
    success = fill_and_submit(
        apply_url=apply_url,
        resume_pdf_path=upload_path,
        profile=profile,
        jd_text=jd_text,
        dry_run=dry_run,
        overrides=overrides,
        screenshot_path=os.path.join(OUTPUT_DIR, "form_screenshot.png"),
    )

    if success:
        print("\nDone.")
        if dry_run:
            print("Review output/form_screenshot.png then run without --dry-run to submit for real.")
    else:
        print("\nSomething went wrong. Check output/form_screenshot.png for the current state.")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _confirm(label: str, extracted: str) -> str:
    """Show extracted value, let user press Enter to keep or type a correction."""
    if extracted:
        answer = input(f"{label} [{extracted}]: ").strip()
        return answer if answer else extracted
    else:
        return input(f"{label}: ").strip()


def _ask(label: str, default: str) -> str:
    """Ask a question with an optional default."""
    if default:
        answer = input(f"{label} [{default}]: ").strip()
        return answer if answer else default
    return input(f"{label}: ").strip()


def _prepare_resume_for_upload(resume_path: str) -> str:
    """
    Returns the path to upload. Most ATS accept .docx natively, 
    so we return the path directly instead of converting.
    """
    return resume_path


def _yes_no(prompt: str, default: bool = True) -> bool:
    default_str = "Y/n" if default else "y/N"
    answer = input(f"{prompt} ({default_str}): ").strip().lower()
    if not answer:
        return default
    return answer.startswith("y")


def _check_env() -> None:
    if not os.environ.get("MOONSHOT_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: MOONSHOT_API_KEY not set.")
        print("  export MOONSHOT_API_KEY=your-key-here")
        sys.exit(1)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Apply Agent — AI-powered job application filler"
    )
    subparsers = parser.add_subparsers(dest="command")

    # setup
    setup_parser = subparsers.add_parser(
        "setup", help="First-time setup: reads your resume, builds your profile"
    )
    setup_parser.add_argument(
        "--resume", required=True, help="Path to your resume .docx file"
    )

    # apply
    apply_parser = subparsers.add_parser("apply", help="Apply to a job")
    apply_parser.add_argument(
        "--job-url", required=True, help="Job posting URL or direct ATS form URL"
    )
    apply_parser.add_argument(
        "--dry-run", action="store_true",
        help="Fill the form but do not click submit"
    )
    # Overrides
    apply_parser.add_argument("--salary", help="Override desired salary")
    apply_parser.add_argument("--notice", help="Override notice period")
    apply_parser.add_argument("--auth-canada", help="Override work auth in Canada (yes/no)")
    apply_parser.add_argument("--sponsorship", help="Override sponsorship requirement (yes/no)")
    apply_parser.add_argument("--gender", help="Override gender")
    apply_parser.add_argument("--race", help="Override race/ethnicity")
    apply_parser.add_argument("--veteran", help="Override veteran status")
    apply_parser.add_argument("--disability", help="Override disability status")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup(args)
    elif args.command == "apply":
        cmd_apply(args)
    else:
        parser.print_help()
