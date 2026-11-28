import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { cx } from "./ui";
import { useSystem } from "./status";

export const DEMO_NOTICE = "Synthetic data - demo only. Never upload real documents.";

const NAV = [
  { to: "/citizen", label: "Citizen" },
  { to: "/console", label: "Officer console" },
  { to: "/how-it-works", label: "How it works" },
];

function DemoStrip() {
  return (
    <div className="sticky top-0 z-40 border-b border-amber-400 bg-amber-100 text-amber-950" role="note" aria-label="Demo notice">
      <p className="mx-auto max-w-6xl px-4 py-1.5 text-center text-sm font-semibold">
        <span aria-hidden="true">⚠ </span>
        {DEMO_NOTICE}
      </p>
    </div>
  );
}

function Header() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    cx(
      "block rounded-md px-3 py-2 text-[0.95rem] font-semibold",
      isActive ? "bg-accent-soft text-accent-strong" : "text-ink hover:bg-paper",
    );

  return (
    <header className="border-b border-line bg-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3">
        <Link to="/" className="flex items-center gap-2 rounded-md text-lg font-bold text-ink">
          <svg viewBox="0 0 32 32" className="h-8 w-8" aria-hidden="true">
            <rect width="32" height="32" rx="7" fill="#1d4f91" />
            <path d="M9 16.5l4.5 4.5L23 11" fill="none" stroke="#fff" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          VerifyOnce
        </Link>
        <button
          type="button"
          className="btn btn-quiet px-3 md:hidden"
          aria-expanded={open}
          aria-controls="main-nav"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Close menu" : "Menu"}
        </button>
        <nav id="main-nav" aria-label="Main" className={cx("w-full md:block md:w-auto", open ? "block" : "hidden")}>
          <ul className="flex flex-col gap-1 pt-2 md:flex-row md:pt-0">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} className={linkClass}>
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}

function StatusItem({ label, value, tone }: { label: string; value: string; tone?: "ok" | "warn" | "bad" }) {
  const dot = tone === "bad" ? "bg-red-700" : tone === "warn" ? "bg-amber-600" : "bg-green-700";
  return (
    <div className="min-w-0">
      <dt className="text-xs font-semibold tracking-wide text-muted uppercase">{label}</dt>
      <dd className="break-words text-sm text-ink">
        {tone ? <span className={cx("mr-1.5 inline-block h-2 w-2 rounded-full align-middle", dot)} aria-hidden="true" /> : null}
        {value}
      </dd>
    </div>
  );
}

function SystemStatus() {
  const { status, health, reachable } = useSystem();
  return (
    <section aria-labelledby="system-status" className="mx-auto max-w-6xl px-4 py-6">
      <h2 id="system-status" className="text-sm font-bold text-ink">
        System status
      </h2>
      {reachable === false ? (
        <p className="mt-2 text-sm text-red-900" role="status">
          <span aria-hidden="true">● </span>API unreachable. Start the server, then reload this page.
        </p>
      ) : !status ? (
        <p className="mt-2 text-sm text-muted">Checking…</p>
      ) : (
        <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatusItem
            label="API and database"
            value={health?.status === "ok" ? "Up · database ok" : `Up · ${health?.database ?? "database unknown"}`}
            tone={health?.status === "ok" ? "ok" : "warn"}
          />
          <StatusItem label="Extraction model" value={`${status.provider} · ${status.model}`} />
          <StatusItem
            label="Extraction mode"
            value={
              status.extraction_mode === "live"
                ? "Live: uploads call the model"
                : "Cache first: known files replay an earlier real run"
            }
            tone={status.extraction_mode === "live" && !status.provider_key_set ? "warn" : undefined}
          />
          <StatusItem
            label="Checks"
            value={`Confidence ≥ ${status.confidence_cutoff.toFixed(2)} · sharpness ≥ ${status.image_quality_thresholds.min_sharpness} · contrast ≥ ${status.image_quality_thresholds.min_contrast}`}
          />
          {status.extraction_mode === "live" && !status.provider_key_set ? (
            <StatusItem label="API key" value="Not set: uploads go to officer review" tone="warn" />
          ) : null}
          <StatusItem
            label="Public demo"
            value={
              status.public_demo
                ? `On · sample documents only · officer actions ${status.officer_actions_enabled ? "need the demo PIN" : "off"}`
                : "Off · local use"
            }
          />
          <StatusItem label="Explanations" value={status.explanations_enabled ? "On (never change a verdict)" : "Off · rules text only"} />
          <StatusItem label="Uploads" value={`${status.upload_limits.types.map((t) => t.toUpperCase()).join(" or ")}, up to ${status.upload_limits.max_mb} MB`} />
        </dl>
      )}
    </section>
  );
}

export function Layout() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-white px-3 py-2 font-semibold text-accent focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to main content
      </a>
      <DemoStrip />
      <Header />
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 outline-none">
        <Outlet />
      </main>
      <footer className="border-t border-line bg-white">
        <SystemStatus />
        <div className="border-t border-line">
          <p className="mx-auto max-w-6xl px-4 py-4 text-sm text-muted">
            VerifyOnce is a hackathon demo. Every person, document and scheme here is fictional. Rules decide eligibility; officers
            decide every uncertain case. <Link to="/how-it-works#limitations" className="font-semibold text-accent underline">Limitations</Link>
          </p>
        </div>
      </footer>
    </div>
  );
}
