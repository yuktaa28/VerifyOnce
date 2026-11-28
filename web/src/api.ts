// Same-origin client for the VerifyOnce API. The site and the API are served by one process.

import type {
  Audit,
  CaseDetail,
  Decision,
  DemoDocument,
  EvidenceResult,
  Health,
  Profile,
  ProfileApplication,
  ProfileSummary,
  QueueRow,
  Scheme,
  Status,
  UploadResult,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const PIN_KEY = "verifyonce.demoPin";

// The demo PIN lives only in this browser tab's session storage, after the visitor types it.
export const demoPin = {
  get(): string {
    try {
      return window.sessionStorage.getItem(PIN_KEY) ?? "";
    } catch {
      return "";
    }
  },
  set(pin: string): void {
    try {
      if (pin) window.sessionStorage.setItem(PIN_KEY, pin);
      else window.sessionStorage.removeItem(PIN_KEY);
    } catch {
      /* storage unavailable: the PIN is simply not remembered */
    }
  },
};

function detailMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === "object" && d && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join("; ");
  }
  if (status === 404) return "Not found.";
  return `The server answered with an error (HTTP ${status}).`;
}

async function request<T>(path: string, init: RequestInit & { pin?: boolean } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.pin) {
    const pin = demoPin.get();
    if (pin) headers.set("X-Demo-Pin", pin);
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Can't reach the VerifyOnce server. Check that it is running, then try again.");
  }
  let body: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) throw new ApiError(response.status, detailMessage(body, response.status));
  return body as T;
}

const json = (data: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

export const api = {
  status: () => request<Status>("/status"),
  health: () => request<Health>("/health"),
  profiles: () => request<ProfileSummary[]>("/profiles"),
  profile: (id: string) => request<Profile>(`/profiles/${encodeURIComponent(id)}`),
  profileApplications: (id: string) =>
    request<ProfileApplication[]>(`/profiles/${encodeURIComponent(id)}/applications`),
  schemes: () => request<Scheme[]>("/schemes"),
  demoDocuments: () => request<DemoDocument[]>("/demo-documents"),
  checkPin: () => request<{ ok: boolean }>("/demo-pin", { method: "POST", pin: true }),

  upload(profileId: string, documentType: string, file: File) {
    const form = new FormData();
    form.append("document_type", documentType);
    form.append("file", file, file.name);
    return request<UploadResult>(`/profiles/${encodeURIComponent(profileId)}/documents`, {
      method: "POST",
      body: form,
      pin: true,
    });
  },
  apply: (profileId: string, schemeId: string) =>
    request<Decision>(`/profiles/${encodeURIComponent(profileId)}/applications`, json({ scheme_id: schemeId })),

  queue: () => request<QueueRow[]>("/officer/queue"),
  caseDetail: (id: number) => request<CaseDetail>(`/officer/cases/${id}`),
  audit: (id: number) => request<Audit>(`/applications/${id}/audit`),
  resolveEvidence: (id: number, body: { fact_id: number; resolution: "confirm" | "reject"; reason: string; officer: string }) =>
    request<EvidenceResult>(`/officer/cases/${id}/evidence`, { ...json(body), pin: true }),
  decide: (
    id: number,
    body: { action: "approve" | "reject" | "override"; verdict?: "approve" | "reject"; reason: string; officer: string },
  ) => request<Decision>(`/officer/cases/${id}/decision`, { ...json(body), pin: true }),
};
