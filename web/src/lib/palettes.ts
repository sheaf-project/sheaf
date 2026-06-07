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
  | "nonbinary"
  | "asexual"
  | "bi"
  | "crimson"
  | "goldenrod"
  | "mint"
  | "ocean"
  | "pan"
  | "plural"
  | "sepia";

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
  {
    id: "asexual",
    displayName: "Asexual",
    swatch: {
      light: ["#5C005C", "#A4A4A4", "#7E6680"],
      dark: ["#B233B2", "#A4A4A4", "#1A1A1A"],
    },
  },
  {
    id: "bi",
    displayName: "Bi",
    swatch: {
      light: ["#A8025A", "#0038A8", "#9B4F96"],
      dark: ["#EF4D8E", "#4A6FD4", "#C28BBE"],
    },
  },
  {
    id: "crimson",
    displayName: "Crimson",
    swatch: {
      light: ["#DC2626", "#F43F5E", "#C2410C"],
      dark: ["#F87171", "#FB7185", "#F97316"],
    },
  },
  {
    id: "goldenrod",
    displayName: "Goldenrod",
    swatch: {
      light: ["#A16207", "#FACC15", "#1E40AF"],
      dark: ["#FACC15", "#FDE047", "#60A5FA"],
    },
  },
  {
    id: "mint",
    displayName: "Mint",
    swatch: {
      light: ["#059669", "#10B981", "#0D9488"],
      dark: ["#34D399", "#6EE7B7", "#99F6E4"],
    },
  },
  {
    id: "ocean",
    displayName: "Ocean",
    swatch: {
      light: ["#0284C7", "#0EA5E9", "#0891B2"],
      dark: ["#38BDF8", "#7DD3FC", "#67E8F9"],
    },
  },
  {
    id: "pan",
    displayName: "Pan",
    swatch: {
      light: ["#C8005F", "#1685C0", "#B59800"],
      dark: ["#FF6BB0", "#5DCAFF", "#FFD800"],
    },
  },
  {
    id: "plural",
    displayName: "Plural",
    swatch: {
      light: ["#543576", "#7674C2", "#89C8B0"],
      dark: ["#7674C2", "#89C8B0", "#F4ECBC"],
    },
  },
  {
    id: "sepia",
    displayName: "Sepia",
    swatch: {
      light: ["#D97706", "#F59E0B", "#C2410C"],
      dark: ["#FBBF24", "#FCD34D", "#FB923C"],
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
