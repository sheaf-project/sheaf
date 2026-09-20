/**
 * BerryTree import - preview call.
 *
 * The actual import is enqueued via lib/imports.ts (the async job
 * runner). What's left here is the synchronous preview step.
 */
import { apiFetch } from "./api-client";

export interface BerrytreeUnsupportedSection {
  name: string;
  count: number;
}

export interface BerrytreePreviewSummary {
  system_name: string | null;
  schema_version: number | null;
  member_count: number;
  members: { id: string; name: string }[];
  template_count: number;
  custom_front_count: number;
  custom_fronts: { id: string; name: string }[];
  fronting_type_count: number;
  front_history_count: number;
  folder_count: number;
  tag_count: number;
  custom_field_count: number;
  unsupported_sections: BerrytreeUnsupportedSection[];
  export_errors: string[];
  limit_warnings: string[];
}

export async function previewImport(
  file: File,
): Promise<BerrytreePreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<BerrytreePreviewSummary>("/v1/import/berrytree/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
