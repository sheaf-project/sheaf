/**
 * Prism (.prism) import API client.
 *
 * Preview decrypts the PRISM1 envelope server-side using the
 * user-supplied passphrase and returns entity counts + a member
 * list. The actual import is enqueued through the unified
 * /v1/imports/file endpoint with the passphrase passed as the
 * `credential` form field (see createFileImport in lib/imports.ts).
 */
import { apiFetch } from "./api-client";

export interface PrismPreviewMember {
  id: string;
  name: string;
  is_archived: boolean;
  has_avatar: boolean;
  pluralkit_id: string | null;
}

export interface PrismPreviewSummary {
  system_name: string | null;
  format_version: string | null;
  export_date: string | null;
  app_name: string | null;
  member_count: number;
  members: PrismPreviewMember[];
  group_count: number;
  custom_field_count: number;
  front_session_count: number;
  sleep_session_count: number;
  conversation_count: number;
  message_count: number;
  poll_count: number;
  poll_option_count: number;
  note_count: number;
  reminder_count: number;
  habit_count: number;
  member_board_post_count: number;
  media_attachment_count: number;
  media_blob_count: number;
  limit_warnings: string[];
}

export async function previewImport(
  file: File,
  passphrase: string,
): Promise<PrismPreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  form.append("passphrase", passphrase);
  return apiFetch<PrismPreviewSummary>("/v1/import/prism/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
