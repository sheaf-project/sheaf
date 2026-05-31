/**
 * Shipped palette catalog for the web client. The actual colour values
 * live in `index.css` under selectors like `[data-palette="purple"]`;
 * this file exposes the metadata the picker UI needs (display name,
 * a 3-colour swatch for the preview chip, and dark/light flags so the
 * Appearance card can render the right preview without applying it).
 *
 * Adding a new palette = drop entries here + add the matching CSS
 * block in index.css. The id is stable across releases and persisted
 * to localStorage / backend, so it must not be renamed.
 *
 * The Android counterpart lives at
 * `sheaf-android/app/src/main/java/systems/lupine/sheaf/ui/theme/`;
 * the ids are intentionally aligned across clients so a future
 * "current palette" sync (or just shared documentation) reads
 * consistently. Material You is Android-only (no web equivalent) and
 * is omitted here.
 */

/** Stable per-palette id. Persisted; do not rename. */
export type PaletteId =
  | "classic"
  | "purple"
  | "oled"
  | "pride"
  | "trans"
  | "nonbinary";

export interface PaletteMeta {
  id: PaletteId;
  displayName: string;
  /** Three representative swatches for the picker preview chip.
   *  Picked to read as "this is what this palette looks like" at a
   *  glance — typically primary, secondary, and an accent. */
  swatch: {
    light: [string, string, string];
    dark: [string, string, string];
  };
}

export const PALETTES: PaletteMeta[] = [
  {
    id: "classic",
    displayName: "Classic",
    swatch: {
      light: ["#0F0F0F", "#F4F4F4", "#737373"],
      dark: ["#FAFAFA", "#404040", "#A3A3A3"],
    },
  },
  {
    id: "purple",
    displayName: "Purple",
    swatch: {
      light: ["#4F46E5", "#EEF2FF", "#8B7BFF"],
      dark: ["#A78BFA", "#15123A", "#6D5FE6"],
    },
  },
  {
    id: "oled",
    displayName: "OLED",
    swatch: {
      light: ["#8B5CF6", "#F5F3FF", "#5B21B6"],
      dark: ["#8B5CF6", "#000000", "#2A1F50"],
    },
  },
  {
    id: "pride",
    displayName: "Pride",
    swatch: {
      light: ["#D81B60", "#FBC02D", "#8E24AA"],
      dark: ["#F06292", "#D4A800", "#CE93D8"],
    },
  },
  {
    id: "trans",
    displayName: "Trans",
    swatch: {
      light: ["#E16A8C", "#55CDFC", "#A0B6CC"],
      dark: ["#F7A8B8", "#2A9FD6", "#C9D6E2"],
    },
  },
  {
    id: "nonbinary",
    displayName: "Non-binary",
    swatch: {
      light: ["#7B3FA4", "#FCF434", "#4A4A55"],
      dark: ["#9C59D1", "#FCF434", "#D8C8E8"],
    },
  },
];

export const DEFAULT_PALETTE: PaletteId = "classic";

/** Resolve a persisted id back to a known palette; fall back to the
 *  default if the id no longer matches anything (catalog rename /
 *  removal between releases). */
export function paletteFromId(id: string | null | undefined): PaletteId {
  if (!id) return DEFAULT_PALETTE;
  const match = PALETTES.find((p) => p.id === id);
  return match ? match.id : DEFAULT_PALETTE;
}
