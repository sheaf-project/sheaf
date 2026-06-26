import type { System, SystemUpdate } from "@/types/api";
import { apiFetch } from "./api-client";

export function getMySystem() {
  return apiFetch<System>("/v1/systems/me");
}

export function updateMySystem(data: SystemUpdate) {
  return apiFetch<System>("/v1/systems/me", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function exportData(format: "sheaf_native" | "openplural" = "sheaf_native") {
  const q = format === "openplural" ? "?format=openplural" : "";
  return apiFetch<Record<string, unknown>>(`/v1/export${q}`);
}

// --- Article 15 + async export jobs ---------------------------------------

// Artefact format for an export job. "sheaf_native" / "openplural" are the
// full-system exports; the fronts_* values are standalone front-history files
// (a single CSV / JSON / iCalendar file, no zip).
export type ExportJobFormat =
  | "sheaf_native"
  | "openplural"
  | "fronts_csv"
  | "fronts_json"
  | "fronts_ics";

export interface ExportJob {
  id: string;
  include_images: boolean;
  format: ExportJobFormat;
  status: "pending" | "running" | "done" | "failed" | "expired";
  requested_at: string;
  started_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
  file_size_bytes: number | null;
  error: string | null;
}

export function listExportJobs() {
  return apiFetch<ExportJob[]>("/v1/export/jobs");
}

export function createExportJob(body: {
  include_images: boolean;
  // Artefact format: "sheaf_native" (export.json + images/), "openplural"
  // (an .openplural.zip bundle), or a standalone front-history file
  // (fronts_csv / fronts_json / fronts_ics). Defaults server-side to
  // "sheaf_native" when omitted. include_images is ignored for fronts_*.
  format?: ExportJobFormat;
  password: string;
  totp_code?: string;
}) {
  return apiFetch<ExportJob>("/v1/export/jobs", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function exportJobDownloadUrl(id: string): string {
  // Cookie-auth on the GET means a plain navigation works for both the
  // S3-redirect and filesystem-stream cases.
  return `/v1/export/jobs/${id}/download`;
}

export interface AccountDataRequest {
  password: string;
  totp_code?: string;
}

export function requestAccountData(body: AccountDataRequest) {
  return apiFetch<Record<string, unknown>>("/v1/account/data", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
