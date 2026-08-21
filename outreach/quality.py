"""
Content-quality gate for generated outreach emails.

PR #1 made the outreach pipeline reliable at the *delivery* layer: structured
JSON output, classified retries, SMTP timeouts, atomic claims, Celery
durability. What it did not cover is whether the text Gemini returned is
actually fit to send. `ai_client.validate_and_clean` only checks length and
strips markdown, so a structurally-valid JSON response whose body reads

    "We've been impressed by [Company]'s work in [specific domain]..."

passes untouched, is sent to a real prospect, and is recorded with
ai_used=True — i.e. it counts as a *success* on the reliability dashboard.

That specific failure is not hypothetical: the generation prompt in
tasks.py hands Gemini a sample email containing literal bracketed slots
([Company], [specific domain], [specific achievement/product], ...) and tells
it to follow that format. When the Google-Search grounding pass returns
nothing — which is swallowed silently and rendered into the prompt as
"No results available" — the model has nothing concrete to substitute and
routinely emits the scaffolding verbatim.

This module is the missing layer: a set of deterministic, dependency-free
checks that score a generated (subject, body) pair and classify each defect
as a BLOCKER (must never be sent) or a WARNING (send, but record it). It is
deliberately *not* an LLM call — the gate must be fast, free, and
deterministic enough to unit-test, so it can run on every single email
without adding latency or quota cost to the batch.
"""

import re
from dataclasses import dataclass, field

# --- severities -------------------------------------------------------------

BLOCKER = "blocker"
WARNING = "warning"

# Score deducted per issue type. Blockers also hard-fail the gate regardless
# of the resulting score, so these weights only shape the reported score.
_WEIGHTS = {
    "unfilled_placeholder": 45,
    "template_token_leak": 45,
    "ai_meta_commentary": 30,
    "missing_company": 25,
    "missing_signature": 20,
    "generic_fallback_clone": 20,
    "empty_body": 100,
    "body_too_short": 30,
    "body_too_long": 10,
    "wrong_recipient": 15,
    "missing_greeting": 10,
    "markdown_artifact": 8,
    "subject_missing": 20,
    "subject_too_long": 5,
    "subject_prefix_mismatch": 5,
    "subject_in_body": 10,
    "unresolved_na": 15,
}


@dataclass
class Issue:
    code: str
    severity: str
    detail: str

    def as_dict(self):
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


@dataclass
class QualityReport:
    score: int = 100
    issues: list = field(default_factory=list)

    @property
    def blockers(self):
        return [i for i in self.issues if i.severity == BLOCKER]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def passed(self):
        """An email may only be sent as-is when it has no blocking defects."""
        return not self.blockers

    @property
    def codes(self):
        return [i.code for i in self.issues]

    def as_list(self):
        """JSON-serialisable form, for ActionLog.quality_issues / run logs."""
        return [i.as_dict() for i in self.issues]

    def summary(self):
        if not self.issues:
            return "clean"
        return "; ".join(f"{i.code}({i.severity})" for i in self.issues)

    def repair_instructions(self):
        """Human-readable defect list fed back to Gemini in the repair pass."""
        return "\n".join(f"- {i.code}: {i.detail}" for i in self.issues)


# --- detectors --------------------------------------------------------------

# Bracketed scaffolding the model copies from the sample email when it has no
# real fact to substitute: "[Company]", "[specific domain]", "[insert X]".
# Requires 2+ chars and at least one letter so real prose like "[1]" or a
# stray "]" doesn't trip it.
_SQUARE_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{2,80}\]")

# Angle-bracket slots: "<company name>", "<your name>".
_ANGLE_PLACEHOLDER_RE = re.compile(r"<[a-zA-Z][^>\n]{1,60}>")

# Django/Jinja-style tokens leaking out of an OutreachCampaign.email_template
# that was pasted into the prompt as campaign guidance and echoed back.
_CURLY_TOKEN_RE = re.compile(r"\{\{?\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\}?\}")

# Model talking to the operator instead of the prospect.
_META_PATTERNS = [
    r"\bhere(?:'s| is) (?:the|your|a) (?:email|draft|message)\b",
    r"\bas an ai\b",
    r"\bi hope this helps\b",
    r"\bi've (?:drafted|written|generated)\b",
    r"\blet me know if you(?:'d| would) like\b",
    r"\bfeel free to (?:adjust|modify|tweak)\b",
    r"\bbelow is (?:the|a) (?:email|draft)\b",
    r"^\s*(?:certainly|sure|of course)[!,.]",
    r"\b(?:draft|version) \d\b",
]
_META_RE = re.compile("|".join(_META_PATTERNS), re.IGNORECASE | re.MULTILINE)

# Leftover markdown after ai_client._strip_markdown ran.
_MARKDOWN_RE = re.compile(r"(\*\*|^#{1,6}\s|^\s*[-*]\s+)", re.MULTILINE)

# Literal "N/A" surfacing in prose — means an empty Apollo field was pasted
# straight into the email instead of being written around.
_NA_RE = re.compile(r"\bN/?A\b")

_SUBJECT_LINE_RE = re.compile(r"^\s*subject\s*:", re.IGNORECASE)

# Corporate suffixes stripped before matching a company name in the body, so
# "Acme Technologies Pvt Ltd" still matches a body that just says "Acme".
_COMPANY_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "pvt", "private", "corp", "corporation",
    "co", "company", "plc", "gmbh", "sa", "bv", "nv", "ag", "srl", "oy", "ab",
    "group", "holdings", "holding", "technologies", "technology", "tech",
    "labs", "lab", "solutions", "systems", "services", "ventures", "partners",
    "consulting", "consultancy", "india", "global", "international", "the",
    "and", "of", "for",
}

_MIN_BODY_CHARS = 200
_MAX_BODY_CHARS = 6000
_MAX_SUBJECT_CHARS = 160


def _distinctive_company_tokens(company: str) -> list:
    """
    Reduce a company name to the tokens that actually identify it.

    Returns [] when nothing distinctive survives (e.g. "Tech Solutions"),
    in which case the caller must skip the missing-company check rather than
    raise a false blocker on a name made entirely of generic words.
    """
    cleaned = re.sub(r"[^\w\s]", " ", (company or "").lower())
    tokens = [t for t in cleaned.split() if len(t) >= 3 and t not in _COMPANY_SUFFIXES]
    return tokens


def check_email(
    subject: str,
    body: str,
    *,
    company: str = "",
    contact_person: str = "",
    sender_name: str = "",
    subject_prefix: str = "",
    fallback_body: str = "",
) -> QualityReport:
    """
    Score a generated (subject, body) pair against the things that actually
    make an outreach email unsendable.

    Every argument beyond subject/body is optional context — a check whose
    context is missing is skipped rather than guessed at, so this never
    invents a blocker it cannot substantiate.
    """
    report = QualityReport()
    subject = (subject or "").strip()
    body = (body or "").strip()

    def add(code, severity, detail):
        report.issues.append(Issue(code, severity, detail))

    # -- body presence ------------------------------------------------------
    if not body:
        add("empty_body", BLOCKER, "Generated body was empty.")
        report.score = 0
        return report

    if len(body) < _MIN_BODY_CHARS:
        add(
            "body_too_short",
            BLOCKER,
            f"Body is only {len(body)} chars; a real outreach email needs at "
            f"least {_MIN_BODY_CHARS}.",
        )
    elif len(body) > _MAX_BODY_CHARS:
        add("body_too_long", WARNING, f"Body is {len(body)} chars.")

    # -- unfilled scaffolding (the headline defect this gate exists for) ----
    square = _SQUARE_PLACEHOLDER_RE.findall(body) + _SQUARE_PLACEHOLDER_RE.findall(subject)
    angle = _ANGLE_PLACEHOLDER_RE.findall(body)
    placeholders = square + angle
    if placeholders:
        shown = ", ".join(repr(p) for p in placeholders[:4])
        add(
            "unfilled_placeholder",
            BLOCKER,
            f"Contains {len(placeholders)} unfilled placeholder(s) copied from "
            f"the sample email: {shown}. Replace each with a real, specific "
            f"fact about the company, or rewrite the sentence without it.",
        )

    curly = _CURLY_TOKEN_RE.findall(body) + _CURLY_TOKEN_RE.findall(subject)
    if curly:
        shown = ", ".join(repr(c) for c in curly[:4])
        add(
            "template_token_leak",
            BLOCKER,
            f"Contains un-substituted template token(s): {shown}.",
        )

    # -- model talking to the operator --------------------------------------
    meta = _META_RE.search(body)
    if meta:
        add(
            "ai_meta_commentary",
            BLOCKER,
            f"Body contains assistant commentary aimed at the operator rather "
            f"than the prospect: {meta.group(0)!r}. The body must contain only "
            f"the email itself.",
        )

    if _SUBJECT_LINE_RE.search(body):
        add(
            "subject_in_body",
            WARNING,
            "Body starts with a 'Subject:' line; the subject belongs in the "
            "subject field only.",
        )

    # -- personalization actually happened ----------------------------------
    body_lower = body.lower()
    tokens = _distinctive_company_tokens(company)
    if tokens and not any(t in body_lower for t in tokens):
        add(
            "missing_company",
            BLOCKER,
            f"Body never mentions the target company ({company!r}). An "
            f"outreach email that does not name the recipient's company is "
            f"not personalized.",
        )

    if _NA_RE.search(body):
        add(
            "unresolved_na",
            WARNING,
            "Body contains a literal 'N/A' — an empty data field was written "
            "into the email instead of being written around.",
        )

    if fallback_body and _near_identical(body, fallback_body):
        add(
            "generic_fallback_clone",
            WARNING,
            "Body is nearly identical to the static fallback template, so the "
            "AI call added no personalization despite succeeding.",
        )

    # -- structure the prompt explicitly demanded ---------------------------
    if sender_name and sender_name.lower() not in body_lower:
        add(
            "missing_signature",
            BLOCKER,
            f"Body is not signed by the sender ({sender_name!r}); the "
            f"signature block is missing or wrong.",
        )

    first_line = body.split("\n", 1)[0].strip()
    if not re.match(r"^(respected|dear|hi|hello)\b", first_line, re.IGNORECASE):
        add(
            "missing_greeting",
            WARNING,
            f"Body does not open with a greeting line (got {first_line[:60]!r}).",
        )
    elif contact_person:
        parts = [p for p in re.sub(r"[^\w\s]", " ", contact_person).split() if p]
        last_name = parts[-1] if parts else ""
        generic = re.search(r"\b(sir|ma'?am|madam|team)\b", first_line, re.IGNORECASE)
        if last_name and last_name.lower() not in first_line.lower() and not generic:
            add(
                "wrong_recipient",
                WARNING,
                f"Greeting {first_line[:60]!r} does not address the contact "
                f"({contact_person!r}).",
            )

    if _MARKDOWN_RE.search(body):
        add(
            "markdown_artifact",
            WARNING,
            "Body still contains markdown formatting after cleanup.",
        )

    # -- subject ------------------------------------------------------------
    if not subject:
        add("subject_missing", BLOCKER, "Subject line was empty.")
    else:
        if len(subject) > _MAX_SUBJECT_CHARS:
            add("subject_too_long", WARNING, f"Subject is {len(subject)} chars.")
        if subject_prefix and not subject.upper().startswith(subject_prefix.upper()):
            add(
                "subject_prefix_mismatch",
                WARNING,
                f"Subject does not start with {subject_prefix!r}.",
            )

    # -- score --------------------------------------------------------------
    deduction = sum(_WEIGHTS.get(i.code, 5) for i in report.issues)
    report.score = max(0, 100 - deduction)
    return report


def _near_identical(a: str, b: str, threshold: float = 0.9) -> bool:
    """
    Cheap token-overlap similarity — used only to notice that a 'successful'
    AI generation produced essentially the static template. Deliberately not
    difflib: this runs per-email and only needs a coarse signal.
    """
    at = set(re.findall(r"[a-z]{4,}", a.lower()))
    bt = set(re.findall(r"[a-z]{4,}", b.lower()))
    if not at or not bt:
        return False
    return len(at & bt) / max(len(at), len(bt)) >= threshold


def build_repair_prompt(original_prompt: str, subject: str, body: str, report: QualityReport) -> str:
    """
    Build the corrective follow-up prompt for a draft that failed the gate.

    Feeds the model its own rejected draft plus the specific, itemised
    reasons — a targeted repair converges far more often than blindly
    re-rolling the same prompt, and costs one call instead of discarding the
    generation entirely and dropping to the generic template.
    """
    return (
        f"{original_prompt}\n\n"
        f"---\n"
        f"IMPORTANT — YOUR PREVIOUS ATTEMPT WAS REJECTED.\n\n"
        f"You produced this draft:\n"
        f"Subject: {subject}\n"
        f"Body:\n{body}\n\n"
        f"It was rejected by an automated quality check for these reasons:\n"
        f"{report.repair_instructions()}\n\n"
        f"Rewrite the email so that every one of those problems is fixed. "
        f"In particular: never emit square-bracket or angle-bracket "
        f"placeholders such as [Company] or [specific domain] — the sample "
        f"format uses brackets only to show you where real details belong, "
        f"and every one of them must be replaced with a genuine, specific "
        f"detail. If you do not have a real fact for a slot, rewrite the "
        f"sentence so the slot is not needed rather than leaving a "
        f"placeholder. Keep the same tone, structure, and signature. "
        f"Respond as JSON matching the given schema."
    )
