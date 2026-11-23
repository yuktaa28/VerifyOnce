"""Five reproducible synthetic profiles covering each evaluator path.

This file is the single source of truth for the demo data. ``scripts/make_documents.py``
renders each SeedDocument as an image with exactly these values, so uploading the
images (and extracting them correctly) gives the same verdicts as seeding directly.

All people, numbers and documents here are invented. ID numbers are written the way
a masked card prints them ("XXXX XXXX 2051"); only the last four digits are stored.
Each document carries exactly the fields extraction reads for its type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.config import DEFAULT_CONFIDENCE_CUTOFF
from backend.models import Profile
from backend.service import add_document, add_fact, create_profile
from backend.verification import verify_profile

SEED_ACTOR = "seed"
SCHOLARSHIP = "state_student_scholarship"
FEE_WAIVER = "income_based_fee_waiver"


@dataclass(frozen=True)
class SeedDocument:
    document_type: str
    facts: dict[str, tuple[Any, float]]  # field -> (raw value as printed, confidence)
    quality: str = "clean"  # "clean" or "blurry" (how make_documents.py renders it)


@dataclass(frozen=True)
class SeedProfile:
    id: str
    label: str
    documents: tuple[SeedDocument, ...]
    expected: dict[str, str]  # scheme id -> expected verdict


SEED_PROFILES: tuple[SeedProfile, ...] = (
    SeedProfile(
        id="seed-approve",
        label="Approval: every rule passes; name initials differ but match",
        documents=(
            SeedDocument("identity_card", {
                "name": ("Ravi K. Patil", 0.98),
                "date_of_birth": ("2006-03-14", 0.97),
                "state": ("Karnataka", 0.96),
                "id_last4": ("XXXX XXXX 2051", 0.95),
            }),
            SeedDocument("income_certificate", {
                "name": ("Ravi Patil", 0.95),
                "state": ("Karnataka", 0.94),
                "annual_income_inr": ("2,40,000", 0.93),
            }),
            SeedDocument("marksheet", {
                "name": ("RAVI K PATIL", 0.96),
                "date_of_birth": ("2006-03-14", 0.95),
                "marks_percent": ("78.5%", 0.97),
            }),
        ),
        expected={SCHOLARSHIP: "approve", FEE_WAIVER: "approve"},
    ),
    SeedProfile(
        id="seed-reject-income",
        label="Rejection: income above both limits",
        documents=(
            SeedDocument("identity_card", {
                "name": ("Meera Iyer", 0.97),
                "date_of_birth": ("2005-11-02", 0.96),
                "state": ("Tamil Nadu", 0.96),
                "id_last4": ("XXXX XXXX 7734", 0.95),
            }),
            SeedDocument("income_certificate", {
                "name": ("Meera Iyer", 0.95),
                "state": ("Tamil Nadu", 0.94),
                "annual_income_inr": ("5,20,000", 0.95),
            }),
            SeedDocument("marksheet", {
                "name": ("Meera Iyer", 0.96),
                "date_of_birth": ("2005-11-02", 0.95),
                "marks_percent": ("88%", 0.97),
            }),
        ),
        expected={SCHOLARSHIP: "reject", FEE_WAIVER: "reject"},
    ),
    SeedProfile(
        id="seed-below-marks",
        label="Rejection: marks below 60% (fails scholarship, passes fee waiver)",
        documents=(
            SeedDocument("identity_card", {
                "name": ("Arjun Mehta", 0.97),
                "date_of_birth": ("2006-07-21", 0.96),
                "state": ("Maharashtra", 0.95),
                "id_last4": ("XXXX XXXX 3390", 0.96),
            }),
            SeedDocument("income_certificate", {
                "name": ("Arjun Mehta", 0.94),
                "state": ("Maharashtra", 0.93),
                "annual_income_inr": ("2,10,000", 0.94),
            }),
            SeedDocument("marksheet", {
                "name": ("Arjun Mehta", 0.96),
                "date_of_birth": ("2006-07-21", 0.95),
                "marks_percent": ("52%", 0.97),
            }),
        ),
        expected={SCHOLARSHIP: "reject", FEE_WAIVER: "approve"},
    ),
    SeedProfile(
        id="seed-name-mismatch",
        label="Review: income certificate spells the name differently (Ishan Rau vs Ishan Rao)",
        documents=(
            SeedDocument("identity_card", {
                "name": ("Ishan Rao", 0.97),
                "date_of_birth": ("2006-01-09", 0.96),
                "state": ("Telangana", 0.95),
                "id_last4": ("XXXX XXXX 6612", 0.95),
            }),
            SeedDocument("income_certificate", {
                "name": ("Ishan Rau", 0.93),
                "state": ("Telangana", 0.93),
                "annual_income_inr": ("2,60,000", 0.94),
            }),
            SeedDocument("marksheet", {
                "name": ("Ishan Rao", 0.95),
                "date_of_birth": ("2006-01-09", 0.95),
                "marks_percent": ("72%", 0.96),
            }),
        ),
        expected={SCHOLARSHIP: "needs_human_review", FEE_WAIVER: "needs_human_review"},
    ),
    SeedProfile(
        id="seed-low-confidence",
        label="Review: marksheet is a blurry, low-contrast scan",
        documents=(
            SeedDocument("identity_card", {
                "name": ("Kavya Nair", 0.97),
                "date_of_birth": ("2006-05-30", 0.96),
                "state": ("Kerala", 0.95),
                "id_last4": ("XXXX XXXX 4408", 0.95),
            }),
            SeedDocument("income_certificate", {
                "name": ("Kavya Nair", 0.95),
                "state": ("Kerala", 0.94),
                "annual_income_inr": ("2,80,000", 0.93),
            }),
            SeedDocument(
                "marksheet",
                {
                    "name": ("Kavya Nair", 0.64),
                    "date_of_birth": ("2006-05-30", 0.58),
                    "marks_percent": ("81%", 0.62),
                },
                quality="blurry",
            ),
        ),
        expected={SCHOLARSHIP: "needs_human_review", FEE_WAIVER: "approve"},
    ),
)


def seed_database(session: Session, *, confidence_cutoff: float = DEFAULT_CONFIDENCE_CUTOFF) -> list[str]:
    """Insert any seed profile that is not in the database yet, then verify its facts.

    Returns the ids created.
    """
    created = []
    for seed in SEED_PROFILES:
        if session.get(Profile, seed.id) is not None:
            continue
        profile = create_profile(session, seed.id, label=seed.label, actor=SEED_ACTOR)
        for doc in seed.documents:
            document = add_document(session, profile, doc.document_type, actor=SEED_ACTOR, source="seed")
            for field, (raw, confidence) in doc.facts.items():
                add_fact(session, document, field, raw, confidence, actor=SEED_ACTOR)
        verify_profile(session, profile.id, confidence_cutoff=confidence_cutoff, actor=SEED_ACTOR)
        created.append(seed.id)
    session.flush()
    return created
