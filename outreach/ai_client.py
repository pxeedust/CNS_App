"""
Shared Gemini call layer for the outreach app.

Centralises everything that was previously duplicated (and fragile) across
_generate_ai_email / _generate_followup_email / _analyze_reply_sentiment in
tasks.py: client construction, structured JSON output (so responses no
longer need fragile string-splitting), transient-vs-permanent error
classification, and retry/backoff for the transient case only.
"""

import logging
import re
import threading

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = (
    "You are an expert business consultant writing highly professional, "
    "personalized outreach emails on behalf of 180 Degrees Consulting, "
    "IIT Kharagpur — a student-run consultancy providing strategic and "
    "operational services to organisations aiming for greater impact and "
    "efficiency. Always respond with the exact JSON schema you are given. "
    "Never include markdown formatting (no **, ##, or bullet points) inside "
    "the subject or body fields — they are sent as plain-text email content. "
    "Sound warm and human, not AI-generated."
)

EMAIL_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "subject": {"type": "STRING"},
        "body": {"type": "STRING"},
    },
    "required": ["subject", "body"],
}

SENTIMENT_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "sentiment": {
            "type": "STRING",
            "enum": [
                "Positive",
                "Interested",
                "Neutral",
                "Negative",
                "Not Interested",
            ],
        },
        "explanation": {"type": "STRING"},
    },
    "required": ["sentiment", "explanation"],
}

_TIMEOUT_MS = 30_000

_client_lock = threading.Lock()
_client_cache: dict[str, "genai.Client"] = {}


def get_client(api_key: str) -> "genai.Client":
    """Return a cached genai.Client for this API key (one per process)."""
    client = _client_cache.get(api_key)
    if client is not None:
        return client
    with _client_lock:
        client = _client_cache.get(api_key)
        if client is None:
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=_TIMEOUT_MS),
            )
            _client_cache[api_key] = client
        return client


def classify_gemini_exception(exc: Exception) -> str:
    """
    Classify a Gemini SDK exception as:
      - "transient"     — worth retrying (429 rate limit, 5xx overload, timeout)
      - "safety_block"  — the prompt/response was blocked by safety filters
      - "invalid"       — permanent client error (bad argument, 4xx other than 429)
      - "unknown"       — anything else (network errors, SDK internals)
    """
    if isinstance(exc, genai_errors.ServerError):
        return "transient"
    if isinstance(exc, genai_errors.ClientError):
        code = getattr(exc, "code", None)
        if code == 429:
            return "transient"
        return "invalid"
    text = str(exc).lower()
    if "safety" in text or "blocked" in text or "prohibited_content" in text:
        return "safety_block"
    if "timeout" in text or "timed out" in text or "unavailable" in text:
        return "transient"
    return "unknown"


def _is_transient(exc: Exception) -> bool:
    return classify_gemini_exception(exc) in ("transient", "unknown")


gemini_retry = retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(4),
    reraise=True,
)


@gemini_retry
def generate_json(
    *,
    api_key: str,
    model_name: str,
    prompt: str,
    response_schema: dict,
    system_instruction: str | None = SYSTEM_INSTRUCTION,
):
    """
    Call Gemini with structured JSON output. Retries transient failures
    (rate limit / server overload / timeout) with exponential backoff;
    safety blocks and invalid-argument errors are raised immediately
    without retrying, since retrying an identical bad prompt wastes calls
    and will never succeed.

    Returns the parsed dict on success. Raises on failure — callers are
    responsible for the fallback-to-template behavior.
    """
    client = get_client(api_key)
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=response_schema,
    )
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=config,
    )

    candidates = getattr(response, "candidates", None)
    if not candidates:
        raise ValueError("Gemini returned no candidates (likely a safety block).")
    finish_reason = str(getattr(candidates[0], "finish_reason", "") or "")
    if finish_reason and finish_reason.upper() not in ("STOP", "MAX_TOKENS", ""):
        raise ValueError(f"Gemini response blocked or truncated: {finish_reason}")

    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed if isinstance(parsed, dict) else dict(parsed)

    import json

    return json.loads(response.text)


@gemini_retry
def generate_text(*, api_key: str, model_name: str, prompt: str, config=None):
    """Plain (non-JSON) generation, used for the Google Search research pass."""
    client = get_client(api_key)
    return client.models.generate_content(model=model_name, contents=prompt, config=config)


_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
_MARKDOWN_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)")
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MARKDOWN_BULLET_RE = re.compile(r"^[ \t]*[-*]\s+", re.MULTILINE)


def _strip_markdown(text: str) -> str:
    """
    Defense-in-depth cleanup: the prompt already forbids markdown, but
    Gemini occasionally ignores instructions, so strip common markdown
    artifacts before the text goes out as a plain-text email.
    """
    text = _MARKDOWN_HEADING_RE.sub("", text)
    text = _MARKDOWN_BULLET_RE.sub("", text)
    text = _MARKDOWN_BOLD_RE.sub(r"\1", text)
    text = _MARKDOWN_ITALIC_RE.sub(r"\1", text)
    return text


def validate_and_clean(subject: str, body: str, *, subject_prefix: str | None = None):
    """
    Defense-in-depth content validation for a generated (subject, body) pair.

    Returns (subject, body, problems) where `problems` is a list of human
    -readable strings describing anything that was auto-corrected or looks
    suspicious — callers log/record these rather than silently discarding
    them, so a fallback-to-generic-subject decision is visible instead of
    invisible as it was before.
    """
    problems: list[str] = []

    subject = (subject or "").strip()
    body = (body or "").strip()

    if not body:
        problems.append("empty body")
    elif len(body) < 40:
        problems.append(f"body suspiciously short ({len(body)} chars)")
    elif len(body) > 6000:
        problems.append(f"body suspiciously long ({len(body)} chars), truncating")
        body = body[:6000]

    cleaned_body = _strip_markdown(body)
    if cleaned_body != body:
        problems.append("stripped stray markdown formatting")
        body = cleaned_body

    cleaned_subject = _strip_markdown(subject)
    if cleaned_subject != subject:
        problems.append("stripped stray markdown formatting from subject")
        subject = cleaned_subject

    if not subject:
        problems.append("empty subject")
    elif subject_prefix and not subject.upper().startswith(subject_prefix.upper()):
        problems.append(
            f"subject did not start with required prefix {subject_prefix!r} "
            f"(kept as-is rather than silently discarding personalization)"
        )

    return subject, body, problems
