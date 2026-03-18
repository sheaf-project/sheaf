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
}

export interface SPImportResult {
  members_imported: number;
  custom_fronts_imported: number;
  fronts_imported: number;
  groups_imported: number;
  custom_fields_imported: number;
  notes_skipped: number;
  warnings: string[];
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

export async function runImport(
  file: File,
  options: {
    system_profile: boolean;
    member_ids: string[] | null;
    custom_fronts: boolean;
    custom_fields: boolean;
    groups: boolean;
    front_history: boolean;
  },
): Promise<SPImportResult> {
  const params = new URLSearchParams();
  params.set("system_profile", String(options.system_profile));
  params.set("custom_fronts", String(options.custom_fronts));
  params.set("custom_fields", String(options.custom_fields));
  params.set("groups", String(options.groups));
  params.set("front_history", String(options.front_history));
  if (options.member_ids !== null) {
    params.set("member_ids", options.member_ids.join(","));
  }

  const form = new FormData();
  form.append("file", file);

  return apiFetch<SPImportResult>(`/v1/import/simplyplural?${params.toString()}`, {
    method: "POST",
    headers: {},
    body: form,
  });
}
