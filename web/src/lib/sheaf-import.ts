/**
 * Sheaf native re-import — preview call.
 *
 * The actual import is enqueued via lib/imports.ts (the async job
 * runner). What's left here is the synchronous preview step.
 */
import { apiFetch } from "./api-client";

export interface SheafPreviewMember {
  id: string;
  name: string;
}

export interface SheafPreviewSummary {
  system_name: string | null;
  member_count: number;
  members: SheafPreviewMember[];
  front_count: number;
  group_count: number;
  tag_count: number;
  custom_field_count: number;
  journal_count: number;
  message_count: number;
  poll_count: number;
  reminder_count: number;
  channel_count: number;
  // True when the uploaded file was an export-with-images zip; the
  // submit step then uses source "sheaf_archive" and may restore the
  // bundled images.
  archive: boolean;
  image_count: number;
}

export async function previewSheafImport(file: File): Promise<SheafPreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<SheafPreviewSummary>("/v1/import/sheaf/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
