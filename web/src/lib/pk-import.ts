/**
 * PluralKit import — preview calls.
 *
 * The actual import is enqueued via lib/imports.ts (the async job
 * runner). What's left here is the synchronous preview step used by
 * the import flow's review screen.
 */
import { apiFetch } from "./api-client";

export interface PKPreviewMember {
  id: string;
  name: string;
}

export interface PKPreviewSummary {
  system_name: string | null;
  member_count: number;
  members: PKPreviewMember[];
  group_count: number;
  switch_count: number;
  earliest_switch: string | null;
  latest_switch: string | null;
  limit_warnings: string[];
}

export async function previewImportFromFile(file: File): Promise<PKPreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<PKPreviewSummary>("/v1/import/pluralkit/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}

export async function previewImportFromApi(token: string): Promise<PKPreviewSummary> {
  return apiFetch<PKPreviewSummary>("/v1/import/pluralkit-api/preview", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}
