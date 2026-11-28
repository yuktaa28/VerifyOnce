"""Optional plain-language explanations of recorded decisions (off by default).

Enabled with EXPLANATIONS_ENABLED=1, so the demo makes no extra API calls otherwise.

* Gemini (GEMINI_MODEL) writes a 2-3 sentence explanation for the citizen and a short
  brief for the officer. It is given only structured decision data: scheme name,
  verdict, rule outcomes, and flag types with the numbers involved. Never document
  text, never a person's name.
* The text is validated: it may not mention a rule ID, a number, or a capitalised
  name that is not in that data, and it must state the same outcome as the recorded
  verdict. If the API fails, hits quota (429), stays overloaded
  (503 after the usual retries), is blocked, or the text fails validation, a template
  explanation is used instead.
* The explanation is stored on the Decision when it is recorded, labelled
  "generated" or "template" with the model. It never makes or changes a verdict.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.config import Settings
from backend.evaluator import APPROVE, NEEDS_HUMAN_REVIEW, REJECT, EvaluationResult
from backend.extraction import RETRY_DELAYS, GeminiProvider, call_with_retries
from backend.fields import DOCUMENT_LABELS, FIELD_LABELS, field_label, format_value
from backend.schemes import Scheme

GENERATED = "generated"
TEMPLATE = "template"

VERDICT_TEXT = {APPROVE: "approved", REJECT: "not approved", NEEDS_HUMAN_REVIEW: "sent to an officer for review"}
ISSUE_TEXT = {
    "missing": "is missing from the documents",
    "invalid": "could not be read on at least one document",
    "low_quality_image": "comes only from a blurry or low-contrast scan",
    "low_confidence": "was read with low confidence",
    "conflict": "differs between documents",
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "citizen_explanation": {"type": "string", "description": "2-3 plain sentences for the applicant"},
        "officer_brief": {"type": "string", "description": "1-2 short sentences for the officer"},
    },
    "required": ["citizen_explanation", "officer_brief"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You explain eligibility decisions that a rules engine has already made for a government benefits office.

The JSON you receive is the complete record of one decision. Rules:
- Never change, soften, question or predict the verdict. Explain the verdict exactly as given.
- Use only facts in the JSON. Do not mention any number, rule ID, scheme, document or name that is not in it.
- Never mention a person's name or any personal detail.
- citizen_explanation: 2-3 short, plain sentences addressed to the applicant ("you", "your"). No rule IDs.
- officer_brief: 1-2 short sentences for the officer; you may cite rule IDs from the JSON.
Reply with only the JSON object."""


@dataclass(frozen=True)
class Explanation:
    citizen: str
    officer_brief: str
    source: str  # "generated" or "template"
    model: str | None = None
    note: str | None = None  # why the template was used


# --- Structured input ---------------------------------------------------------------------


def decision_facts(scheme: Scheme, verdict: str, result: EvaluationResult, *, decided_by: str) -> dict[str, Any]:
    """The only data the model sees: no document text, no names, no identity values."""
    rules = {r.id: r for r in scheme.rules}
    outcomes = []
    for o in result.rule_outcomes:
        outcomes.append({
            "rule_id": o.rule_id,
            "requirement": rules[o.rule_id].description if o.rule_id in rules else None,
            "field": field_label(o.field),
            "value_found": format_value(o.field, o.actual) if o.actual is not None else None,
            "limit": format_value(o.field, o.expected) if o.expected is not None else None,
            "outcome": o.outcome,
        })
    flags = []
    for r in result.review_reasons:
        flag = {"field": field_label(r.field), "issue": ISSUE_TEXT.get(r.issue, r.issue)}
        if r.issue == "low_confidence":
            match = re.search(r"confidence (\d+)%, below the (\d+)% cutoff", r.message)
            if match:
                flag["confidence_percent"], flag["cutoff_percent"] = int(match.group(1)), int(match.group(2))
        flags.append(flag)
    return {
        "scheme": scheme.name,
        "verdict": VERDICT_TEXT.get(verdict, verdict),
        "decided_by": "an officer" if decided_by.startswith("officer:") else "the rules engine (automatic)",
        "rules_passed": sum(o.outcome == "pass" for o in result.rule_outcomes),
        "rules_failed": sum(o.outcome == "fail" for o in result.rule_outcomes),
        "rules_unresolved": sum(o.outcome == "unresolved" for o in result.rule_outcomes),
        "rule_outcomes": outcomes,
        "flags": flags,
    }


# --- Template fallback --------------------------------------------------------------------


def template_explanation(facts: dict[str, Any], *, note: str | None = None) -> Explanation:
    scheme, verdict = facts["scheme"], facts["verdict"]
    failed = [o for o in facts["rule_outcomes"] if o["outcome"] == "fail"]
    passed = [o for o in facts["rule_outcomes"] if o["outcome"] == "pass"]
    if verdict == VERDICT_TEXT[APPROVE]:
        citizen = (f"Your application for {scheme} was approved. Your verified documents met every "
                   f"requirement that was checked.")
    elif verdict == VERDICT_TEXT[REJECT]:
        reasons = "; ".join(f"{o['field'].lower()} {o['value_found']} did not meet: {o['requirement']}"
                            for o in failed) or "an officer decided it does not qualify"
        citizen = f"Your application for {scheme} was not approved: {reasons}."
        if passed:
            citizen += " The other requirements were met."
    else:
        issues = "; ".join(f"{f['field'].lower()} {f['issue']}" for f in facts["flags"]) or "some evidence needs a check"
        citizen = (f"Your application for {scheme} was sent to an officer for review because {issues}. "
                   "No automatic decision was made, and an officer will decide.")
    brief = (f"{verdict.capitalize()} by {facts['decided_by']}: {facts['rules_passed']} rule(s) passed, "
             f"{facts['rules_failed']} failed, {facts['rules_unresolved']} unresolved.")
    if facts["flags"]:
        brief += " Flags: " + "; ".join(f"{f['field']} {f['issue']}" for f in facts["flags"]) + "."
    return Explanation(citizen, brief, TEMPLATE, None, note)


# --- Validation ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_RULE_ID = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_CAPITALISED = re.compile(r"\b[A-Z][a-z]+\b")


def _numbers(text: str) -> set[float]:
    found = set()
    for token in _NUMBER.findall(text):
        cleaned = token.replace(",", "").rstrip(".")
        try:
            found.add(round(float(cleaned), 4))
        except ValueError:
            continue
    return found


def _allowed_numbers(facts: dict[str, Any]) -> set[float]:
    allowed = _numbers(json.dumps(facts, ensure_ascii=False))
    for value in list(allowed):
        allowed.add(round(value * 100, 4))  # 0.62 <-> 62
        allowed.add(round(value / 100, 4))
    return allowed


def _allowed_words(facts: dict[str, Any]) -> set[str]:
    words = set(re.findall(r"[A-Za-z]+", json.dumps(facts, ensure_ascii=False)))
    for label in [*FIELD_LABELS.values(), *DOCUMENT_LABELS.values()]:
        words.update(re.findall(r"[A-Za-z]+", label))
    return {w.lower() for w in words} | {"inr", "rs", "rupees"}


_NEGATIVE = ("not approved", "not eligible", "rejected", "does not qualify", "did not qualify", "denied",
             "unsuccessful", "not successful")


def verdict_problem(text: str, verdict_text: str) -> str | None:
    """The citizen text must state the same outcome as the recorded verdict."""
    lowered = text.lower()
    negative = any(phrase in lowered for phrase in _NEGATIVE)
    if verdict_text == VERDICT_TEXT[APPROVE] and ("approved" not in lowered or negative):
        return "text does not say the application was approved"
    if verdict_text == VERDICT_TEXT[REJECT] and not negative:
        return "text does not say the application was not approved"
    if verdict_text == VERDICT_TEXT[NEEDS_HUMAN_REVIEW] and not ("officer" in lowered or "review" in lowered):
        return "text does not say an officer will review it"
    return None


def validate_text(text: str, facts: dict[str, Any]) -> list[str]:
    """Problems with a generated text: rule IDs, numbers or names that are not in the decision."""
    problems = []
    rule_ids = {o["rule_id"] for o in facts["rule_outcomes"]}
    for rule_id in set(_RULE_ID.findall(text)) - rule_ids:
        problems.append(f"unknown rule ID {rule_id!r}")
    allowed_numbers = _allowed_numbers(facts)
    for number in sorted(_numbers(text) - allowed_numbers):
        problems.append(f"number {number:g} is not in the decision")
    allowed_words = _allowed_words(facts)
    for match in _CAPITALISED.finditer(text):
        before = text[:match.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?:\n"
        if not sentence_start and match.group(0).lower() not in allowed_words:
            problems.append(f"name {match.group(0)!r} is not in the decision")
    return problems


# --- Explainer ----------------------------------------------------------------------------


class Explainer:
    def __init__(self, *, enabled: bool, provider: GeminiProvider | None,
                 retry_delays: tuple[float, ...] = RETRY_DELAYS, sleep: Callable[[float], None] = time.sleep):
        self.enabled = enabled
        self.provider = provider
        self.retry_delays = retry_delays
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"Explainer(enabled={self.enabled}, provider={self.provider!r})"

    @classmethod
    def from_settings(cls, settings: Settings, *, client: Any = None) -> "Explainer":
        provider = GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model, client=client)
        return cls(enabled=settings.explanations_enabled, provider=provider)

    def explain(self, scheme: Scheme, verdict: str, result: EvaluationResult, *, decided_by: str) -> Explanation | None:
        """None when disabled. Otherwise a generated explanation, or the template on any failure."""
        if not self.enabled:
            return None
        facts = decision_facts(scheme, verdict, result, decided_by=decided_by)
        if self.provider is None or self.provider.missing_configuration():
            return template_explanation(facts, note="GEMINI_API_KEY is not set")
        prompt = json.dumps(facts, ensure_ascii=False, indent=1)
        reply, _calls, error = call_with_retries(
            lambda: self.provider.generate_text(system=SYSTEM_PROMPT, prompt=prompt, schema=RESPONSE_SCHEMA),
            provider_name=self.provider.name, label=f"explanation for {scheme.id}",
            delays=self.retry_delays, sleep=self._sleep,
        )
        if error is not None:
            return template_explanation(facts, note=str(error))
        if reply.blocked:
            return template_explanation(facts, note=reply.blocked)
        try:
            data = json.loads(reply.text)
            citizen = " ".join(str(data["citizen_explanation"]).split())
            brief = " ".join(str(data["officer_brief"]).split())
        except (ValueError, KeyError, TypeError):
            return template_explanation(facts, note="reply was not the expected JSON")
        problems = validate_text(citizen, facts) + validate_text(brief, facts)
        mismatch = verdict_problem(citizen, facts["verdict"])
        if mismatch:
            problems.append(mismatch)
        if not citizen or not brief or problems:
            return template_explanation(facts, note="validation failed: " + "; ".join(problems or ["empty text"]))
        return Explanation(citizen, brief, GENERATED, reply.model)
