import { Fragment, useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useSystem } from "../components/status";
import { PageTitle } from "../components/ui";

const PIPELINE = [
  { title: "Upload", body: "Citizen uploads a PNG or JPG (max 3 MB) once.", tag: "citizen" },
  { title: "Image-quality gate", body: "Sharpness and contrast measured from pixels. No AI.", tag: "measured" },
  { title: "Extraction", body: "The model reads fields into a fixed schema. Known demo files replay a cached real run.", tag: "model" },
  { title: "Verification", body: "Validators, confidence cutoff and cross-document comparison.", tag: "rules" },
  { title: "Verified facts", body: "Stored once with their source, then reused by every scheme.", tag: "store" },
];

const TAG_STYLE: Record<string, string> = {
  citizen: "bg-paper text-ink border-line",
  measured: "bg-paper text-ink border-line",
  model: "bg-accent text-white border-accent",
  rules: "bg-accent-soft text-accent-strong border-accent/40",
  store: "bg-accent-soft text-accent-strong border-accent/40",
};

const LIMITATIONS = [
  ["Synthetic data only.", "Every person, document and scheme here is fictional. Never upload real personal data or real identity documents."],
  ["Human review for uncertain cases.", "Missing, unreadable, low-confidence, blurry or conflicting evidence always goes to an officer. The system never rejects on evidence it does not trust."],
  ["Deterministic rules decide eligibility.", "The model only reads fields from documents. Rules in YAML files decide every verdict, and the optional explanations never change one."],
  ["Officer sign-off ends every review case.", "A case sent to review closes only with an officer's approve, reject or override, recorded with a typed reason in an append-only audit trail."],
  ["Image-quality thresholds are tuned on synthetic images.", "Real phone photos and scans may need the thresholds retuned."],
  ["Model confidence is not trusted on its own.", "In testing, the model gave the same 0.8 to correct and incorrect readings of a blurry scan. Deterministic checks decide what is flagged."],
  ["A free-tier model's availability and limits can change.", "Models get overloaded (503), quotas run out (429), and models are retired. The app retries, then sends documents to review. Demo documents can be served from a cache of real runs."],
  ["Hidden instructions in documents.", "The prompt ignores them and the schema refuses extra fields. A model that obeyed an instruction while keeping to the schema would not be caught by the schema alone."],
  ["No authentication.", "Officer identity is a typed name. On the public demo, officer actions need a demo PIN. This is a demo, not a production system."],
];

function Arrow({ vertical }: { vertical?: boolean }) {
  return (
    <span aria-hidden="true" className={vertical ? "block text-center text-2xl leading-none text-muted" : "text-2xl text-muted"}>
      {vertical ? "↓" : "→"}
    </span>
  );
}

export function HowItWorks() {
  const { status } = useSystem();
  const { hash } = useLocation();

  useEffect(() => {
    document.title = "How it works · VerifyOnce";
  }, []);
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [hash]);

  return (
    <div className="space-y-10">
      <PageTitle eyebrow="How it works" title="From one upload to every scheme">
        The model reads documents. Measurements, validators and versioned rules decide. Officers decide whatever is uncertain.
      </PageTitle>

      <section aria-labelledby="architecture" className="card p-4 sm:p-6">
        <h2 id="architecture" className="text-xl font-bold">
          Architecture
        </h2>
        <p className="mt-1 text-sm text-muted">One FastAPI process serves this website and the API under /api, backed by SQLite.</p>

        <figure className="mt-5" aria-labelledby="diagram-caption">
          <ol className="grid gap-2 lg:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr_auto_1fr] lg:items-stretch">
            {PIPELINE.map((stage, i) => (
              <Fragment key={stage.title}>
                <li className="flex flex-col rounded-lg border border-line bg-white p-3">
                  <span className={`self-start rounded border px-1.5 py-0.5 text-[0.7rem] font-bold tracking-wide uppercase ${TAG_STYLE[stage.tag]}`}>
                    {stage.tag}
                  </span>
                  <span className="mt-2 font-bold">
                    {i + 1}. {stage.title}
                  </span>
                  <span className="mt-1 text-sm text-muted">{stage.body}</span>
                </li>
                {i < PIPELINE.length - 1 ? (
                  <li aria-hidden="true" className="flex items-center justify-center">
                    <span className="hidden lg:inline">
                      <Arrow />
                    </span>
                    <span className="lg:hidden">
                      <Arrow vertical />
                    </span>
                  </li>
                ) : null}
              </Fragment>
            ))}
          </ol>

          <div className="my-3">
            <Arrow vertical />
          </div>

          <div className="rounded-lg border-2 border-accent/40 bg-accent-soft p-4 text-center">
            <p className="font-bold text-accent-strong">6. Deterministic rules</p>
            <p className="text-sm text-ink">Each scheme is a versioned YAML ruleset. Precedence: needs review, then reject, then approve.</p>
          </div>

          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border border-line bg-white p-4">
              <Arrow vertical />
              <p className="mt-1 font-bold">Clear evidence: decided automatically</p>
              <p className="text-sm text-muted">Approved or not eligible, with every rule, value and source document shown. “You may qualify for” suggests other schemes.</p>
            </div>
            <div className="rounded-lg border border-amber-600/50 bg-amber-50 p-4">
              <Arrow vertical />
              <p className="mt-1 font-bold text-amber-950">Uncertain evidence: officer review</p>
              <p className="text-sm text-amber-950">
                Conflicts, blurry scans or low confidence. The officer resolves evidence and approves, rejects or overrides, always with a
                typed reason.
              </p>
            </div>
          </div>

          <div className="mt-3 rounded-lg border border-dashed border-[#6b7280] bg-paper p-3 text-center text-sm">
            <strong>Append-only record:</strong> decisions are never edited, only superseded, and every step is an audit event with actor and
            time.
          </div>
          <figcaption id="diagram-caption" className="sr-only">
            Pipeline: upload, image-quality gate, extraction, verification, verified facts, deterministic rules, then either an automatic
            decision or officer review, all recorded in an append-only audit log.
          </figcaption>
        </figure>
      </section>

      <section aria-labelledby="sources" className="grid gap-4 md:grid-cols-3">
        <h2 id="sources" className="sr-only">
          Where facts come from
        </h2>
        <div className="card p-4">
          <span className="inline-block rounded-md border border-accent bg-accent px-2 py-0.5 font-mono text-xs text-white">live · model</span>
          <p className="mt-2 text-sm">The model read this image at upload time.</p>
        </div>
        <div className="card p-4">
          <span className="inline-block rounded-md border border-accent/50 bg-accent-soft px-2 py-0.5 font-mono text-xs text-accent-strong">cache · model</span>
          <p className="mt-2 text-sm">The model read this exact file in an earlier real run. The stored reply was replayed; no model ran now.</p>
        </div>
        <div className="card p-4">
          <span className="inline-block rounded-md border border-dashed border-[#6b7280] bg-white px-2 py-0.5 font-mono text-xs text-muted">
            seed fixture · no model
          </span>
          <p className="mt-2 text-sm">Values loaded from the seed fixtures. No model ran.</p>
        </div>
      </section>

      <section id="limitations" aria-labelledby="limitations-heading" className="card scroll-mt-16 p-4 sm:p-6">
        <h2 id="limitations-heading" className="text-xl font-bold">
          Limitations and responsible use
        </h2>
        <ul className="mt-4 space-y-3">
          {LIMITATIONS.map(([title, body]) => (
            <li key={title} className="border-l-4 border-accent/40 pl-3">
              <strong>{title}</strong> <span className="text-muted">{body}</span>
            </li>
          ))}
        </ul>
        {status ? (
          <p className="mt-5 text-sm text-muted">
            Right now: extraction by {status.provider} · {status.model}, mode {status.extraction_mode}, confidence cutoff{" "}
            {status.confidence_cutoff.toFixed(2)}.
          </p>
        ) : null}
      </section>
    </div>
  );
}
