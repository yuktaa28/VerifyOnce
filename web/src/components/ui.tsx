import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api";
import { FACT_STATUS_LABELS, sourceOf, verdictLabel } from "../format";
import type { AppStatus, Fact, Verdict } from "../types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// --- Data loading -------------------------------------------------------------------

export function useAsync<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const counter = useRef(0);

  const reload = useCallback(async () => {
    const run = ++counter.current;
    setLoading(true);
    try {
      const value = await load();
      if (run === counter.current) {
        setData(value);
        setError(null);
      }
    } catch (exc) {
      if (run === counter.current) setError(errorText(exc));
    } finally {
      if (run === counter.current) setLoading(false);
    }
  }, deps);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, loading, reload, setData };
}

export function errorText(exc: unknown): string {
  if (exc instanceof ApiError) return exc.message;
  return "Something went wrong in the page. Reload and try again.";
}

// --- Badges -------------------------------------------------------------------------

const VERDICT_STYLE: Record<Verdict, string> = {
  approve: "border-green-700/30 bg-green-50 text-green-900",
  reject: "border-red-700/30 bg-red-50 text-red-900",
  needs_human_review: "border-amber-700/40 bg-amber-50 text-amber-950",
};
const VERDICT_ICON: Record<Verdict, string> = { approve: "✓", reject: "✕", needs_human_review: "!" };

export function VerdictBadge({ verdict, prefix }: { verdict: Verdict | null | undefined; prefix?: string }) {
  if (!verdict) return <span className="text-muted">—</span>;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-sm font-semibold whitespace-nowrap",
        VERDICT_STYLE[verdict],
      )}
    >
      <span aria-hidden="true">{VERDICT_ICON[verdict]}</span>
      {prefix ? `${prefix} ` : ""}
      {verdictLabel(verdict)}
    </span>
  );
}

const FACT_STYLE: Record<string, string> = {
  verified: "bg-green-50 text-green-900 border-green-700/30",
  flagged: "bg-amber-50 text-amber-950 border-amber-700/40",
  rejected: "bg-red-50 text-red-900 border-red-700/30",
  unverified: "bg-paper text-muted border-line",
};

export function FactStatusBadge({ status, reviewedBy }: { status: string; reviewedBy?: string | null }) {
  return (
    <span className={cx("inline-block rounded-md border px-2 py-0.5 text-xs font-semibold whitespace-nowrap", FACT_STYLE[status])}>
      {FACT_STATUS_LABELS[status] ?? status}
      {reviewedBy ? <span className="font-normal"> · {reviewedBy}</span> : null}
    </span>
  );
}

const SOURCE_STYLE = {
  live: "border-accent bg-accent text-white",
  cache: "border-accent/50 bg-accent-soft text-accent-strong",
  seed: "border-dashed border-[#6b7280] bg-white text-muted",
  unknown: "border-line bg-paper text-muted",
};

/** The live / cache / seed-fixture badge, with the model name. */
export function SourceBadge({ fact }: { fact: Pick<Fact, "source" | "extraction_source" | "extraction_model"> }) {
  const s = sourceOf(fact);
  return (
    <span
      title={s.title}
      className={cx("inline-block rounded-md border px-2 py-0.5 font-mono text-xs whitespace-nowrap", SOURCE_STYLE[s.kind])}
    >
      {s.label}
      <span className="sr-only">. {s.title}</span>
    </span>
  );
}

const APP_STATUS_STYLE: Record<string, string> = {
  "awaiting officer decision": "bg-amber-100 text-amber-950 border-amber-700/40",
  "decided by officer": "bg-accent-soft text-accent-strong border-accent/40",
  "decided automatically": "bg-paper text-ink border-line",
};

export function AppStatusBadge({ status }: { status: AppStatus | string }) {
  return (
    <span className={cx("inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap", APP_STATUS_STYLE[status])}>
      {status}
    </span>
  );
}

// --- Feedback -----------------------------------------------------------------------

type Tone = "info" | "success" | "warning" | "error";
const TONE: Record<Tone, string> = {
  info: "border-accent/30 bg-accent-soft text-ink",
  success: "border-green-700/30 bg-green-50 text-green-950",
  warning: "border-amber-700/40 bg-amber-50 text-amber-950",
  error: "border-red-700/40 bg-red-50 text-red-950",
};

export function Alert({ tone = "info", title, children, role }: { tone?: Tone; title?: ReactNode; children?: ReactNode; role?: string }) {
  return (
    <div className={cx("rounded-lg border px-4 py-3 text-[0.95rem]", TONE[tone])} role={role ?? (tone === "error" ? "alert" : undefined)}>
      {title ? <p className="font-semibold">{title}</p> : null}
      {children ? <div className={title ? "mt-1" : undefined}>{children}</div> : null}
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="flex items-center gap-2 text-muted" role="status">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" aria-hidden="true" />
      {label}
    </p>
  );
}

export function Loadable({ loading, error, children, label }: { loading: boolean; error: string | null; children: ReactNode; label?: string }) {
  if (error) return <Alert tone="error" title="Could not load this">{error}</Alert>;
  if (loading) return <Spinner label={label} />;
  return <>{children}</>;
}

export function PageTitle({ eyebrow, title, children }: { eyebrow?: string; title: string; children?: ReactNode }) {
  return (
    <header className="mb-6">
      {eyebrow ? <p className="text-sm font-semibold tracking-wide text-accent uppercase">{eyebrow}</p> : null}
      <h1 className="mt-1 text-2xl font-bold text-ink sm:text-3xl">{title}</h1>
      {children ? <div className="mt-2 max-w-3xl text-muted">{children}</div> : null}
    </header>
  );
}

export function Section({ title, id, children, aside }: { title: string; id?: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="card p-4 sm:p-6" aria-labelledby={id}>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={id} className="text-lg font-bold text-ink sm:text-xl">
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}
