import { useEffect, useId, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { documentLabel, fieldLabel, showValue, verdictLabel } from "../format";
import type { Audit, CaseDetail, Fact, Mismatch } from "../types";
import { OfficerIdentity, useOfficerName } from "../components/officer";
import { AuditTimeline, BackLink, DecisionCard, FactsTable, ImageQualityTable } from "../components/tables";
import { Alert, AppStatusBadge, Loadable, Section, SourceBadge, VerdictBadge, cx, errorText, useAsync } from "../components/ui";

const MIN_REASON = 5;

export function CaseView() {
  const id = Number(useParams().id);
  const valid = Number.isInteger(id) && id > 0;
  const detail = useAsync<CaseDetail>(() => (valid ? api.caseDetail(id) : Promise.reject(new Error())), [id]);
  const audit = useAsync<Audit>(() => (valid ? api.audit(id) : Promise.reject(new Error())), [id]);
  const [officerName, setOfficerName] = useOfficerName();
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    document.title = `Case ${valid ? `#${id}` : ""} · Officer console · VerifyOnce`;
  }, [id, valid]);

  const refresh = (message?: string) => {
    if (message) setNotice(message);
    void detail.reload();
    void audit.reload();
  };

  if (!valid) {
    return (
      <div className="space-y-4">
        <BackLink to="/console">Back to the queue</BackLink>
        <Alert tone="error">That is not a valid case number.</Alert>
      </div>
    );
  }

  const c = detail.data;
  return (
    <div className="space-y-6">
      <BackLink to="/console">Back to the queue</BackLink>
      <Loadable loading={detail.loading && !c} error={detail.error} label="Loading the case…">
        {c ? (
          <>
            <header className="space-y-2">
              <p className="text-sm font-semibold tracking-wide text-accent uppercase">Officer console · case #{c.application_id}</p>
              <h1 className="text-2xl font-bold sm:text-3xl">
                {c.profile_id} · {c.scheme.name}
              </h1>
              {c.profile_label ? <p className="text-muted">{c.profile_label}</p> : null}
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <AppStatusBadge status={c.status} />
                <VerdictBadge verdict={c.decision.verdict} prefix="Current:" />
                {c.awaiting_officer_decision ? <VerdictBadge verdict={c.suggested_verdict} prefix="Suggested:" /> : null}
                <span className="text-sm text-muted">Ruleset {c.scheme.ruleset_version}</span>
              </div>
            </header>

            <OfficerIdentity name={officerName} onName={setOfficerName} />

            {notice ? (
              <Alert tone="success" role="status">
                {notice}
              </Alert>
            ) : null}

            <div className="grid gap-6 lg:grid-cols-3">
              <div className="min-w-0 space-y-6 lg:col-span-2">
                <Section title="Conflicts between documents" id="conflicts">
                  <Conflicts mismatches={c.current_evaluation.mismatches} />
                </Section>

                <Section title="Image quality" id="quality">
                  <ImageQualityTable documents={c.documents} />
                </Section>

                <Section
                  title="Evidence to resolve"
                  id="evidence"
                  aside={<span className="text-sm text-muted">{c.unresolved_flags.length} unresolved</span>}
                >
                  {c.unresolved_flags.length === 0 ? (
                    <p className="text-muted">No flagged evidence is left on this case.</p>
                  ) : (
                    <div className="space-y-5">
                      {c.unresolved_flags.map((f) => (
                        <ResolveFlag
                          key={f.fact_id}
                          caseId={c.application_id}
                          fact={f}
                          officer={officerName}
                          onDone={(msg) => refresh(msg)}
                        />
                      ))}
                    </div>
                  )}
                </Section>

              </div>

              <div className="min-w-0 space-y-6">
                <Section title="Decide" id="decide">
                  <DecideForm detail={c} officer={officerName} onDone={(msg) => refresh(msg)} />
                </Section>
                <Section title="Audit history" id="audit">
                  <Loadable loading={audit.loading && !audit.data} error={audit.error} label="Loading the audit history…">
                    <AuditTimeline events={audit.data?.events ?? []} />
                  </Loadable>
                </Section>
              </div>
            </div>

            <Section title="Current decision and rules" id="rules">
              <DecisionCard decision={c.decision} officer />
            </Section>

            <Section title="All facts on file" id="facts">
              <FactsTable facts={c.documents.flatMap((d) => d.facts)} showDocument caption="Every fact on this profile" />
            </Section>
          </>
        ) : null}
      </Loadable>
    </div>
  );
}

function Conflicts({ mismatches }: { mismatches: Mismatch[] }) {
  if (!mismatches.length) return <p className="text-muted">The documents agree with each other.</p>;
  return (
    <div className="space-y-5">
      {mismatches.map((m) => (
        <div key={m.field}>
          <h3 className="font-semibold">{fieldLabel(m.field)}</h3>
          <p className="text-sm text-muted">{m.message}</p>
          <div className={cx("mt-3 grid gap-3", m.groups.length > 2 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
            {m.groups.map((g, i) => (
              <div key={i} className="rounded-lg border-2 border-amber-600/50 bg-amber-50 p-4">
                <p className="text-lg font-bold break-words">{showValue(m.field, g.value)}</p>
                <ul className="mt-2 space-y-1 text-sm">
                  {g.observations.map((o) => (
                    <li key={o.fact_id}>
                      {documentLabel(o.document_type)} <span className="text-muted">· confidence {o.confidence.toFixed(2)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ReasonField({ id, value, onChange, error }: { id: string; value: string; onChange: (v: string) => void; error?: string | null }) {
  return (
    <div>
      <label htmlFor={id} className="field-label">
        Reason (required)
      </label>
      <textarea
        id={id}
        className="input min-h-[5rem]"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-err` : undefined}
      />
      {error ? (
        <p id={`${id}-err`} className="mt-1 text-sm font-semibold text-red-900">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function checkInputs(officer: string, reason: string): string | null {
  if (!officer.trim()) return "Enter your officer name at the top of the page first.";
  if (reason.trim().length < MIN_REASON) return `Type a reason of at least ${MIN_REASON} characters.`;
  return null;
}

function ResolveFlag({ caseId, fact, officer, onDone }: { caseId: number; fact: Fact; officer: string; onDone: (msg: string) => void }) {
  const [resolution, setResolution] = useState<"confirm" | "reject">("confirm");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reasonId = useId();
  const title = `${fieldLabel(fact.field)} on the ${documentLabel(fact.document_type).toLowerCase()}`;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const local = checkInputs(officer, reason);
    if (local) return setProblem(local);
    setBusy(true);
    setProblem(null);
    try {
      const r = await api.resolveEvidence(caseId, { fact_id: fact.fact_id, resolution, reason: reason.trim(), officer: officer.trim() });
      onDone(`Evidence ${resolution === "confirm" ? "confirmed" : "rejected"}: ${title}. Suggested verdict now: ${verdictLabel(r.suggested_verdict)}. ${r.status === "awaiting officer decision" ? "The case still waits for your decision." : ""}`);
    } catch (exc) {
      setProblem(errorText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="rounded-lg border border-line p-4" noValidate aria-label={`Resolve ${title}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-bold">{title}</h3>
        <SourceBadge fact={fact} />
      </div>
      <p className="mt-1">
        Value <strong>{showValue(fact.field, fact.value)}</strong> <span className="text-muted">· confidence {fact.confidence.toFixed(2)}</span>
      </p>
      {fact.flag_reasons.length ? (
        <ul className="mt-2 list-disc pl-5 text-sm text-amber-950">
          {fact.flag_reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : null}
      <fieldset className="mt-3">
        <legend className="sr-only">Resolution for {title}</legend>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          {(
            [
              ["confirm", "Confirm: the value is right"],
              ["reject", "Reject: the value is wrong"],
            ] as const
          ).map(([value, label]) => (
            <label
              key={value}
              className={cx(
                "flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 focus-within:outline-3 focus-within:outline-offset-2 focus-within:outline-accent",
                resolution === value ? "border-accent bg-accent-soft" : "border-line",
              )}
            >
              <input type="radio" name={`res-${fact.fact_id}`} value={value} checked={resolution === value} onChange={() => setResolution(value)} className="accent-[#1d4f91]" />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="mt-3">
        <ReasonField id={reasonId} value={reason} onChange={setReason} error={problem} />
      </div>
      <button type="submit" className="btn btn-primary mt-3" disabled={busy}>
        {busy ? "Recording…" : "Record resolution"}
      </button>
    </form>
  );
}

function DecideForm({ detail, officer, onDone }: { detail: CaseDetail; officer: string; onDone: (msg: string) => void }) {
  const [action, setAction] = useState<"approve" | "reject" | "override">(detail.can_approve ? "approve" : "reject");
  const [overrideTo, setOverrideTo] = useState<"approve" | "reject">("approve");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reasonId = useId();

  useEffect(() => {
    if (action === "approve" && !detail.can_approve) setAction("reject");
  }, [detail.can_approve, action]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const local = checkInputs(officer, reason);
    if (local) return setProblem(local);
    setBusy(true);
    setProblem(null);
    try {
      const d = await api.decide(detail.application_id, {
        action,
        verdict: action === "override" ? overrideTo : undefined,
        reason: reason.trim(),
        officer: officer.trim(),
      });
      setReason("");
      onDone(`Decision recorded: ${verdictLabel(d.verdict)} (decision #${d.decision_id}). The earlier decisions are kept in the audit history.`);
    } catch (exc) {
      setProblem(errorText(exc));
    } finally {
      setBusy(false);
    }
  };

  const options = [
    { value: "approve" as const, label: "Approve", disabled: !detail.can_approve },
    { value: "reject" as const, label: "Reject", disabled: false },
    { value: "override" as const, label: "Override the rules", disabled: false },
  ];

  return (
    <form onSubmit={submit} className="space-y-4" noValidate aria-label="Record an officer decision">
      {!detail.awaiting_officer_decision ? (
        <Alert tone="info">This case is {detail.status}. A new decision supersedes the current one; nothing is deleted.</Alert>
      ) : null}
      {detail.approve_blockers.length ? (
        <Alert tone="warning" title="Approve is blocked">
          <ul className="list-disc pl-5">
            {detail.approve_blockers.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
        </Alert>
      ) : null}
      <fieldset>
        <legend className="field-label">Action</legend>
        <div className="flex flex-col gap-2">
          {options.map((o) => (
            <label
              key={o.value}
              className={cx(
                "flex min-h-[44px] items-center gap-2 rounded-lg border px-3 py-2 focus-within:outline-3 focus-within:outline-offset-2 focus-within:outline-accent",
                o.disabled ? "cursor-not-allowed border-line bg-paper text-muted" : "cursor-pointer border-line",
                action === o.value && !o.disabled && "border-accent bg-accent-soft",
              )}
            >
              <input
                type="radio"
                name="officer-action"
                value={o.value}
                checked={action === o.value}
                disabled={o.disabled}
                onChange={() => setAction(o.value)}
                className="accent-[#1d4f91]"
              />
              {o.label}
              {o.disabled ? <span className="text-xs">(blocked)</span> : null}
            </label>
          ))}
        </div>
      </fieldset>
      {action === "override" ? (
        <div>
          <label htmlFor="override-to" className="field-label">
            Override to
          </label>
          <select id="override-to" className="input" value={overrideTo} onChange={(e) => setOverrideTo(e.target.value as "approve" | "reject")}>
            <option value="approve">Approved</option>
            <option value="reject">Not eligible</option>
          </select>
        </div>
      ) : null}
      <ReasonField id={reasonId} value={reason} onChange={setReason} error={problem} />
      <button type="submit" className="btn btn-primary w-full" disabled={busy}>
        {busy ? "Recording…" : "Record decision"}
      </button>
    </form>
  );
}
