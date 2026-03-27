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
}

export interface SheafImportResult {
  members_imported: number;
  fronts_imported: number;
  groups_imported: number;
  tags_imported: number;
  custom_fields_imported: number;
  warnings: string[];
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

export async function runSheafImport(
  file: File,
  options: {
    system_profile: boolean;
    member_ids: string[] | null;
    fronts: boolean;
    groups: boolean;
    tags: boolean;
    custom_fields: boolean;
  },
): Promise<SheafImportResult> {
  const params = new URLSearchParams();
  params.set("system_profile", String(options.system_profile));
  params.set("fronts", String(options.fronts));
  params.set("groups", String(options.groups));
  params.set("tags", String(options.tags));
  params.set("custom_fields", String(options.custom_fields));
  if (options.member_ids !== null) {
    params.set("member_ids", options.member_ids.join(","));
  }

  const form = new FormData();
  form.append("file", file);

  return apiFetch<SheafImportResult>(`/v1/import/sheaf?${params.toString()}`, {
    method: "POST",
    headers: {},
    body: form,
  });
}
