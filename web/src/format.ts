// Labels and value formatting. Mirrors frontend/ui.py so both UIs say the same thing.

import type { Fact, ImageQuality, Verdict } from "./types";

export const FIELD_LABELS: Record<string, string> = {
  name: "Name",
  date_of_birth: "Date of birth",
  state: "State",
  annual_income_inr: "Annual income",
  marks_percent: "Marks",
  id_last4: "ID number",
};

export const DOCUMENT_LABELS: Record<string, string> = {
  identity_card: "Identity card",
  income_certificate: "Income certificate",
  marksheet: "Marksheet",
};

export const DOCUMENT_TYPES = ["identity_card", "income_certificate", "marksheet"] as const;

export const VERDICT_LABELS: Record<Verdict, string> = {
  approve: "Approved",
  reject: "Not eligible",
  needs_human_review: "Needs officer review",
};

export const FACT_STATUS_LABELS: Record<string, string> = {
  verified: "Verified",
  flagged: "Flagged",
  rejected: "Rejected by officer",
  unverified: "Unverified",
};

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function documentLabel(type: string | null | undefined): string {
  if (!type) return "Document";
  return DOCUMENT_LABELS[type] ?? type.replace(/_/g, " ");
}

export function verdictLabel(verdict: string | null | undefined): string {
  return (verdict && VERDICT_LABELS[verdict as Verdict]) || verdict || "—";
}

/** Indian digit grouping: 210000 -> ₹2,10,000. */
export function inr(amount: number): string {
  const digits = String(Math.trunc(amount));
  if (digits.length <= 3) return `₹${digits}`;
  let head = digits.slice(0, -3);
  const tail = digits.slice(-3);
  const groups: string[] = [];
  while (head.length > 2) {
    groups.unshift(head.slice(-2));
    head = head.slice(0, -2);
  }
  return "₹" + [...(head ? [head] : []), ...groups].join(",") + "," + tail;
}

/** Display a fact value. ID numbers are always masked. */
export function showValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "— unreadable —";
  if (field === "id_last4") return `XXXX XXXX ${String(value)}`;
  if (field === "annual_income_inr" && typeof value === "number") return inr(value);
  if (field === "marks_percent" && typeof value === "number") return `${Number(value.toFixed(2))}%`;
  return String(value);
}

export type SourceKind = "live" | "cache" | "seed" | "unknown";

/** Where a fact came from. Cached or seeded values are never labelled live. */
export function sourceOf(fact: Pick<Fact, "source" | "extraction_source" | "extraction_model">): {
  kind: SourceKind;
  label: string;
  title: string;
} {
  if (fact.source === "seed") {
    return { kind: "seed", label: "seed fixture · no model", title: "Values loaded from the seed fixtures. No model ran." };
  }
  const model = fact.extraction_model || "no model";
  if (fact.extraction_source === "live") {
    return { kind: "live", label: `live · ${model}`, title: `${model} read this image when it was uploaded.` };
  }
  if (fact.extraction_source === "cache") {
    return {
      kind: "cache",
      label: `cache · ${model}`,
      title: `${model} read this exact file in an earlier real run; the stored reply was replayed. No model ran for this upload.`,
    };
  }
  return { kind: "unknown", label: `${fact.extraction_source || "unknown"} · ${model}`, title: "Source not recorded." };
}

export function imageQualityLine(q: ImageQuality | null | undefined): string {
  if (!q) return "Image quality: not measured";
  if (q.sharpness === undefined || q.contrast === undefined) {
    return `Image quality: ${q.ok ? "ok" : "LOW QUALITY"} (seed label)`;
  }
  return (
    `Image quality: sharpness ${q.sharpness.toFixed(1)} (min ${q.min_sharpness}), ` +
    `contrast ${q.contrast.toFixed(1)} (min ${q.min_contrast}) → ` +
    (q.ok ? "ok" : "LOW QUALITY: blurry or low-contrast")
  );
}

/** API timestamps are UTC; some carry no zone marker. */
export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function actorLabel(actor: string): string {
  if (actor === "evaluator") return "Rules engine";
  if (actor.startsWith("officer:")) return `Officer ${actor.slice("officer:".length)}`;
  if (actor.startsWith("citizen:")) return `Citizen ${actor.slice("citizen:".length)}`;
  return actor;
}
