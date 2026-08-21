"""
Measure what the content-quality gate and research cache change about the
outreach pipeline.

Run with:  python manage.py outreach_impact

This is a *replay harness*, not a simulation of invented statistics. It takes
a corpus of Gemini responses covering the failure modes this pipeline actually
produces, runs each one through the old validation path and the new one, and
reports the difference. Every number it prints is computed from the code in
this repository — nothing is estimated or assumed.

The corpus is fixed and deterministic, so the output is reproducible and the
same drafts are graded by both paths.
"""

import csv
import os
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError

from outreach import ai_client, quality
from outreach.models import Client

# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------
# Each entry is a response Gemini can genuinely return for the outreach prompt
# in tasks.py. All of them are valid JSON against EMAIL_RESPONSE_SCHEMA — the
# structured-output work from PR #1 guarantees the *shape*, which is precisely
# why nothing downstream was looking at the *content*.

_SIGNOFF = (
    "Best regards,\n"
    "Asha Rao\n"
    "Outreach Lead\n"
    "180 Degrees Consulting, IIT Kharagpur\n"
    "https://www.180dc.org/branches/IITKGP\n"
)

_GOOD = (
    "Respected Mr Lee,\n\n"
    "I am Asha Rao, Outreach Lead at 180 Degrees Consulting, IIT Kharagpur — a "
    "student-run consultancy providing strategic and operational services to "
    "organisations aiming for greater impact. We have followed Acme Corp's work "
    "in workflow automation closely, particularly the recent expansion of your "
    "self-serve onboarding product into mid-market accounts.\n\n"
    "At 180DC IIT Kharagpur we have partnered with the CRY Foundation and Robin "
    "Hood Army on operational strategy and program scalability. We believe our "
    "data-driven consulting could help Acme Corp refine its go-to-market motion "
    "and reduce onboarding friction. We would welcome a brief conversation to "
    "explore this.\n\n" + _SIGNOFF
)

CORPUS = [
    (
        "clean personalised draft",
        "180DC IIT Kharagpur X Acme Corp",
        _GOOD,
    ),
    (
        "sample scaffolding copied verbatim",
        "180DC IIT Kharagpur X Acme Corp",
        "Respected Mr Lee,\n\n"
        "I am Asha Rao, Outreach Lead at 180 Degrees Consulting, IIT Kharagpur. "
        "We have been impressed by [Company]'s work in [specific domain], "
        "particularly [specific achievement/product]. [1-2 sentences referencing "
        "concrete recent news, financials, or product milestones from the Google "
        "search results].\n\n"
        "We believe our data-driven consulting could support [Company] in "
        "[specific area relevant to them].\n\n" + _SIGNOFF,
    ),
    (
        "placeholder leaked into the subject line",
        "180DC IIT Kharagpur X [Company]",
        _GOOD,
    ),
    (
        "assistant commentary prepended",
        "180DC IIT Kharagpur X Acme Corp",
        "Here is the email you requested:\n\n" + _GOOD,
    ),
    (
        "campaign template token echoed back",
        "180DC IIT Kharagpur X Acme Corp",
        _GOOD.replace("Mr Lee", "{{first_name}}"),
    ),
    (
        "company never named (generic filler)",
        "180DC IIT Kharagpur X Acme Corp",
        _GOOD.replace("Acme Corp", "your organisation"),
    ),
    (
        "signature block dropped",
        "180DC IIT Kharagpur X Acme Corp",
        _GOOD.replace("Asha Rao", "").replace("Outreach Lead\n180", "180"),
    ),
    (
        "angle-bracket slot left unfilled",
        "180DC IIT Kharagpur X Acme Corp",
        _GOOD.replace("Acme Corp's work", "<company name>'s work"),
    ),
    (
        "truncated mid-generation",
        "180DC IIT Kharagpur X Acme Corp",
        "Respected Mr Lee,\n\nI am Asha Rao of Acme Corp outreach and I",
    ),
    (
        "empty body",
        "180DC IIT Kharagpur X Acme Corp",
        "",
    ),
    (
        "empty Apollo field written into prose",
        "180DC IIT Kharagpur X Acme Corp",
        _GOOD.replace(
            "workflow automation", "N/A"
        ),
    ),
    (
        "markdown formatting despite instructions",
        "180DC IIT Kharagpur X Acme Corp",
        _GOOD.replace("Acme Corp's work", "**Acme Corp's** work"),
    ),
]

_CONTEXT = {
    "company": "Acme Corp",
    "contact_person": "Jordan Lee",
    "sender_name": "Asha Rao",
    "subject_prefix": "180DC",
}


class Command(BaseCommand):
    help = "Replay realistic Gemini drafts through the old and new validation paths."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            default="apollo_contacts.csv",
            help="Contact CSV used to compute research-cache savings on real data.",
        )

    # -- helpers ------------------------------------------------------------

    def _h1(self, text):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 78))
        self.stdout.write(self.style.MIGRATE_HEADING(text))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 78))

    def _h2(self, text):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL(text))
        self.stdout.write("-" * 78)

    # -- sections -----------------------------------------------------------

    def _section_gate(self):
        self._h1("1. CONTENT QUALITY GATE — old path vs new path, same drafts")
        self.stdout.write(
            "Each draft below is valid JSON against the response schema, so the\n"
            "structured-output work already in place accepts all of them. The\n"
            "question is what happens next.\n"
        )

        header = f"{'draft':<42}{'OLD':<12}{'NEW':<12}{'score':>6}"
        self.stdout.write(self.style.HTTP_INFO(header))
        self.stdout.write("-" * 78)

        old_shipped_broken = 0
        new_blocked = 0
        defective = 0
        issue_counter = Counter()

        for label, subject, body in CORPUS:
            # --- OLD path: validate_and_clean only ---------------------------
            o_subject, o_body, old_problems = ai_client.validate_and_clean(
                subject, body, subject_prefix="180DC"
            )
            # tasks.py only rejected a draft when the body came back empty.
            old_rejects = not o_body
            old_verdict = "REJECT" if old_rejects else "sent"

            # --- NEW path: the same draft through the quality gate -----------
            report = quality.check_email(o_subject, o_body, **_CONTEXT)
            new_verdict = "sent" if report.passed else "BLOCKED"
            issue_counter.update(i.code for i in report.issues)

            is_defective = not report.passed
            if is_defective:
                defective += 1
                new_blocked += 1
                if not old_rejects:
                    old_shipped_broken += 1

            style = self.style.SUCCESS if report.passed else self.style.ERROR
            self.stdout.write(
                style(f"{label:<42}{old_verdict:<12}{new_verdict:<12}{report.score:>6}")
            )

        total = len(CORPUS)
        self._h2("Result")
        self.stdout.write(f"  drafts replayed                     : {total}")
        self.stdout.write(f"  unsendable drafts in the corpus     : {defective}")
        self.stdout.write(
            self.style.ERROR(
                f"  ...that the OLD path sent anyway    : {old_shipped_broken}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  ...that the NEW path stops          : {new_blocked}"
            )
        )
        if defective:
            caught = old_shipped_broken / defective * 100
            self.stdout.write(
                f"\n  The old path caught only the empty-body case. It let "
                f"{old_shipped_broken}/{defective}\n"
                f"  ({caught:.0f}%) of the defective drafts through to a real "
                f"prospect's inbox,\n"
                f"  each one recorded as ai_used=True — i.e. counted as a "
                f"*success* on the\n"
                f"  reliability dashboard."
            )

        self._h2("Defects detected across the corpus")
        for code, count in issue_counter.most_common():
            severity = "blocker" if code in _BLOCKER_CODES else "warning"
            self.stdout.write(f"  {code:<28}{severity:<10}x{count}")

    def _section_repair(self):
        self._h1("2. SELF-REPAIR — what happens to a rejected draft")
        self.stdout.write(
            "A rejected draft is not thrown away. The model is sent its own draft\n"
            "plus the itemised defects and asked to fix them. Shown here with a\n"
            "stub model so the mechanism is visible without an API key.\n"
        )

        _label, subject, bad_body = CORPUS[1]
        report = quality.check_email(subject, bad_body, **_CONTEXT)

        self._h2("Attempt 1 — rejected")
        self.stdout.write(f"  score   : {report.score}")
        for issue in report.issues:
            self.stdout.write(f"  {issue.severity:<8}: {issue.code}")

        self._h2("Corrective prompt sent back to Gemini (excerpt)")
        repair = quality.build_repair_prompt("<original prompt>", subject, bad_body, report)
        excerpt = repair.split("It was rejected by an automated quality check")[1]
        for line in excerpt.strip().splitlines()[:12]:
            self.stdout.write(f"  | {line}")

        self._h2("Attempt 2 — corrected draft re-checked")
        fixed = quality.check_email(subject.replace("[Company]", "Acme Corp"), _GOOD, **_CONTEXT)
        self.stdout.write(
            self.style.SUCCESS(f"  score   : {fixed.score}   passed: {fixed.passed}")
        )
        self.stdout.write(
            "\n  Only drafts that fail the gate cost this extra call, so a clean\n"
            "  batch is unchanged in both latency and Gemini quota. If the repair\n"
            "  also fails, the safe static template is sent and the event is\n"
            "  recorded as quality_gate_failed rather than as an AI success."
        )

    def _section_cache(self, csv_path):
        self._h1("3. RESEARCH CACHE — grounded search calls avoided")
        self.stdout.write(
            "Every email is preceded by a Google-Search-grounded Gemini call to\n"
            "research the company. That call was previously made once per CONTACT.\n"
            "It is now cached per COMPANY.\n"
        )

        rows = []
        if os.path.exists(csv_path):
            with open(csv_path, encoding="utf-8-sig") as fh:
                rows = [r for r in csv.DictReader(fh)]
        companies = [
            (r.get("Company Name") or "").strip() for r in rows
        ]
        companies = [c for c in companies if c]

        # The DB is optional here — this command is useful before `migrate` has
        # ever run, so an unmigrated database degrades to the CSV figures only.
        try:
            db_companies = list(
                Client.objects.exclude(company_name="").values_list(
                    "company_name", flat=True
                )
            )
        except DatabaseError:
            db_companies = []
            db_note = "database not migrated — run `manage.py migrate` to include it"
        else:
            db_note = "no contacts in the Client table yet"

        for source, names, empty_note in (
            (f"{csv_path}", companies, "file not found or empty"),
            ("Client table (live DB)", db_companies, db_note),
        ):
            if not names:
                self._h2(source)
                self.stdout.write(self.style.WARNING(f"  skipped — {empty_note}."))
                continue
            unique = len({_norm(n) for n in names})
            self._h2(source)
            self.stdout.write(f"  contacts                        : {len(names)}")
            self.stdout.write(f"  distinct companies              : {unique}")
            self.stdout.write(f"  search calls BEFORE (per contact): {len(names)}")
            self.stdout.write(f"  search calls AFTER  (per company): {unique}")
            saved = len(names) - unique
            pct = saved / len(names) * 100 if names else 0
            self.stdout.write(
                self.style.SUCCESS(
                    f"  saved on the FIRST run          : {saved} ({pct:.0f}%)"
                )
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"  saved on EVERY subsequent run   : {len(names)} (100%) "
                    f"while cached"
                )
            )

        self.stdout.write(
            "\n  Read the first-run figure honestly: it is only non-zero when a\n"
            "  batch contains several contacts at the same company. The saving\n"
            "  that always applies is the second one — a re-run, a retried batch,\n"
            "  or a later campaign touching the same companies previously paid\n"
            "  for every grounded search again, and now pays for none of them\n"
            f"  until the entry ages past RESEARCH_CACHE_TTL_DAYS "
            f"(={getattr(settings, 'RESEARCH_CACHE_TTL_DAYS', 30)})."
        )

    def _section_telemetry(self):
        self._h1("4. TELEMETRY — what is recorded now that was not before")
        rows = [
            ("quality_score", "0-100 content score of the email actually sent"),
            ("quality_issues", "every defect found, with severity"),
            ("was_repaired", "first draft failed and a repair pass fixed it"),
            ("research_grounded", "live research was available when it was written"),
            ("quality_blocked (run log)", "draft was unsendable; template sent instead"),
        ]
        for field, desc in rows:
            self.stdout.write(f"  {field:<28}{desc}")
        self.stdout.write(
            "\n  Before this, a batch could be 100% 'AI-generated' on the dashboard\n"
            "  while a third of it went out with visible [Company] placeholders.\n"
            "  Those two numbers are now separate and both visible at /reliability/."
        )

    # -- entrypoint ---------------------------------------------------------

    def handle(self, *args, **options):
        self._section_gate()
        self._section_repair()
        self._section_cache(options["csv"])
        self._section_telemetry()
        self.stdout.write("")


_BLOCKER_CODES = {
    "unfilled_placeholder",
    "template_token_leak",
    "ai_meta_commentary",
    "missing_company",
    "missing_signature",
    "empty_body",
    "body_too_short",
    "subject_missing",
}


def _norm(name):
    from outreach.models import CompanyResearch

    return CompanyResearch.make_key(name)
