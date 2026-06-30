/**
 * PluralSpace import API client.
 *
 * Preview opens the export zip server-side and returns counts +
 * member list. The actual import is enqueued through the unified
 * /v1/imports/file endpoint (createFileImport in lib/imports.ts).
 */
import { apiFetch } from "./api-client";

export interface PluralspacePreviewMember {
  id: string;
  name: string;
  is_custom_front: boolean;
  is_archived: boolean;
  has_avatar: boolean;
  roles: string[];
  groups: string[];
}

export interface PluralspacePreviewSummary {
  system_name: string | null;
  format_version: string | null;
  export_date: string | null;
  member_count: number;
  custom_front_count: number;
  members: PluralspacePreviewMember[];
  group_count: number;
  custom_field_count: number;
  front_count: number;
  journal_entry_count: number;
  chat_channel_count: number;
  chat_message_count: number;
  poll_count: number;
  thought_count: number;
  media_file_count: number;
  limit_warnings: string[];
}

export async function previewImport(file: File): Promise<PluralspacePreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<PluralspacePreviewSummary>("/v1/import/pluralspace/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
