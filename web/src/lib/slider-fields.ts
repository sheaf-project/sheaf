import type { CSSProperties } from "react";
import type { SliderOptions } from "@/types/api";

/**
 * Helpers for the slider custom field type, shared by the editor control,
 * the read-only readout and the settings card.
 *
 * Two modes, as the server defines them (sheaf/services/slider_fields.py):
 * `labeled` is 0..100 between a left and a right label and reads as a
 * percentage; `numeric` is a quantity between min and max with an optional
 * unit. Either can paint a gradient across the track.
 */

/** The server fills every key in, but an older row or a hand-written options
 *  object may not have them all; read defensively. */
export function sliderOptions(raw: unknown): SliderOptions {
  const o = (raw ?? {}) as Partial<SliderOptions>;
  const mode = o.mode === "numeric" ? "numeric" : "labeled";
  const min = mode === "labeled" ? 0 : typeof o.min === "number" ? o.min : 0;
  const max = mode === "labeled" ? 100 : typeof o.max === "number" ? o.max : 10;
  return {
    mode,
    min,
    max,
    step: typeof o.step === "number" && o.step > 0 ? o.step : null,
    unit: typeof o.unit === "string" && o.unit ? o.unit : null,
    labels: {
      left: o.labels?.left ?? null,
      center: o.labels?.center ?? null,
      right: o.labels?.right ?? null,
    },
    gradient:
      Array.isArray(o.gradient) && o.gradient.length >= 2
        ? o.gradient.filter((c): c is string => typeof c === "string")
        : null,
  };
}

/** "62%" or "172.5 cm", the same reading the public profile serves. */
export function formatSliderValue(value: unknown, options: SliderOptions): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  const shown = Math.round(value * 100) / 100;
  if (options.mode === "labeled") return `${shown}%`;
  return options.unit ? `${shown} ${options.unit}` : String(shown);
}

/** CSS for the track: the gradient when there is one, nothing otherwise. */
export function sliderTrackStyle(options: SliderOptions): CSSProperties {
  if (options.gradient && options.gradient.length >= 2) {
    return { background: `linear-gradient(to right, ${options.gradient.join(", ")})` };
  }
  return {};
}

/** Where a value sits on the track, 0..1. */
export function sliderFraction(value: number, options: SliderOptions): number {
  const span = options.max - options.min;
  if (span <= 0) return 0;
  return Math.min(1, Math.max(0, (value - options.min) / span));
}
