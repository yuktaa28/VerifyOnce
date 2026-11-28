import { useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, api, demoPin } from "../api";
import { DOCUMENT_TYPES, documentLabel, verdictLabel } from "../format";
import type { Decision, DemoDocument, Profile, ProfileApplication, Scheme, UploadResult } from "../types";
import { useSystem } from "../components/status";
import { DecisionCard, FactsTable, PossibleSchemes, QualityLine } from "../components/tables";
import { Alert, AppStatusBadge, Loadable, PageTitle, Section, SourceBadge, Spinner, VerdictBadge, cx, errorText, useAsync } from "../components/ui";

const PROFILE_ID = /^[A-Za-z0-9_-]{2,40}$/;
const MAX_MB = 3;

export function Citizen() {
  const [params, setParams] = useSearchParams();
  const profileId = params.get("profile") ?? "";

  useEffect(() => {
    document.title = "Citizen · VerifyOnce";
  }, []);

  const chooseProfile = (id: string) => {
    const next = new URLSearchParams(params);
    if (id) next.set("profile", id);
    else next.delete("profile");
    setParams(next);
  };

  return (
    <div className="space-y-6">
      <PageTitle eyebrow="Citizen" title="Your documents and schemes">
        Choose a synthetic profile, upload documents once, then apply to any scheme. Every fact shows where it came from.
      </PageTitle>
      <ProfilePicker profileId={profileId} onChoose={chooseProfile} />
      {profileId ? <ProfileWorkspace key={profileId} profileId={profileId} /> : null}
    </div>
  );
}

// --- Profile picker -----------------------------------------------------------------

function ProfilePicker({ profileId, onChoose }: { profileId: string; onChoose: (id: string) => void }) {
  const profiles = useAsync(() => api.profiles(), []);
  const [newId, setNewId] = useState("");
  const [newError, setNewError] = useState<string | null>(null);
  const selectId = useId();
  const newIdField = useId();

  const startNew = (e: FormEvent) => {
    e.preventDefault();
    const id = newId.trim();
    if (!PROFILE_ID.test(id)) {
      setNewError("Use 2 to 40 letters, numbers, hyphens or underscores, for example demo-live.");
      return;
    }
    setNewError(null);
    onChoose(id);
  };

  const known = profiles.data ?? [];
  const isNew = profileId && profiles.data && !known.some((p) => p.profile_id === profileId);

  return (
    <Section title="1. Choose a profile" id="choose-profile">
      <Loadable loading={profiles.loading && !profiles.data} error={profiles.error} label="Loading profiles…">
        <div className="grid gap-6 md:grid-cols-2">
          <div>
            <label htmlFor={selectId} className="field-label">
              Profile
            </label>
            <select id={selectId} className="input" value={isNew ? "" : profileId} onChange={(e) => onChoose(e.target.value)}>
              <option value="">Select a profile…</option>
              {known.map((p) => (
                <option key={p.profile_id} value={p.profile_id}>
                  {p.profile_id} · {p.documents} document{p.documents === 1 ? "" : "s"}
                  {p.label ? ` · ${p.label}` : ""}
                </option>
              ))}
            </select>
            <p className="mt-2 text-sm text-muted">
              Seeded profiles already have verified documents on file. Try <strong>seed-below-marks</strong> to see a rejection followed
              by a one-click fee waiver.
            </p>
          </div>
          <form onSubmit={startNew} noValidate>
            <label htmlFor={newIdField} className="field-label">
              Or start a new profile
            </label>
            <div className="flex gap-2">
              <input
                id={newIdField}
                className="input"
                value={newId}
                placeholder="demo-live"
                autoComplete="off"
                aria-invalid={newError ? true : undefined}
                aria-describedby={newError ? `${newIdField}-err` : undefined}
                onChange={(e) => setNewId(e.target.value)}
              />
              <button type="submit" className="btn btn-secondary shrink-0">
                Start
              </button>
            </div>
            {newError ? (
              <p id={`${newIdField}-err`} className="mt-1 text-sm text-red-900">
                {newError}
              </p>
            ) : null}
          </form>
        </div>
        {isNew ? (
          <div className="mt-4">
            <Alert tone="info">
              New profile <strong>{profileId}</strong>: upload its documents below. It is saved with the first upload.
            </Alert>
          </div>
        ) : null}
      </Loadable>
    </Section>
  );
}

// --- Workspace ----------------------------------------------------------------------

function ProfileWorkspace({ profileId }: { profileId: string }) {
  const profile = useAsync<Profile | null>(
    () =>
      api.profile(profileId).catch((exc) => {
        if (exc instanceof ApiError && exc.status === 404) return null;
        throw exc;
      }),
    [profileId],
  );
  const applications = useAsync<ProfileApplication[]>(
    () =>
      api.profileApplications(profileId).catch((exc) => {
        if (exc instanceof ApiError && exc.status === 404) return [];
        throw exc;
      }),
    [profileId],
  );

  const refresh = () => {
    void profile.reload();
    void applications.reload();
  };

  return (
    <>
      <Section title="2. Documents on file" id="documents">
        <Loadable loading={profile.loading && !profile.data} error={profile.error} label="Loading documents…">
          <DocumentsOnFile profile={profile.data} />
        </Loadable>
      </Section>
      <UploadSection profileId={profileId} onUploaded={refresh} />
      <ApplySection
        profileId={profileId}
        hasDocuments={!!profile.data?.documents.length}
        applications={applications.data ?? []}
        onApplied={() => void applications.reload()}
      />
    </>
  );
}

function DocumentsOnFile({ profile }: { profile: Profile | null }) {
  if (!profile || !profile.documents.length) {
    return <p className="text-muted">No documents yet. Upload an identity card, an income certificate and a marksheet.</p>;
  }
  return (
    <div className="space-y-5">
      {profile.documents.map((doc) => (
        <article key={doc.document_id} className="rounded-lg border border-line p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-bold">
              {documentLabel(doc.document_type)}
              {doc.filename ? <span className="ml-2 text-sm font-normal text-muted">{doc.filename}</span> : null}
            </h3>
            <SourceBadge fact={{ source: doc.source, extraction_source: doc.extraction.source, extraction_model: doc.extraction.model }} />
          </div>
          <div className="mt-1">
            <QualityLine quality={doc.image_quality} />
          </div>
          {doc.extraction.error ? (
            <div className="mt-2">
              <Alert tone="error">Extraction error: {doc.extraction.error}</Alert>
            </div>
          ) : null}
          <div className="mt-3">
            <FactsTable facts={doc.facts} caption={`Fields read from the ${documentLabel(doc.document_type)}`} />
          </div>
        </article>
      ))}
    </div>
  );
}

// --- Upload -------------------------------------------------------------------------

function clientCheck(file: File): string | null {
  const name = file.name.toLowerCase();
  const typeOk = ["image/png", "image/jpeg"].includes(file.type) || /\.(png|jpe?g)$/.test(name);
  if (!typeOk) return "Only PNG or JPG images are accepted. PDF, GIF, WEBP and other files are not supported.";
  if (file.size > MAX_MB * 1024 * 1024) return `The file is larger than ${MAX_MB} MB. Upload a PNG or JPG image under ${MAX_MB} MB.`;
  if (file.size === 0) return "The file is empty. Choose a PNG or JPG image of the document.";
  return null;
}

function UploadSection({ profileId, onUploaded }: { profileId: string; onUploaded: () => void }) {
  const { status } = useSystem();
  const samples = useAsync<DemoDocument[]>(() => api.demoDocuments(), []);
  const [docType, setDocType] = useState<string>("marksheet");
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsPin, setNeedsPin] = useState(false);
  const [pin, setPin] = useState(demoPin.get());
  const [result, setResult] = useState<UploadResult | null>(null);
  const [sample, setSample] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const ids = { input: useId(), sample: useId(), pin: useId(), help: useId() };

  const pick = (f: File | null) => {
    setResult(null);
    setError(null);
    if (!f) {
      setFile(null);
      setFileError(null);
      return;
    }
    const problem = clientCheck(f);
    setFileError(problem);
    setFile(problem ? null : f);
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    setSample("");
    pick(e.dataTransfer.files?.[0] ?? null);
  };

  const chooseSample = async (id: string) => {
    setSample(id);
    if (!id) return pick(null);
    const doc = samples.data?.find((d) => d.id === id);
    if (!doc) return;
    try {
      const response = await fetch(doc.url);
      if (!response.ok) throw new Error();
      const blob = await response.blob();
      setDocType(doc.document_type);
      pick(new File([blob], doc.filename, { type: blob.type || "image/png" }));
    } catch {
      setFileError("Could not load that sample document. Try again.");
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) {
      setFileError("Choose an image file first, or pick a sample document.");
      return;
    }
    if (pin !== demoPin.get()) demoPin.set(pin.trim());
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const uploaded = await api.upload(profileId, docType, file);
      setResult(uploaded);
      setNeedsPin(false);
      onUploaded();
    } catch (exc) {
      setError(errorText(exc));
      if (exc instanceof ApiError && exc.status === 403) setNeedsPin(true);
    } finally {
      setBusy(false);
    }
  };

  const showPin = needsPin || (status?.own_uploads_need_pin && !!pin);

  return (
    <Section title="3. Upload a document" id="upload">
      <form onSubmit={submit} className="space-y-5" noValidate>
        <fieldset>
          <legend className="field-label">Document type</legend>
          <div className="flex flex-wrap gap-2">
            {DOCUMENT_TYPES.map((t) => (
              <label
                key={t}
                className={cx(
                  "flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 font-semibold focus-within:outline-3 focus-within:outline-offset-2 focus-within:outline-accent",
                  docType === t ? "border-accent bg-accent-soft text-accent-strong" : "border-line bg-white",
                )}
              >
                <input type="radio" name="document_type" value={t} checked={docType === t} onChange={() => setDocType(t)} className="accent-[#1d4f91]" />
                {documentLabel(t)}
              </label>
            ))}
          </div>
        </fieldset>

        <div>
          <span className="field-label" id={`${ids.input}-label`}>
            Document image
          </span>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={cx(
              "flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors focus-within:outline-3 focus-within:outline-offset-2 focus-within:outline-accent",
              dragging ? "border-accent bg-accent-soft" : "border-[#8a94a6] bg-paper",
            )}
          >
            <p className="font-semibold">{file ? file.name : "Drag and drop an image here"}</p>
            <p className="text-sm text-muted" id={ids.help}>
              {file ? `${(file.size / 1024).toFixed(0)} KB · ready to upload` : `PNG or JPG, up to ${MAX_MB} MB.`}
            </p>
            <input
              ref={inputRef}
              id={ids.input}
              type="file"
              accept=".png,.jpg,.jpeg,image/png,image/jpeg"
              className="sr-only"
              aria-labelledby={`${ids.input}-label`}
              aria-describedby={ids.help}
              onChange={(e) => {
                setSample("");
                pick(e.target.files?.[0] ?? null);
                e.target.value = "";
              }}
            />
            <button type="button" className="btn btn-quiet" onClick={() => inputRef.current?.click()}>
              {file ? "Choose a different file" : "Choose a file"}
            </button>
          </div>
          {fileError ? (
            <p className="mt-2 text-sm font-semibold text-red-900" role="alert">
              {fileError}
            </p>
          ) : null}
        </div>

        <div>
          <label htmlFor={ids.sample} className="field-label">
            Or use a sample document
          </label>
          <select id={ids.sample} className="input" value={sample} onChange={(e) => void chooseSample(e.target.value)} disabled={!samples.data}>
            <option value="">{samples.data ? "Choose a synthetic sample…" : "Loading samples…"}</option>
            {samples.data?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
                {d.cached ? " · cached reading" : " · not cached"}
              </option>
            ))}
          </select>
          <p className="mt-1 text-sm text-muted">
            Samples marked “cached reading” were read by the model in an earlier real run; their result is replayed and labelled
            <strong> cache</strong>, never live.
          </p>
        </div>

        {showPin ? (
          <div>
            <label htmlFor={ids.pin} className="field-label">
              Demo PIN (only for your own synthetic files)
            </label>
            <input id={ids.pin} type="password" className="input max-w-xs" value={pin} autoComplete="off" onChange={(e) => setPin(e.target.value)} />
          </div>
        ) : null}

        {status?.own_uploads_need_pin && !showPin ? (
          <p className="text-sm text-muted">
            This public demo accepts the sample documents only.{" "}
            <button type="button" className="font-semibold text-accent underline" onClick={() => setNeedsPin(true)}>
              I have the demo PIN
            </button>
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Reading the document…" : "Upload and read"}
          </button>
          {busy ? <Spinner label="Live extraction can take a few seconds, longer if the model is busy." /> : null}
        </div>
        {error ? (
          <Alert tone="error" title="Upload refused">
            {error}
          </Alert>
        ) : null}
      </form>

      {result ? <UploadOutcome result={result} /> : null}
    </Section>
  );
}

function UploadOutcome({ result }: { result: UploadResult }) {
  const ext = result.extraction;
  const sourceSentence =
    ext.extraction_source === "live"
      ? `${ext.model} read this image just now.`
      : ext.extraction_source === "cache"
        ? `Read by ${ext.model} in an earlier real run and replayed from the cache. No model ran just now.`
        : `Source: ${ext.extraction_source}.`;
  return (
    <div className="mt-6 space-y-4 border-t border-line pt-6" aria-live="polite">
      <h3 className="text-lg font-bold">
        {documentLabel(result.document_type)}: {result.filename}
      </h3>
      {result.status === "verified" ? (
        <Alert tone="success">Every field was read and verified.</Alert>
      ) : result.status === "flagged" ? (
        <Alert tone="warning">Some fields are flagged. An officer will check them. The reasons are listed below.</Alert>
      ) : (
        <Alert tone="error">The document could not be read, so every field goes to an officer.</Alert>
      )}
      <QualityLine quality={result.image_quality} />
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold">Extraction:</span>
        <SourceBadge fact={{ source: "upload", extraction_source: ext.extraction_source, extraction_model: ext.model }} />
        <span className="text-muted">
          provider {ext.provider} · {ext.attempts} model call{ext.attempts === 1 ? "" : "s"} now · {sourceSentence}
        </span>
      </p>
      {ext.error ? <Alert tone="error">Extraction error: {ext.error}</Alert> : null}
      <FactsTable facts={result.facts} caption="Fields read from this upload" />
      {result.other_facts_changed.map((c) => (
        <Alert key={c.fact_id} tone="info">
          This upload changed an earlier fact: {c.field} is now {c.to} ({c.reasons.join("; ") || "checks passed"}).
        </Alert>
      ))}
    </div>
  );
}

// --- Apply --------------------------------------------------------------------------

function ApplySection({
  profileId,
  hasDocuments,
  applications,
  onApplied,
}: {
  profileId: string;
  hasDocuments: boolean;
  applications: ProfileApplication[];
  onApplied: () => void;
}) {
  const schemes = useAsync<Scheme[]>(() => api.schemes(), []);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const resultRef = useRef<HTMLDivElement>(null);

  const apply = async (schemeId: string) => {
    setBusy(schemeId);
    setError(null);
    try {
      const decision = await api.apply(profileId, schemeId);
      setDecisions((prev) => [decision, ...prev]);
      onApplied();
      window.setTimeout(() => resultRef.current?.focus(), 0);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(null);
    }
  };

  const byScheme = new Map(applications.map((a) => [a.scheme_id, a]));
  const [latest, ...earlier] = decisions;

  return (
    <Section title="4. Apply for schemes" id="apply">
      <Loadable loading={schemes.loading && !schemes.data} error={schemes.error} label="Loading schemes…">
        {!hasDocuments ? <p className="mb-4 text-muted">Upload documents first. Applying without them sends the case to an officer.</p> : null}
        <ul className="grid gap-4 md:grid-cols-3">
          {(schemes.data ?? []).map((s) => {
            const app = byScheme.get(s.id);
            const locked = app?.awaiting_officer_decision || app?.decided_by_officer;
            return (
              <li key={s.id} className="flex flex-col rounded-lg border border-line p-4">
                <h3 className="font-bold">{s.name}</h3>
                <p className="mt-1 flex-1 text-sm text-muted">{s.description}</p>
                <p className="mt-2 text-xs text-muted">Ruleset {s.ruleset_version}</p>
                {app ? (
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <VerdictBadge verdict={app.decision.verdict} />
                    <AppStatusBadge status={app.status} />
                  </div>
                ) : null}
                <button
                  type="button"
                  className="btn btn-primary mt-4"
                  disabled={busy !== null || !!locked}
                  onClick={() => void apply(s.id)}
                  aria-label={`${app ? "Apply again for" : "Apply for"} ${s.name}`}
                >
                  {busy === s.id ? "Applying…" : app ? "Apply again" : "Apply"}
                </button>
                {locked ? (
                  <p className="mt-2 text-xs text-muted">
                    {app?.awaiting_officer_decision ? "An officer is reviewing this application." : "An officer has decided this application."}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </Loadable>

      {error ? (
        <div className="mt-4">
          <Alert tone="error" title="Could not apply">
            {error}
          </Alert>
        </div>
      ) : null}

      {latest ? (
        <div ref={resultRef} tabIndex={-1} className="mt-6 space-y-5 border-t border-line pt-6 outline-none" aria-live="polite">
          <h3 className="text-lg font-bold">Result: {latest.scheme_name}</h3>
          <DecisionCard decision={latest} />
          <PossibleSchemes schemes={latest.possible_schemes} onApply={(id) => void apply(id)} busy={busy} />
          {earlier.length ? (
            <div>
              <h4 className="text-sm font-semibold text-muted">Earlier in this visit</h4>
              <ul className="mt-2 space-y-1 text-sm">
                {earlier.map((d) => (
                  <li key={d.decision_id}>
                    {d.scheme_name}: {verdictLabel(d.verdict)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </Section>
  );
}
