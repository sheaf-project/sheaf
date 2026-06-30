/**
 * SimplyPlural import — preview call.
 *
 * The actual import is enqueued via lib/imports.ts (the async job
 * runner). What's left here is the synchronous preview step.
 */
import { apiFetch } from "./api-client";

export interface SPPreviewMember {
  id: string;
  name: string;
}

export interface SPPreviewSummary {
  system_name: string | null;
  member_count: number;
  members: SPPreviewMember[];
  custom_front_count: number;
  custom_fronts: SPPreviewMember[];
  front_history_count: number;
  group_count: number;
  custom_field_count: number;
  note_count: number;
  limit_warnings: string[];
}

export async function previewImport(file: File): Promise<SPPreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<SPPreviewSummary>("/v1/import/simplyplural/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
