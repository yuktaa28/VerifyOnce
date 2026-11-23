"""Load the five synthetic seed profiles into the database.

    python scripts/seed.py           # add any seed profile that is missing
    python scripts/seed.py --reset   # delete the SQLite file first, then seed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.engine import make_url  # noqa: E402

from backend.config import Settings  # noqa: E402
from backend.db import init_db, make_engine, make_session_factory  # noqa: E402
from backend.seed import SEED_PROFILES, seed_database  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete the SQLite database file before seeding")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    url = make_url(settings.database_url)
    if args.reset:
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            parser.error("--reset only works with a SQLite database file")
        db_file = Path(url.database)
        if db_file.exists():
            db_file.unlink()
            print(f"Deleted {db_file}")

    engine = make_engine(settings.database_url)
    init_db(engine)
    with make_session_factory(engine)() as session:
        created = seed_database(session)
        session.commit()
    engine.dispose()

    print(f"Database: {url.database}")
    for seed in SEED_PROFILES:
        status = "added" if seed.id in created else "already present"
        print(f"  {seed.id:<22} {status:<16} {seed.label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
