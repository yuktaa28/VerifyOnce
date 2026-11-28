"""Browser smoke test of the website: every page loads, then the reject-then-fee-waiver flow.

Opt-in and free: Playwright for Python driving the Edge (or Chrome) already installed on
Windows, so no browser download and no paid service.

    .venv\\Scripts\\python.exe -m pip install -r requirements-e2e.txt
    cd web; npm run build; cd ..
    .venv\\Scripts\\python.exe -m pytest -m e2e --run-e2e

The app runs in-process on a free port with a fresh demo database and no API key, so no
model is ever called.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest

from backend.app import create_app
from backend.config import REPO_ROOT, Settings
from backend.demo import reset_demo

pytestmark = pytest.mark.e2e

DIST = REPO_ROOT / "web" / "dist"
DEMO_NOTICE = "Synthetic data - demo only. Never upload real documents."
PAGES = {
    "/": "Verify once, reuse everywhere",
    "/citizen": "Your documents and schemes",
    "/console": "Review queue",
    "/console/case/1": "seed-name-mismatch · State Student Scholarship",
    "/how-it-works": "From one upload to every scheme",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site_url(tmp_path_factory) -> Iterator[str]:
    uvicorn = pytest.importorskip("uvicorn")
    if not (DIST / "index.html").is_file():
        pytest.skip("web/dist is not built: cd web; npm install; npm run build")
    tmp = tmp_path_factory.mktemp("site")
    settings = Settings(database_url=f"sqlite:///{(tmp / 'site.db').as_posix()}", cache_dir=tmp / "cache",
                        web_dist_dir=DIST)
    app = create_app(settings)
    reset_demo(app.state.engine, app.state.schemes, with_applications=True,
               confidence_cutoff=settings.confidence_cutoff)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            pytest.fail("the app did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="pip install -r requirements-e2e.txt")
    with sync_api.sync_playwright() as p:
        launched = None
        for channel in ("msedge", "chrome"):
            try:
                launched = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception:  # noqa: BLE001 - try the next installed browser
                continue
        if launched is None:
            pytest.skip("neither Microsoft Edge nor Google Chrome could be started")
        yield launched
        launched.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.errors = []  # type: ignore[attr-defined]
    page.on("pageerror", lambda exc: page.errors.append(str(exc)))  # type: ignore[attr-defined]
    page.on("console", lambda msg: msg.type == "error" and page.errors.append(msg.text))  # type: ignore[attr-defined]
    yield page
    context.close()


@pytest.mark.parametrize("path", list(PAGES))
def test_every_page_loads_with_the_demo_notice_and_system_status(site_url, page, path):
    from playwright.sync_api import expect

    page.goto(site_url + path)
    expect(page.get_by_role("heading", level=1)).to_have_text(PAGES[path])
    expect(page.get_by_role("note", name="Demo notice")).to_have_text("⚠ " + DEMO_NOTICE)
    expect(page.get_by_text("Up · database ok")).to_be_visible()
    assert page.errors == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("path", list(PAGES))
def test_pages_fit_a_phone_screen(site_url, browser, path):
    context = browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True)
    page = context.new_page()
    page.goto(site_url + path)
    page.get_by_text("Up · database ok").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    context.close()


def test_rejected_for_the_scholarship_then_approved_for_the_fee_waiver(site_url, page):
    from playwright.sync_api import expect

    page.goto(site_url + "/citizen")
    page.get_by_label("Profile", exact=True).select_option("seed-below-marks")
    expect(page).to_have_url(f"{site_url}/citizen?profile=seed-below-marks")
    expect(page.get_by_role("cell", name="Arjun Mehta").first).to_be_visible()
    # Seeded facts say so, and nothing is labelled live.
    expect(page.get_by_text("seed fixture · no model").first).to_be_visible()
    expect(page.get_by_text("live ·")).to_have_count(0)

    page.get_by_role("button", name="Apply for State Student Scholarship").click()
    expect(page.get_by_text("Not eligible for State Student Scholarship: marks percentage 52%")).to_be_visible()
    expect(page.get_by_role("cell", name="✕ fail")).to_be_visible()

    offer = page.get_by_role("region", name="You may qualify for")
    expect(offer).to_be_visible()
    offer.get_by_role("button", name="Apply for Income-Based Fee Waiver").click()
    expect(page.get_by_text("Approved for Income-Based Fee Waiver")).to_be_visible()
    expect(page.get_by_role("heading", name="Result: Income-Based Fee Waiver")).to_be_visible()
    assert page.errors == []  # type: ignore[attr-defined]
