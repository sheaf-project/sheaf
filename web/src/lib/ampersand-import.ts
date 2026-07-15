/**
 * Ampersand import - preview call.
 *
 * The actual import is enqueued via lib/imports.ts (the async job
 * runner). What's left here is the synchronous preview step.
 */
import { apiFetch } from "./api-client";

export interface AmpersandPreviewSummary {
  system_count: number;
  member_count: number;
  custom_front_count: number;
  front_history_count: number;
  tag_count: number;
  custom_field_count: number;
  journal_count: number;
  note_count: number;
  board_message_count: number;
  poll_count: number;
  reminder_count: number;
  asset_count: number;
  limit_warnings: string[];
}

export async function previewImport(
  file: File,
): Promise<AmpersandPreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<AmpersandPreviewSummary>("/v1/import/ampersand/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
