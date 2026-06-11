/**
 * API client for the async import job runner.
 *
 * Replaces the synchronous per-source import calls (pk-import.ts etc.
 * still own the *preview* step, which stays synchronous). Enqueue an
 * import here, then poll getImportJob until it reaches a terminal
 * status.
 */
import { apiFetch } from "./api-client";

export type ImportJobSource =
  | "pluralkit_file"
  | "pluralkit_api"
  | "tupperbox_file"
  | "simplyplural_file"
  | "sheaf_file"
  | "sheaf_archive"
  | "pluralspace_file"
  | "prism_file";

export type ImportJobStatus =
  | "pending"
  | "running"
  | "complete"
  | "failed"
  | "cancelled";

export interface ImportJobEvent {
  level: "info" | "warning" | "error";
  stage: string;
  message: string;
  record_ref?: string | null;
}

export interface ImportJob {
  id: string;
  source: ImportJobSource;
  status: ImportJobStatus;
  counts: Record<string, number>;
  events: ImportJobEvent[];
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Lighter shape returned by the list endpoint — no events array. */
export interface ImportJobSummary {
  id: string;
  source: ImportJobSource;
  status: ImportJobStatus;
  counts: Record<string, number>;
  started_at: string | null;
  finished_at: string | null;
  archived_at: string | null;
  created_at: string;
}

export interface ImportJobList {
  items: ImportJobSummary[];
  next_cursor: string | null;
}

/** A terminal status is one the runner will never move a job out of. */
export function isTerminal(status: ImportJobStatus): boolean {
  return status === "complete" || status === "failed" || status === "cancelled";
}

/** User-facing label for each import source. */
export const SOURCE_LABELS: Record<ImportJobSource, string> = {
  pluralkit_file: "PluralKit (file)",
  pluralkit_api: "PluralKit (API)",
  tupperbox_file: "Tupperbox",
  simplyplural_file: "SimplyPlural",
  sheaf_file: "Sheaf",
  sheaf_archive: "Sheaf (with images)",
  pluralspace_file: "PluralSpace",
  prism_file: "Prism",
};

const TERMINAL = new Set<ImportJobStatus>(["complete", "failed", "cancelled"]);

/**
 * Generate an idempotency key for an import attempt. Call once per
 * attempt and reuse the value across retries / double-clicks so the
 * server dedupes rather than enqueueing twice.
 */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

/** Enqueue a file-based import. Returns the created (pending) job. */
export async function createFileImport(params: {
  source: Exclude<ImportJobSource, "pluralkit_api">;
  file: File;
  idempotencyKey: string;
  options?: Record<string, unknown>;
  /** Per-source secret. Currently only used by Prism (PRISM1
   * envelope passphrase). Encrypted at rest by the server until the
   * runner finalises the job. */
  credential?: string;
}): Promise<ImportJob> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("source", params.source);
  form.append("idempotency_key", params.idempotencyKey);
  if (params.options !== undefined) {
    form.append("options", JSON.stringify(params.options));
  }
  if (params.credential !== undefined && params.credential !== "") {
    form.append("credential", params.credential);
  }
  return apiFetch<ImportJob>("/v1/imports/file", {
    method: "POST",
    headers: {},
    body: form,
  });
}

/** Enqueue a credential-based import (PluralKit API). */
export async function createApiImport(params: {
  pkToken: string;
  idempotencyKey: string;
  options?: Record<string, unknown>;
}): Promise<ImportJob> {
  return apiFetch<ImportJob>("/v1/imports/api", {
    method: "POST",
    body: JSON.stringify({
      source: "pluralkit_api",
      idempotency_key: params.idempotencyKey,
      pk_token: params.pkToken,
      options: params.options ?? null,
    }),
  });
}

export async function listImportJobs(
  opts: { cursor?: string; includeArchived?: boolean } = {},
): Promise<ImportJobList> {
  const params = new URLSearchParams({ limit: "50" });
  if (opts.includeArchived) params.set("include_archived", "true");
  // Pass the previous page's next_cursor here to fetch the next page.
  if (opts.cursor) params.set("cursor", opts.cursor);
  return apiFetch<ImportJobList>(`/v1/imports?${params.toString()}`);
}

export async function getImportJob(id: string): Promise<ImportJob> {
  return apiFetch<ImportJob>(`/v1/imports/${id}`);
}

/** Cancel a pending job, or archive a terminal one. */
export async function deleteImportJob(id: string): Promise<void> {
  await apiFetch<void>(`/v1/imports/${id}`, { method: "DELETE" });
}

/**
 * refetchInterval callback for TanStack Query — poll every 2s while a
 * job is non-terminal, stop once it settles. Shared by the list and
 * detail pages.
 */
export function pollWhileActive(status: ImportJobStatus | undefined): number | false {
  if (status === undefined) return 2000;
  return TERMINAL.has(status) ? false : 2000;
}
