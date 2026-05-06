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
}

export interface PKImportResult {
  members_imported: number;
  groups_imported: number;
  fronts_imported: number;
  warnings: string[];
}

export interface PKImportOptions {
  system_profile: boolean;
  member_ids: string[] | null;
  groups: boolean;
  front_history: boolean;
}

// --- File path ---------------------------------------------------------------

export async function previewImportFromFile(file: File): Promise<PKPreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<PKPreviewSummary>("/v1/import/pluralkit/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}

export async function runImportFromFile(
  file: File,
  options: PKImportOptions,
): Promise<PKImportResult> {
  const params = new URLSearchParams();
  params.set("system_profile", String(options.system_profile));
  params.set("groups", String(options.groups));
  params.set("front_history", String(options.front_history));
  if (options.member_ids !== null) {
    params.set("member_ids", options.member_ids.join(","));
  }

  const form = new FormData();
  form.append("file", file);

  return apiFetch<PKImportResult>(`/v1/import/pluralkit?${params.toString()}`, {
    method: "POST",
    headers: {},
    body: form,
  });
}

// --- API path ----------------------------------------------------------------

export async function previewImportFromApi(token: string): Promise<PKPreviewSummary> {
  return apiFetch<PKPreviewSummary>("/v1/import/pluralkit-api/preview", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export async function runImportFromApi(
  token: string,
  options: PKImportOptions,
): Promise<PKImportResult> {
  return apiFetch<PKImportResult>("/v1/import/pluralkit-api", {
    method: "POST",
    body: JSON.stringify({ token, options }),
  });
}
