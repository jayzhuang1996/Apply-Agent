"""
Uses Kimi to answer arbitrary job application form fields.
Profile-as-context injection: the full profile + JD are in the system prompt.
Kimi figures out the right answer for any field label it receives.
"""

import yaml
from openai import OpenAI
from agent.llm_client import chat, MODEL_FAST


def build_system_prompt(profile: dict, jd_text: str) -> str:
    profile_yaml = yaml.dump(profile, default_flow_style=False, allow_unicode=True)
    return f"""You are filling out a job application form on behalf of this candidate.

CANDIDATE PROFILE:
---
{profile_yaml}
---

JOB DESCRIPTION:
---
{jd_text[:3000]}
---

RULES:
- Answer using only information from the candidate profile above.
- Never fabricate facts, credentials, or experience.
- For EEO fields (gender, race, ethnicity, veteran, disability): if the profile value is empty, return "Prefer not to say" or "Decline to identify" or the equivalent opt-out option.
- For yes/no authorization questions: use the work_authorization section of the profile.
- For salary fields: use the compensation section.
- For free-text motivational questions ("Why do you want to work here?"): draw from the narrative section and connect to the job description.
- Return ONLY the answer value. No explanation, no punctuation around it, no quotes unless the value itself contains them.
- For dropdown fields, return exactly one of the provided options. Match case exactly.
- For checkbox fields, return "check" or "uncheck".
- For text fields, return the text to type."""


def answer_field(
    label: str,
    field_type: str,
    options: list[str],
    profile: dict,
    jd_text: str,
    client: OpenAI | None = None,
) -> str:
    """
    Returns the value to fill for a given form field.

    Args:
        label: The field label text (e.g. "First name", "Are you authorized to work in Canada?")
        field_type: "text", "dropdown", "checkbox", "textarea", "file"
        options: List of dropdown options (empty for text/checkbox fields)
        profile: Parsed profile.yaml as a dict
        jd_text: Job description text
        client: Optional reused OpenAI client

    Returns:
        String value to fill/select
    """
    if field_type == "file":
        return ""

    system = build_system_prompt(profile, jd_text)

    options_text = f"\nOptions: {options}" if options else ""
    user_message = f"""Field label: "{label}"
Field type: {field_type}{options_text}

What value should be filled in for this field?"""

    return chat(
        system=system,
        user=user_message,
        model=MODEL_FAST,
        max_tokens=256,
        client=client,
    )
