/**
 * Tupperbox import — preview call.
 *
 * The actual import is enqueued via lib/imports.ts (the async job
 * runner). What's left here is the synchronous preview step.
 */
import { apiFetch } from "./api-client";

export interface TBPreviewMember {
  id: string;
  name: string;
}

export interface TBPreviewSummary {
  member_count: number;
  members: TBPreviewMember[];
  group_count: number;
}

export async function previewImport(file: File): Promise<TBPreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<TBPreviewSummary>("/v1/import/tupperbox/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
