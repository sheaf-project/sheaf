/**
 * Masquerade import - preview call.
 *
 * The actual import is enqueued via lib/imports.ts (the async job
 * runner). What's left here is the synchronous preview step.
 */
import { apiFetch } from "./api-client";

export interface MasqueradePreviewMember {
  id: string;
  name: string;
}

export interface MasqueradePreviewSummary {
  member_count: number;
  members: MasqueradePreviewMember[];
  limit_warnings: string[];
}

export async function previewImport(
  file: File,
): Promise<MasqueradePreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<MasqueradePreviewSummary>("/v1/import/masquerade/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
