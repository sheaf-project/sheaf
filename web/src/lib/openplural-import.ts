/**
 * OpenPlural import - preview call.
 *
 * Mirrors the Sheaf native re-import: a synchronous preview step counts
 * the sections in the uploaded file (a bare openplural.json document or
 * an .openplural.zip bundle, sniffed server-side) so the card can show
 * per-section counts and a member selector before the user commits. The
 * actual import is enqueued via lib/imports.ts under source
 * "openplural_file".
 */
import { apiFetch } from "./api-client";
import type { SheafPreviewSummary } from "./sheaf-import";

// OpenPlural carries the same section counts as the Sheaf preview, plus
// the length of the export lineage (how many prior exports this file has
// passed through).
export interface OpenpluralPreviewSummary extends SheafPreviewSummary {
  lineage_length: number;
}

export async function previewOpenpluralImport(
  file: File,
): Promise<OpenpluralPreviewSummary> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<OpenpluralPreviewSummary>("/v1/import/openplural/preview", {
    method: "POST",
    headers: {},
    body: form,
  });
}
