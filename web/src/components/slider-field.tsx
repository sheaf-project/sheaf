import { useId } from "react";
import type { SliderOptions } from "@/types/api";
import { cn } from "@/lib/utils";
import {
  formatSliderValue,
  sliderFraction,
  sliderTrackStyle,
} from "@/lib/slider-fields";

/**
 * The slider custom field type on the web: the control for editing a value
 * and the read-only bar for showing one. The shape of the options and the
 * reading they produce live in lib/slider-fields.ts. The value is always a
 * number on the wire; the editor keeps it as a string while dragging, the way
 * the number field does, and the caller coerces on save.
 */

function EndLabels({ options }: { options: SliderOptions }) {
  const { left, center, right } = options.labels;
  const hasAny = left || center || right;
  if (options.mode === "numeric" && !hasAny) {
    return (
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>
          {options.min}
          {options.unit ? ` ${options.unit}` : ""}
        </span>
        <span>
          {options.max}
          {options.unit ? ` ${options.unit}` : ""}
        </span>
      </div>
    );
  }
  if (!hasAny) return null;
  return (
    <div className="relative flex justify-between text-[11px] text-muted-foreground">
      <span className="max-w-[40%] truncate">{left ?? ""}</span>
      {center && (
        <span className="absolute left-1/2 max-w-[40%] -translate-x-1/2 truncate">
          {center}
        </span>
      )}
      <span className="max-w-[40%] truncate text-right">{right ?? ""}</span>
    </div>
  );
}

/**
 * Editor. `value` is the editor's string (empty = unset); `onChange` gets the
 * next string. A native range input, so keyboard, touch and screen readers
 * all work without a library; the gradient is painted on a track behind it.
 */
export function SliderInput({
  id,
  value,
  options,
  onChange,
}: {
  id?: string;
  value: string;
  options: SliderOptions;
  onChange: (next: string) => void;
}) {
  const fallbackId = useId();
  const inputId = id ?? fallbackId;
  const parsed = value === "" ? null : Number(value);
  const isSet = parsed !== null && Number.isFinite(parsed);
  const midpoint = options.min + (options.max - options.min) / 2;
  const current = isSet ? (parsed as number) : midpoint;
  const step = options.step ?? (options.mode === "labeled" ? 1 : "any");
  const hasGradient = !!options.gradient;

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          {hasGradient && (
            <div
              aria-hidden
              className="pointer-events-none absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded-full"
              style={sliderTrackStyle(options)}
            />
          )}
          <input
            id={inputId}
            type="range"
            min={options.min}
            max={options.max}
            step={step}
            value={current}
            onChange={(e) => onChange(e.target.value)}
            className={cn(
              "relative w-full cursor-pointer",
              hasGradient && "sheaf-slider-on-gradient",
              !isSet && "opacity-60",
            )}
            aria-valuetext={isSet ? formatSliderValue(current, options) : "not set"}
          />
        </div>
        <div className="w-20 shrink-0 text-right text-sm tabular-nums">
          {isSet ? (
            formatSliderValue(current, options)
          ) : (
            <span className="text-muted-foreground">not set</span>
          )}
        </div>
        {isSet && (
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={() => onChange("")}
            aria-label="Clear value"
          >
            clear
          </button>
        )}
      </div>
      <EndLabels options={options} />
    </div>
  );
}

/** Read-only: the bar with a marker at the value, and the reading beside it. */
export function SliderReadout({
  value,
  options,
  className,
}: {
  value: unknown;
  options: SliderOptions;
  className?: string;
}) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const pct = sliderFraction(value, options) * 100;
  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex items-center gap-3">
        <div className="relative h-2 flex-1 rounded-full bg-muted" style={sliderTrackStyle(options)}>
          <div
            className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-foreground shadow"
            style={{ left: `${pct}%` }}
            aria-hidden
          />
        </div>
        <span className="w-20 shrink-0 text-right text-sm tabular-nums">
          {formatSliderValue(value, options)}
        </span>
      </div>
      <EndLabels options={options} />
    </div>
  );
}
