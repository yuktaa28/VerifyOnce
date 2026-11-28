import { useEffect, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { formatTime, verdictLabel } from "../format";
import type { QueueRow, Verdict } from "../types";
import { OfficerIdentity, useOfficerName } from "../components/officer";
import { AppStatusBadge, Loadable, PageTitle, VerdictBadge, cx, useAsync } from "../components/ui";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "awaiting officer decision", label: "Awaiting officer" },
  { value: "decided automatically", label: "Decided automatically" },
  { value: "decided by officer", label: "Decided by officer" },
];
const VERDICT_FILTERS: { value: "" | Verdict; label: string }[] = [
  { value: "", label: "Any verdict" },
  { value: "needs_human_review", label: "Needs officer review" },
  { value: "approve", label: "Approved" },
  { value: "reject", label: "Not eligible" },
];

export function Console() {
  const [params, setParams] = useSearchParams();
  const statusFilter = params.get("status") ?? "";
  const verdictFilter = params.get("verdict") ?? "";
  const queue = useAsync<QueueRow[]>(() => api.queue(), []);
  const [officerName, setOfficerName] = useOfficerName();

  useEffect(() => {
    document.title = "Officer console · VerifyOnce";
  }, []);

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const rows = queue.data ?? [];
  const counts = useMemo(() => {
    const c: Record<string, number> = { "": rows.length };
    for (const r of rows) {
      c[r.status] = (c[r.status] ?? 0) + 1;
      c[`v:${r.verdict}`] = (c[`v:${r.verdict}`] ?? 0) + 1;
    }
    return c;
  }, [rows]);
  const shown = rows.filter((r) => (!statusFilter || r.status === statusFilter) && (!verdictFilter || r.verdict === verdictFilter));

  return (
    <div className="space-y-6">
      <PageTitle eyebrow="Officer console" title="Review queue">
        Cases awaiting an officer come first. Nothing automatic can close them: each needs an approve, reject or override with a typed
        reason.
      </PageTitle>

      <OfficerIdentity name={officerName} onName={setOfficerName} />

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: "All applications", value: counts[""] ?? 0 },
          { label: "Awaiting officer", value: counts["awaiting officer decision"] ?? 0, strong: true },
          { label: "Decided automatically", value: counts["decided automatically"] ?? 0 },
          { label: "Decided by officer", value: counts["decided by officer"] ?? 0 },
        ].map((c) => (
          <div key={c.label} className={cx("card p-4", c.strong && (c.value ?? 0) > 0 && "border-amber-600 bg-amber-50")}>
            <dt className="text-sm text-muted">{c.label}</dt>
            <dd className="text-2xl font-bold tabular-nums">{queue.data ? c.value : "…"}</dd>
          </div>
        ))}
      </dl>

      <section className="card p-4 sm:p-5" aria-labelledby="queue-heading">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="queue-heading" className="text-lg font-bold">
            Applications
          </h2>
          <button type="button" className="btn btn-quiet" onClick={() => void queue.reload()}>
            Refresh
          </button>
        </div>

        <div className="mt-4 space-y-3">
          <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-2">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                aria-pressed={statusFilter === f.value}
                onClick={() => setFilter("status", f.value)}
                className={cx(
                  "min-h-[44px] rounded-full border px-4 py-1.5 text-sm font-semibold",
                  statusFilter === f.value ? "border-accent bg-accent text-white" : "border-line bg-white text-ink hover:bg-paper",
                )}
              >
                {f.label} <span className="tabular-nums">({counts[f.value] ?? 0})</span>
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="verdict-filter" className="text-sm font-semibold">
              Latest verdict
            </label>
            <select id="verdict-filter" className="input w-auto" value={verdictFilter} onChange={(e) => setFilter("verdict", e.target.value)}>
              {VERDICT_FILTERS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                  {f.value ? ` (${counts[`v:${f.value}`] ?? 0})` : ""}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-5">
          <Loadable loading={queue.loading && !queue.data} error={queue.error} label="Loading the queue…">
            {shown.length === 0 ? (
              <p className="text-muted">No applications match these filters.</p>
            ) : (
              <>
                <div className="table-wrap hidden md:block">
                  <table className="data-table">
                    <caption className="sr-only">Applications in the review queue</caption>
                    <thead>
                      <tr>
                        <th scope="col">Case</th>
                        <th scope="col">Scheme</th>
                        <th scope="col">Status</th>
                        <th scope="col">Latest verdict</th>
                        <th scope="col">Flags</th>
                        <th scope="col">
                          <span className="sr-only">Open</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((r) => (
                        <tr key={r.application_id} className={r.status === "awaiting officer decision" ? "bg-amber-50/60" : undefined}>
                          <td className="min-w-[14rem]">
                            <span className="font-semibold whitespace-nowrap">
                              <span className="font-mono text-xs text-muted">#{r.application_id}</span> {r.profile_id}
                            </span>
                            {r.profile_label ? <span className="block text-xs text-muted">{r.profile_label}</span> : null}
                          </td>
                          <td>{r.scheme_name}</td>
                          <td>
                            <AppStatusBadge status={r.status} />
                            <span className="mt-1 block text-xs whitespace-nowrap text-muted">{formatTime(r.decided_at)}</span>
                          </td>
                          <td>
                            <VerdictBadge verdict={r.verdict} />
                            {r.status === "awaiting officer decision" && r.suggested_verdict !== r.verdict ? (
                              <span className="mt-1 block text-xs text-muted">Suggested: {verdictLabel(r.suggested_verdict)}</span>
                            ) : null}
                          </td>
                          <td className="tabular-nums">{r.unresolved_flags}</td>
                          <td>
                            <Link to={`/console/case/${r.application_id}`} className="btn btn-secondary px-3 py-1.5" aria-label={`Open case ${r.application_id}, ${r.profile_id}`}>
                              Open
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <ul className="space-y-3 md:hidden">
                  {shown.map((r) => (
                    <li key={r.application_id} className="rounded-lg border border-line p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-semibold">
                          #{r.application_id} · {r.profile_id}
                        </span>
                        <AppStatusBadge status={r.status} />
                      </div>
                      {r.profile_label ? <p className="text-xs text-muted">{r.profile_label}</p> : null}
                      <p className="mt-1 text-sm">{r.scheme_name}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <VerdictBadge verdict={r.verdict} />
                        <span className="text-xs text-muted">{r.unresolved_flags} unresolved flag(s)</span>
                      </div>
                      <Link to={`/console/case/${r.application_id}`} className="btn btn-secondary mt-3 w-full" aria-label={`Open case ${r.application_id}, ${r.profile_id}`}>
                        Open case
                      </Link>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Loadable>
        </div>
      </section>
    </div>
  );
}
