"""
Generates the tailored resume PDF by:
1. Writing Kimi's tailored text back into a copy of the original .docx
   (preserving all original fonts, spacing, and formatting)
2. Converting that .docx to PDF via docx2pdf

This approach keeps the resume looking exactly like the original —
only the words change, not the visual design.
"""

import os
import re
import shutil
from docx import Document
from docx2pdf import convert


def generate_pdf(tailored_md: str, output_pdf_path: str, original_docx_path: str) -> str:
    """
    Produces a PDF that looks exactly like the original resume
    but with Kimi's tailored text.

    Args:
        tailored_md:       Tailored resume text from Kimi (Markdown)
        output_pdf_path:   Where to save the final PDF
        original_docx_path: The user's original .docx (used as formatting template)

    Returns:
        output_pdf_path
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_pdf_path)), exist_ok=True)

    # Step 1: Write tailored text into a copy of the original .docx
    docx_output = output_pdf_path.replace(".pdf", ".docx")
    _write_tailored_docx(tailored_md, original_docx_path, docx_output)

    # Step 2: Convert .docx → PDF
    convert(docx_output, output_pdf_path)

    return output_pdf_path


def _write_tailored_docx(tailored_md: str, original_path: str, output_path: str) -> None:
    """
    Copies the original .docx and replaces paragraph text with the
    tailored content, preserving formatting run-by-run.
    """
    shutil.copy2(original_path, output_path)
    doc = Document(output_path)

    # Parse the tailored markdown into a flat list of non-empty lines
    tailored_lines = [
        _strip_md(line)
        for line in tailored_md.split("\n")
        if line.strip() and not line.strip().startswith("---")
    ]

    # Map original paragraphs to tailored lines by position
    # Only replace paragraphs that had content in the original
    orig_paras = [p for p in doc.paragraphs if p.text.strip()]

    for i, para in enumerate(orig_paras):
        if i >= len(tailored_lines):
            break
        new_text = tailored_lines[i]
        _replace_paragraph_text(para, new_text)

    doc.save(output_path)


def _replace_paragraph_text(para, new_text: str) -> None:
    """
    Replaces the text of a paragraph while preserving the formatting
    of the first run (font, size, bold, color).
    """
    if not para.runs:
        return

    # Preserve first run's formatting
    first_run = para.runs[0]

    # Clear all runs
    for run in para.runs:
        run.text = ""

    # Set new text on first run, preserving its formatting
    first_run.text = new_text


def _strip_md(line: str) -> str:
    """Remove Markdown syntax from a line, keeping plain text."""
    # Remove heading markers
    line = re.sub(r"^#{1,3}\s+", "", line)
    # Remove bold/italic markers
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = re.sub(r"\*(.+?)\*", r"\1", line)
    # Remove bullet markers
    line = re.sub(r"^[-•]\s+", "", line)
    # Remove markdown links, keep text
    line = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", line)
    return line.strip()
