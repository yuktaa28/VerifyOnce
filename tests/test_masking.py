"""Full ID numbers never reach the database or the API."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy.engine import make_url

from backend.fields import FieldValueError, coerce_value, mask_id
from backend.models import Document, Fact, Profile
from backend.service import add_document, add_fact, create_profile

FULL_ID = "8765 4321 9012"


@pytest.mark.parametrize(
    ("raw", "last4"),
    [
        (FULL_ID, "9012"),
        ("XXXX-XXXX-2051", "2051"),
        ("876543219012", "9012"),
        (876543219012, "9012"),
        ("2051", "2051"),
    ],
)
def test_mask_id_keeps_only_last_four_digits(raw, last4):
    assert mask_id(raw) == last4
    assert coerce_value("id_last4", raw) == last4


def test_mask_id_refuses_too_few_digits():
    with pytest.raises(FieldValueError):
        mask_id("XX12")


def test_full_id_is_never_written_to_the_database(bare_session, settings):
    profile = create_profile(bare_session, "p1", actor="test")
    document = add_document(bare_session, profile, "identity_card", actor="test")
    fact = add_fact(bare_session, document, "id_last4", FULL_ID, 0.95, actor="test")
    bare_session.commit()
    assert fact.value_json == "9012"

    db_file = make_url(settings.database_url).database
    with closing(sqlite3.connect(db_file)) as raw:
        dump = "\n".join(raw.iterdump())
    assert "9012" in dump
    for leaked in (FULL_ID, FULL_ID.replace(" ", ""), "87654321"):
        assert leaked not in dump


def test_model_guard_blocks_a_full_id_written_directly(bare_session):
    bare_session.add(Profile(id="p2"))
    document = Document(profile_id="p2", document_type="identity_card")
    bare_session.add(document)
    bare_session.flush()
    bare_session.add(
        Fact(document_id=document.id, field="id_last4", value_type="str", value_json="876543219012", confidence=0.9)
    )
    with pytest.raises(ValueError, match="exactly 4 digits"):
        bare_session.flush()


def test_api_only_exposes_last_four_digits(seeded_client):
    profile = seeded_client.get("/profiles/seed-approve").json()
    ids = [f["value"] for d in profile["documents"] for f in d["facts"] if f["field"] == "id_last4"]
    assert ids == ["2051"]

    decision = seeded_client.post(
        "/profiles/seed-approve/applications", json={"scheme_id": "state_student_scholarship"}
    ).json()
    assert [f["value"] for f in decision["source_facts"] if f["field"] == "id_last4"] == ["2051"]
    assert "XXXX" not in str(decision)
