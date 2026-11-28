import { useEffect } from "react";
import { Link } from "react-router-dom";

const STEPS = [
  {
    n: "1",
    title: "Upload once",
    body: "A citizen uploads an identity card, income certificate and marksheet. The image quality is measured, then a model reads each field into a fixed schema.",
  },
  {
    n: "2",
    title: "Verify the facts",
    body: "Validators, confidence checks and cross-document comparison decide which facts are verified. Anything uncertain is flagged, never guessed.",
  },
  {
    n: "3",
    title: "Reuse everywhere",
    body: "Every scheme checks the same verified facts against versioned rules. Rejected for one scheme? See what else you qualify for, with no re-upload.",
  },
];

const TRUST = [
  { title: "Rules decide, not AI", body: "Eligibility comes from deterministic rules in versioned files. The model only reads documents." },
  { title: "Officers decide uncertain cases", body: "Blurry scans, low confidence and conflicting documents go to an officer. Nothing automatic can close them." },
  { title: "Every step on the record", body: "Decisions are never edited, only superseded. Each one has an actor, a time and, for officers, a typed reason." },
  { title: "Honest about its sources", body: "Every fact says whether it was read live, replayed from a cache of an earlier real run, or loaded from seed fixtures." },
];

export function Landing() {
  useEffect(() => {
    document.title = "VerifyOnce · Verify once, reuse everywhere";
  }, []);

  return (
    <div className="space-y-14">
      <section className="grid items-center gap-8 lg:grid-cols-[1.2fr_1fr]">
        <div>
          <p className="text-sm font-semibold tracking-wide text-accent uppercase">AI for the government office</p>
          <h1 className="mt-2 text-4xl font-bold text-ink sm:text-5xl">Verify once, reuse everywhere</h1>
          <p className="mt-4 max-w-xl text-lg text-muted">
            Citizens prove the same facts again for every scheme. VerifyOnce verifies their documents once, lets clear rules decide
            eligibility, and sends only the uncertain cases to an officer.
          </p>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <Link to="/citizen" className="btn btn-primary px-6 text-base">
              Try as a citizen
            </Link>
            <Link to="/console" className="btn btn-secondary px-6 text-base">
              Officer console
            </Link>
          </div>
        </div>
        <figure className="card p-5" aria-labelledby="example-caption">
          <figcaption id="example-caption" className="text-xs font-semibold tracking-wide text-muted uppercase">Example outcomes from the synthetic demo</figcaption>
          <ul className="mt-3 space-y-3 text-[0.95rem]">
            <li className="flex items-center justify-between gap-3 rounded-lg border border-line p-3">
              <span>
                <strong>State Student Scholarship</strong>
                <span className="block text-sm text-muted">Marks 52% below the 60% minimum</span>
              </span>
              <span className="rounded-full border border-red-700/30 bg-red-50 px-2.5 py-0.5 text-sm font-semibold whitespace-nowrap text-red-900">
                ✕ Not eligible
              </span>
            </li>
            <li className="flex items-center justify-between gap-3 rounded-lg border border-line p-3">
              <span>
                <strong>Income-Based Fee Waiver</strong>
                <span className="block text-sm text-muted">Same documents, no re-upload</span>
              </span>
              <span className="rounded-full border border-green-700/30 bg-green-50 px-2.5 py-0.5 text-sm font-semibold whitespace-nowrap text-green-900">
                ✓ Approved
              </span>
            </li>
            <li className="flex items-center justify-between gap-3 rounded-lg border border-line p-3">
              <span>
                <strong>Blurry marksheet scan</strong>
                <span className="block text-sm text-muted">Sharpness 8.3, minimum 35</span>
              </span>
              <span className="rounded-full border border-amber-700/40 bg-amber-50 px-2.5 py-0.5 text-sm font-semibold whitespace-nowrap text-amber-950">
                ! Officer review
              </span>
            </li>
          </ul>
        </figure>
      </section>

      <section aria-labelledby="steps">
        <h2 id="steps" className="text-2xl font-bold text-ink">
          How it works in three steps
        </h2>
        <ol className="mt-5 grid gap-4 md:grid-cols-3">
          {STEPS.map((s) => (
            <li key={s.n} className="card p-5">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-accent text-lg font-bold text-white" aria-hidden="true">
                {s.n}
              </span>
              <h3 className="mt-3 text-lg font-bold">{s.title}</h3>
              <p className="mt-1 text-muted">{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="trust" className="rounded-2xl bg-accent-soft p-6 sm:p-8">
        <h2 id="trust" className="text-2xl font-bold text-ink">
          Why you can trust the outcome
        </h2>
        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          {TRUST.map((t) => (
            <div key={t.title}>
              <h3 className="font-bold text-accent-strong">{t.title}</h3>
              <p className="mt-1 text-ink">{t.body}</p>
            </div>
          ))}
        </div>
        <p className="mt-6">
          <Link to="/how-it-works" className="font-semibold text-accent underline underline-offset-4">
            See the architecture and the limitations
          </Link>
        </p>
      </section>
    </div>
  );
}
