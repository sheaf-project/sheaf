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

export interface TBImportResult {
  members_imported: number;
  groups_imported: number;
  warnings: string[];
}

export interface TBImportOptions {
  member_ids: string[] | null;
  groups: boolean;
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

export async function runImport(
  file: File,
  options: TBImportOptions,
): Promise<TBImportResult> {
  const params = new URLSearchParams();
  params.set("groups", String(options.groups));
  if (options.member_ids !== null) {
    params.set("member_ids", options.member_ids.join(","));
  }

  const form = new FormData();
  form.append("file", file);

  return apiFetch<TBImportResult>(`/v1/import/tupperbox?${params.toString()}`, {
    method: "POST",
    headers: {},
    body: form,
  });
}
