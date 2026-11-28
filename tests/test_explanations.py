"""Optional explanations: off by default, structured input only, validated, template fallback."""

from __future__ import annotations

import json
import logging

import pytest
from google.genai import errors as genai_errors

from backend.evaluator import evaluate
from backend.explanations import GENERATED, TEMPLATE, Explainer, decision_facts, validate_text
from backend.extraction import GeminiProvider
from backend.service import profile_observations
from conftest import FakeGemini

SCHOLARSHIP = "state_student_scholarship"

GOOD = {
    "citizen_explanation": ("Your application for the State Student Scholarship was not approved because your "
                            "marks of 52% are below the 60% minimum. Your annual income of ₹2,10,000 is within "
                            "the ₹3,00,000 limit."),
    "officer_brief": "Rejected automatically: minimum_marks failed (52% vs 60%); income_limit passed.",
}


def below_marks(seeded_client, schemes):
    with seeded_client.app.state.session_factory() as session:
        observations = profile_observations(session, "seed-below-marks")
    return evaluate(schemes[SCHOLARSHIP], observations, 0.85)


def explainer(fake, **kwargs) -> Explainer:
    sleeps: list[float] = []
    ex = Explainer(enabled=True, provider=GeminiProvider(api_key="k", model="gemini-test", client=fake),
                   sleep=sleeps.append, **kwargs)
    ex.sleeps = sleeps
    return ex


def test_disabled_by_default_and_makes_no_call(seeded_client, schemes):
    from backend.config import Settings

    assert Settings(database_url="sqlite://").explanations_enabled is False
    fake = FakeGemini(default=json.dumps(GOOD))
    ex = Explainer(enabled=False, provider=GeminiProvider(api_key="k", client=fake))
    assert ex.explain(schemes[SCHOLARSHIP], "reject", below_marks(seeded_client, schemes), decided_by="evaluator") is None
    assert fake.calls == []


def test_valid_generated_text_is_kept_and_labelled(seeded_client, schemes):
    fake = FakeGemini(replies={"text": json.dumps(GOOD)})
    result = below_marks(seeded_client, schemes)
    explanation = explainer(fake).explain(schemes[SCHOLARSHIP], "reject", result, decided_by="evaluator")
    assert (explanation.source, explanation.model) == (GENERATED, "gemini-test")
    assert explanation.citizen == GOOD["citizen_explanation"]
    assert fake.calls[0]["config"].temperature == 0


def test_only_structured_decision_data_is_sent(seeded_client, schemes):
    fake = FakeGemini(replies={"text": json.dumps(GOOD)})
    explainer(fake).explain(schemes[SCHOLARSHIP], "reject", below_marks(seeded_client, schemes), decided_by="evaluator")
    (call,) = fake.calls
    assert len(call["contents"]) == 1 and isinstance(call["contents"][0], str)  # no image, no document
    sent = json.loads(call["contents"][0])
    assert set(sent) == {"scheme", "verdict", "decided_by", "rules_passed", "rules_failed", "rules_unresolved",
                         "rule_outcomes", "flags"}
    for personal in ("Arjun", "Mehta", "3390", "2006", "Maharashtra"):
        assert personal not in call["contents"][0]


@pytest.mark.parametrize(("bad", "problem"), [
    ({**GOOD, "officer_brief": "Rejected: grace_marks rule failed."}, "unknown rule ID 'grace_marks'"),
    ({**GOOD, "citizen_explanation": "You were not approved because your marks of 52% are below 65%."},
     "number 65 is not in the decision"),
    ({**GOOD, "citizen_explanation": "Arjun, you were not approved because your marks of 52% are below 60%. "
                                     "Ask Priya at the office."}, "name 'Priya' is not in the decision"),
])
def test_text_that_invents_facts_falls_back_to_the_template(seeded_client, schemes, bad, problem):
    fake = FakeGemini(replies={"text": json.dumps(bad)})
    explanation = explainer(fake).explain(schemes[SCHOLARSHIP], "reject", below_marks(seeded_client, schemes),
                                          decided_by="evaluator")
    assert explanation.source == TEMPLATE
    assert problem in explanation.note
    assert explanation.citizen.startswith("Your application for State Student Scholarship was not approved")


@pytest.mark.parametrize("error", [
    genai_errors.ClientError(429, {"error": {"code": 429, "message": "Quota exceeded.", "status": "RESOURCE_EXHAUSTED"}}),
    genai_errors.ServerError(503, {"error": {"code": 503, "message": "Overloaded.", "status": "UNAVAILABLE"}}),
], ids=["429", "503"])
def test_quota_and_overload_fall_back_after_the_usual_retries(seeded_client, schemes, caplog, error):
    fake = FakeGemini(replies={"text": error})
    ex = explainer(fake)
    with caplog.at_level(logging.WARNING, logger="verifyonce.extraction"):
        explanation = ex.explain(schemes[SCHOLARSHIP], "reject", below_marks(seeded_client, schemes),
                                 decided_by="evaluator")
    assert explanation.source == TEMPLATE
    assert "Gave up after 3 attempts" in explanation.note
    assert len(fake.calls) == 3 and ex.sleeps == [2.0, 5.0]


@pytest.mark.parametrize("reply", ["not json", json.dumps({"citizen_explanation": "x"})])
def test_malformed_replies_fall_back(seeded_client, schemes, reply):
    explanation = explainer(FakeGemini(replies={"text": reply})).explain(
        schemes[SCHOLARSHIP], "reject", below_marks(seeded_client, schemes), decided_by="evaluator")
    assert explanation.source == TEMPLATE


def test_missing_key_uses_the_template_without_a_call(seeded_client, schemes):
    ex = Explainer(enabled=True, provider=GeminiProvider(api_key=None))
    explanation = ex.explain(schemes[SCHOLARSHIP], "reject", below_marks(seeded_client, schemes), decided_by="evaluator")
    assert (explanation.source, explanation.note) == (TEMPLATE, "GEMINI_API_KEY is not set")


def test_validation_accepts_numbers_in_any_shown_form(seeded_client, schemes):
    facts = decision_facts(schemes[SCHOLARSHIP], "reject", below_marks(seeded_client, schemes), decided_by="evaluator")
    assert validate_text(GOOD["citizen_explanation"], facts) == []
    assert validate_text("Income 210000 INR is within 300000 INR.", facts) == []


def test_explanation_is_stored_on_the_decision_and_never_changes_the_verdict(settings, fake_gemini):
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.seed import seed_database

    lying = {"citizen_explanation": "Good news: you were approved.", "officer_brief": "Approved."}
    fake = FakeGemini(replies={"text": [json.dumps(GOOD), json.dumps(lying)]})
    with TestClient(create_app(settings, explainer=explainer(fake))) as client:
        with client.app.state.session_factory() as session:
            seed_database(session)
            session.commit()
        body = client.post("/profiles/seed-below-marks/applications", json={"scheme_id": SCHOLARSHIP}).json()
        assert body["verdict"] == "reject"
        assert (body["explanation"], body["explanation_source"], body["explanation_model"]) == (
            GOOD["citizen_explanation"], "generated", "gemini-test")
        assert body["officer_brief"] == GOOD["officer_brief"]

        # A reply claiming a different outcome changes nothing and is replaced by the template.
        other = client.post("/profiles/seed-reject-income/applications", json={"scheme_id": SCHOLARSHIP}).json()
        assert other["verdict"] == "reject"
        assert other["explanation_source"] == "template"
        assert "approved" not in other["explanation"].replace("not approved", "")


@pytest.mark.parametrize(("verdict", "text", "ok"), [
    ("approved", "Your application was approved.", True),
    ("approved", "Your application was not approved.", False),
    ("not approved", "Good news: you were approved.", False),
    ("not approved", "You are not eligible because your marks are too low.", True),
    ("sent to an officer for review", "An officer will review your documents.", True),
    ("sent to an officer for review", "You were approved.", False),
])
def test_text_must_state_the_recorded_outcome(verdict, text, ok):
    from backend.explanations import verdict_problem

    assert (verdict_problem(text, verdict) is None) is ok
