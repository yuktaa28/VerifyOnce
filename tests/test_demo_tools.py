"""Demo reset, the YAML-only third scheme, and all five cases end to end."""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import REPO_ROOT
from backend.db import make_engine
from backend.demo import reset_demo
from backend.evaluator import evaluate
from backend.schemes import load_schemes
from backend.seed import FEE_WAIVER, SCHOLARSHIP, SEED_PROFILES, seed_database
from backend.service import profile_observations

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import demo_reset  # noqa: E402

MERIT = "merit_book_grant"


def reset(settings, schemes, *, with_applications: bool) -> dict:
    engine = make_engine(settings.database_url)
    try:
        return reset_demo(engine, schemes, with_applications=with_applications, confidence_cutoff=0.85)
    finally:
        engine.dispose()


# --- Demo reset -------------------------------------------------------------------------------


def test_reset_is_idempotent(settings, schemes):
    first = reset(settings, schemes, with_applications=True)
    second = reset(settings, schemes, with_applications=True)
    assert first == second
    assert first["profiles"] == sorted(s.id for s in SEED_PROFILES)


def test_with_applications_puts_two_cases_in_the_officer_queue(settings, schemes):
    summary = reset(settings, schemes, with_applications=True)
    assert [(a["profile_id"], a["scheme_id"], a["status"]) for a in summary["applications"]] == [
        ("seed-name-mismatch", SCHOLARSHIP, "awaiting officer decision"),
        ("seed-low-confidence", SCHOLARSHIP, "awaiting officer decision"),
    ]
    with TestClient(create_app(settings)) as client:
        queue = client.get("/officer/queue").json()
        assert [r["status"] for r in queue] == ["awaiting officer decision"] * 2
        assert client.get("/profiles/seed-below-marks/applications").json() == []  # kept for the live moment


def test_clean_leaves_no_applications(settings, schemes):
    reset(settings, schemes, with_applications=True)
    summary = reset(settings, schemes, with_applications=False)
    assert summary["applications"] == [] and summary["decisions"] == 0
    assert len(summary["profiles"]) == 5


def test_reset_script_flags_and_next_steps(settings, capsys):
    assert demo_reset.main([], settings=settings) == 0
    out = capsys.readouterr().out
    assert "with applications" in out and "awaiting officer decision" in out
    assert "seed-below-marks: no applications" in out
    assert "uvicorn backend.main:app --port 8000" in out and "streamlit run frontend\\app.py" in out
    assert "scripts\\preflight.py" in out

    assert demo_reset.main(["--clean"], settings=settings) == 0
    assert "applications: none" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        demo_reset.main(["--clean", "--with-applications"], settings=settings)


# --- A third scheme from YAML only ------------------------------------------------------------


def test_merit_book_grant_loads_from_yaml_alone(schemes):
    merit = schemes[MERIT]
    assert merit.name == "Merit Book Grant"
    assert {(r.id, r.op, r.value) for r in merit.rules} == {("minimum_marks", "gte", 75), ("income_limit", "lte", 500000)}
    assert (REPO_ROOT / "schemes" / f"{MERIT}.yaml").is_file()


@pytest.mark.parametrize(("profile_id", "verdict"), [
    ("seed-approve", "approve"),             # marks 78.5, income 2,40,000
    ("seed-reject-income", "reject"),        # income 5,20,000 > 5,00,000
    ("seed-below-marks", "reject"),          # marks 52 < 75
    ("seed-name-mismatch", "needs_human_review"),   # name conflict wins over failing marks
    ("seed-low-confidence", "needs_human_review"),  # marks only from the blurry scan
])
def test_merit_book_grant_decides_the_seed_profiles(bare_session, schemes, profile_id, verdict):
    seed_database(bare_session)
    result = evaluate(schemes[MERIT], profile_observations(bare_session, profile_id), 0.85)
    assert result.verdict == verdict


def test_merit_book_grant_through_the_api(seeded_client):
    body = seeded_client.post("/profiles/seed-approve/applications", json={"scheme_id": MERIT}).json()
    assert body["verdict"] == "approve" and body["ruleset_version"] == "2026.1"
    assert {o["rule_id"]: o["outcome"] for o in body["rule_outcomes"]} == {"minimum_marks": "pass", "income_limit": "pass"}


def test_adding_the_scheme_does_not_change_the_existing_offers(seeded_client):
    rejected = seeded_client.post("/profiles/seed-reject-income/applications", json={"scheme_id": SCHOLARSHIP}).json()
    assert rejected["possible_schemes"] == []
    below = seeded_client.post("/profiles/seed-below-marks/applications", json={"scheme_id": SCHOLARSHIP}).json()
    assert [s["scheme_id"] for s in below["possible_schemes"]] == [FEE_WAIVER]


# --- All five cases end to end ----------------------------------------------------------------


def test_all_five_cases_end_to_end(settings, schemes):
    reset(settings, schemes, with_applications=False)
    with TestClient(create_app(settings)) as client:
        for seed in SEED_PROFILES:
            for scheme_id, expected in seed.expected.items():
                response = client.post(f"/profiles/{seed.id}/applications", json={"scheme_id": scheme_id})
                assert response.status_code == 201
                assert response.json()["verdict"] == expected, (seed.id, scheme_id)

        # Every review case is closed by an explicit officer decision with a typed reason.
        queue = client.get("/officer/queue").json()
        awaiting = [r for r in queue if r["status"] == "awaiting officer decision"]
        assert len(awaiting) == 3
        for row in awaiting:
            response = client.post(f"/officer/cases/{row['application_id']}/decision", json={
                "action": "reject", "reason": "Could not verify the documents", "officer": "Asha"})
            assert response.status_code == 201
        statuses = {r["status"] for r in client.get("/officer/queue").json()}
        assert statuses == {"decided automatically", "decided by officer"}
