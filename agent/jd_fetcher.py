"""
Fetches job description text from a URL.
Handles both static pages (requests) and JS-rendered pages (Playwright).
Also handles LinkedIn job post URLs by extracting the external apply link.
"""

import re
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


LINKEDIN_PATTERN = re.compile(r"linkedin\.com/jobs")
RIPPLING_PATTERN = re.compile(r"ats\.rippling\.com")


def fetch_jd(url: str) -> dict:
    """
    Returns:
        {
          "jd_text": str,     # full job description text
          "apply_url": str,   # direct ATS form URL (may differ from input)
          "company": str,     # company name
          "job_title": str,   # job title
        }
    """
    if LINKEDIN_PATTERN.search(url):
        return _fetch_linkedin_jd(url)
    return _fetch_direct_jd(url)


def _fetch_direct_jd(url: str) -> dict:
    """
    For direct ATS URLs (Rippling and others).

    Accepts either:
    - Job post URL:  ats.rippling.com/en-CA/{org}/jobs/{uuid}
    - Apply form URL: ats.rippling.com/en-CA/{org}/jobs/{uuid}/apply?...

    Always fetches the JD from the job post URL.
    Always returns the apply form URL for Playwright to open.
    """
    # Strip any /apply suffix to get the clean job post URL
    job_url = re.sub(r"/apply.*$", "", url)

    # Build the apply URL from the job post URL
    apply_url = _build_apply_url(job_url, url)

    try:
        resp = requests.get(job_url, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(separator="\n", strip=True)

        if len(text) > 200:
            title, company = _extract_title_company(soup, text)
            return {
                "jd_text": _clean_text(text),
                "apply_url": apply_url,
                "company": company,
                "job_title": title,
            }
    except Exception:
        pass

    # Fallback: JS-rendered — use Playwright
    return _fetch_with_playwright(job_url, apply_url=apply_url)


def _build_apply_url(job_url: str, original_url: str) -> str:
    """
    Constructs the apply form URL from a job post URL.

    Rippling pattern:
      job_url:   ats.rippling.com/en-CA/{org}/jobs/{uuid}
      apply_url: ats.rippling.com/en-CA/{org}/jobs/{uuid}/apply
                 ?jobBoardSlug={org}&jobId={uuid}&step=application

    If the original URL already has /apply in it, return it unchanged.
    """
    if "/apply" in original_url:
        return original_url

    # Extract org slug and job UUID from the job URL
    # Pattern: /jobs/{uuid} preceded by /{org}/
    rippling_match = re.search(
        r"ats\.rippling\.com/[^/]+/([^/]+)/jobs/([^/?]+)", job_url
    )
    if rippling_match:
        org = rippling_match.group(1)
        job_id = rippling_match.group(2)
        return (
            f"{job_url}/apply"
            f"?jobBoardSlug={org}&jobId={job_id}&step=application"
        )

    # Generic fallback: just append /apply
    return f"{job_url}/apply"


def _fetch_linkedin_jd(url: str) -> dict:
    """
    Fetches a LinkedIn job listing page.
    Extracts JD text and the external apply URL from the apply button.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")

        jd_text = page.inner_text("body")
        apply_url = url

        try:
            btn = page.locator(
                "a[data-tracking-control-name='public_jobs_apply-link-offsite']"
            )
            if btn.count() > 0:
                apply_url = btn.first.get_attribute("href") or url
        except Exception:
            pass

        title = ""
        try:
            title = page.locator("h1").first.inner_text()
        except Exception:
            pass

        browser.close()

    return {
        "jd_text": _clean_text(jd_text),
        "apply_url": apply_url,
        "company": "",
        "job_title": title,
    }


def _fetch_with_playwright(job_url: str, apply_url: str) -> dict:
    """Fallback for JS-rendered job pages."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(job_url, wait_until="networkidle")
        text = page.inner_text("body")

        title = ""
        try:
            title = page.locator("h1").first.inner_text()
        except Exception:
            pass

        browser.close()

    _, company = _extract_title_company(None, text)

    return {
        "jd_text": _clean_text(text),
        "apply_url": apply_url,
        "company": company,
        "job_title": title,
    }


def _extract_title_company(soup: BeautifulSoup | None, text: str) -> tuple[str, str]:
    """
    Extract job title and company name from page content.
    Strategy: first non-empty line of the JD text is almost always the job title.
    Company is looked up from og:site_name or the URL subdomain.
    """
    title = ""
    company = ""

    # Job title — first meaningful line of the body text
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) < 80 and not line.startswith(("http", "<", "{")):
            title = line
            break

    # Company — try og:site_name or og:title meta tags first
    if soup:
        og_site = soup.find("meta", property="og:site_name")
        if og_site and og_site.get("content"):
            company = og_site["content"].strip()

        if not company:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                # "Operations AI Engineer | Opendoor" → "Opendoor"
                parts = og_title["content"].split("|")
                if len(parts) > 1:
                    company = parts[-1].strip()
                    if not title:
                        title = parts[0].strip()

    return title, company


def _clean_text(text: str) -> str:
    """Remove excessive blank lines from extracted text."""
    lines = text.split("\n")
    cleaned = []
    prev_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        cleaned.append(line)
        prev_blank = is_blank
    return "\n".join(cleaned)
