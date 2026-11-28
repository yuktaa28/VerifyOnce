// Shapes returned by the VerifyOnce API (/api/...). Only the fields the site uses.

export type Verdict = "approve" | "reject" | "needs_human_review";
export type FactStatus = "verified" | "flagged" | "rejected" | "unverified";
export type AppStatus = "awaiting officer decision" | "decided by officer" | "decided automatically";

export interface Status {
  status: string;
  provider: string;
  model: string;
  provider_key_set: boolean;
  extraction_mode: "live" | "cache first";
  confidence_cutoff: number;
  image_quality_thresholds: { min_sharpness: number; min_contrast: number };
  explanations_enabled: boolean;
  schemes: string[];
  public_demo: boolean;
  officer_pin_required: boolean;
  officer_actions_enabled: boolean;
  own_uploads_need_pin: boolean;
  upload_limits: { max_bytes: number; max_mb: number; types: string[] };
  rate_limits: { uploads_per_minute: number; applications_per_minute: number };
}

export interface Health {
  status: "ok" | "degraded";
  database: string;
  profiles?: number;
  problems?: string[];
}

export interface Check {
  name: string;
  kind?: string;
  passed: boolean;
  message: string;
}

export interface Fact {
  fact_id: number;
  document_id: number;
  document_type: string;
  field: string;
  value: unknown;
  confidence: number;
  verification_status: FactStatus;
  reviewed_by: string | null;
  flag_reasons: string[];
  checks: Check[] | null;
  source: string; // "seed" or "upload"
  extraction_source: string | null; // "live" | "cache" | null
  extraction_model: string | null;
  image_quality_ok: boolean | null;
}

export interface ImageQuality {
  sharpness?: number;
  contrast?: number;
  min_sharpness?: number;
  min_contrast?: number;
  ok: boolean;
  source?: string;
}

export interface ProfileSummary {
  profile_id: string;
  label: string | null;
  documents: number;
  applications: number;
}

export interface ProfileDocument {
  document_id: number;
  document_type: string;
  source: string;
  filename: string | null;
  image_quality: ImageQuality | null;
  extraction: { status: string | null; source: string | null; model: string | null; error: string | null };
  facts: Fact[];
}

export interface Profile {
  profile_id: string;
  label: string | null;
  documents: ProfileDocument[];
}

export interface UploadResult {
  profile_id: string;
  document_id: number;
  document_type: string;
  filename: string | null;
  status: "verified" | "flagged" | "extraction_failed";
  image_quality: ImageQuality | null;
  extraction: {
    extraction_source: string;
    status: string;
    provider: string;
    model: string;
    attempts: number;
    error: string | null;
  };
  facts: Fact[];
  other_facts_changed: { fact_id: number; field: string; from: string; to: string; reasons: string[] }[];
}

export interface SchemeRule {
  id: string;
  field: string;
  op: string;
  value: unknown;
  description: string;
}

export interface Scheme {
  id: string;
  name: string;
  ruleset_version: string;
  description: string;
  required_fields: string[];
  rules: SchemeRule[];
}

export interface RuleOutcome {
  rule_id: string;
  field: string;
  operator: string;
  expected: unknown;
  actual: unknown;
  outcome: "pass" | "fail" | string;
  evidence_issues: string[];
  source_fact_ids: number[];
  message: string;
  requirement: string | null;
  source_documents: string[];
}

export interface ReviewReason {
  field: string;
  issue: string;
  message: string;
  fact_ids: number[];
}

export interface Observation {
  fact_id: number;
  document_id: number;
  document_type: string;
  value: unknown;
  confidence: number;
}

export interface Mismatch {
  field: string;
  message: string;
  groups: { value: unknown; observations: Observation[] }[];
}

export interface PossibleScheme {
  scheme_id: string;
  scheme_name: string;
  predicted_verdict: Verdict;
  reason: string;
}

export interface Decision {
  decision_id: number;
  application_id: number;
  profile_id: string;
  scheme_id: string;
  scheme_name: string;
  ruleset_version: string;
  sequence: number;
  supersedes_decision_id: number | null;
  verdict: Verdict;
  reason: string;
  explanation: string | null;
  officer_brief: string | null;
  explanation_source: string | null;
  explanation_model: string | null;
  confidence_cutoff: number;
  rule_outcomes: RuleOutcome[];
  mismatches: Mismatch[];
  review_reasons: ReviewReason[];
  possible_schemes: PossibleScheme[];
  decided_by: string;
  created_at: string;
}

export interface ProfileApplication {
  application_id: number;
  scheme_id: string;
  scheme_name: string;
  status: AppStatus;
  decided_by_officer: boolean;
  awaiting_officer_decision: boolean;
  decision: Decision;
}

export interface QueueRow {
  application_id: number;
  profile_id: string;
  profile_label: string | null;
  scheme_id: string;
  scheme_name: string;
  status: AppStatus;
  verdict: Verdict;
  suggested_verdict: Verdict;
  decided_by: string;
  decided_at: string;
  decision_id: number;
  sequence: number;
  unresolved_flags: number;
  reason: string;
}

export interface CaseDetail {
  application_id: number;
  profile_id: string;
  profile_label: string | null;
  scheme: { id: string; name: string; ruleset_version: string; referenced_fields: string[] };
  status: AppStatus;
  awaiting_officer_decision: boolean;
  decision: Decision;
  decision_is_officer: boolean;
  current_evaluation: { verdict: Verdict; reason: string; review_reasons: ReviewReason[]; mismatches: Mismatch[] };
  suggested_verdict: Verdict;
  unresolved_flags: Fact[];
  can_approve: boolean;
  approve_blockers: string[];
  documents: ProfileDocument[];
}

export interface AuditEvent {
  audit_event_id: number;
  at: string;
  actor: string;
  action: string;
  summary: string;
  decision_id: number | null;
  verdict: Verdict | null;
  is_original_decision: boolean;
  reason: string | null;
}

export interface Audit {
  application_id: number;
  status: AppStatus;
  original_decision_id: number | null;
  events: AuditEvent[];
}

export interface EvidenceResult {
  status: AppStatus;
  suggested_verdict: Verdict;
  suggested_reason: string;
  unresolved_flags: number;
  can_approve: boolean;
  approve_blockers: string[];
}

export interface DemoDocument {
  id: string;
  case: string;
  filename: string;
  document_type: string;
  label: string;
  cached: boolean;
  url: string;
}
