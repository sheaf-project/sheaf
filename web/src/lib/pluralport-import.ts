/**
 * PluralPort import - preview call.
 *
 * Mirrors the Sheaf native re-import: a synchronous preview step counts
 * the sections in the uploaded file (a bare pluralport.json document or
 * a .pluralport.zip bundle, sniffed server-side) so the card can show
 * per-section counts and a member selector before the user commits. The
 * actual import is enqueued via lib/imports.ts under source
 * "pluralport_file".
 */
import { apiFetch } from "./api-client";
import type { SheafPreviewSummary } from "./sheaf-import";

// PluralPort carries the same section counts as the Sheaf preview, plus
// the length of the export lineage (how many prior exports this file has
// passed through).
export interface PluralportPreviewSummary extends SheafPreviewSummary {
  lineage_length: number;
}

export async function previewPluralportImport(
  file: File,
): Promise<PluralportPreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<PluralportPreviewSummary>("/v1/import/pluralport/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
