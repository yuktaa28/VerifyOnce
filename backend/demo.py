"""Demo-day helpers: reset the demo database to a known state.

The reset drops and recreates every table (it does not delete the file, so it also
works while the API is running), loads the five seed profiles, and optionally
pre-creates the two applications that should be waiting in the officer queue.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, func, select

from backend.db import Base, init_db, make_session_factory
from backend.models import Application, AuditEvent, Decision, Document, Fact, Profile
from backend.schemes import Scheme
from backend.seed import SCHOLARSHIP, seed_database
from backend.service import application_status, latest_decision, submit_application

# Pre-created so the officer queue shows two cases awaiting review at startup.
DEMO_APPLICATIONS = (("seed-name-mismatch", SCHOLARSHIP), ("seed-low-confidence", SCHOLARSHIP))
# Left without applications: the live "rejected, then approved for the fee waiver" moment.
FIXED_PROFILE = "seed-below-marks"


def reset_demo(engine: Engine, schemes: dict[str, Scheme], *, with_applications: bool, confidence_cutoff: float,
               explainer: Any = None) -> dict[str, Any]:
    """Wipe and rebuild the demo database. Returns a summary of what is in it."""
    from backend import models  # noqa: F401  (registers the tables)

    Base.metadata.drop_all(engine)
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        seed_database(session, confidence_cutoff=confidence_cutoff)
        if with_applications:
            for profile_id, scheme_id in DEMO_APPLICATIONS:
                submit_application(session, schemes, profile_id, scheme_id, actor="demo-reset",
                                   confidence_cutoff=confidence_cutoff, explainer=explainer)
        session.commit()
        return demo_summary(session, schemes)


def demo_summary(session, schemes: dict[str, Scheme]) -> dict[str, Any]:
    def count(model) -> int:
        return session.scalar(select(func.count()).select_from(model)) or 0

    applications = []
    for application in session.scalars(select(Application).order_by(Application.id)):
        decision = latest_decision(session, application.id)
        applications.append({
            "application_id": application.id,
            "profile_id": application.profile_id,
            "scheme_id": application.scheme_id,
            "verdict": decision.verdict if decision else None,
            "status": application_status(decision) if decision else None,
        })
    return {
        "profiles": sorted(session.scalars(select(Profile.id))),
        "documents": count(Document),
        "facts": count(Fact),
        "decisions": count(Decision),
        "audit_events": count(AuditEvent),
        "applications": applications,
    }
