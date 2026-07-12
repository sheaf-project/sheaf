import { useMemo } from "react";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * Shared IANA timezone picker. A short "Common" group of friendly, indicative
 * names (e.g. "Eastern Time (US & Canada)") sits first, each mapped to the
 * canonical city zone so DST is handled correctly (Eastern follows EST/EDT, UK
 * follows GMT/BST, etc.). Below it is the full IANA list from
 * `Intl.supportedValuesOf`, with the truly fixed / no-DST zones ("EST", "MST",
 * "HST", "Etc/GMT+5", ...) appended so the "a zone that never shifts" case stays
 * reachable. Every value offered is a real IANA zone the backend accepts, and
 * each zone appears exactly once (common zones are not repeated in the full
 * list) so the trigger shows the friendly label.
 *
 * `specialOptions` renders caller-defined entries at the very top carrying
 * opaque sentinel values - e.g. "Automatic" for the account default, or "Follow
 * account default" + "Automatic (this device)" for a device override. The
 * caller maps those sentinels to whatever they mean in its tier. `AUTO_VALUE` /
 * `FOLLOW_ACCOUNT_VALUE` are provided for the common cases.
 */

// Shared sentinels for the special (non-zone) options. Chosen to never collide
// with a real IANA zone name.
export const AUTO_VALUE = "__auto__";
export const FOLLOW_ACCOUNT_VALUE = "__follow__";

// Friendly shortcuts for the most-reached zones, each mapped to a canonical
// city zone so DST is observed correctly where the region uses it. Shown first;
// these zones are excluded from the full list below so each appears once.
const COMMON_ZONES: { label: string; zone: string }[] = [
  { label: "UTC", zone: "UTC" },
  { label: "Eastern Time (US & Canada)", zone: "America/New_York" },
  { label: "Central Time (US & Canada)", zone: "America/Chicago" },
  { label: "Mountain Time (US & Canada)", zone: "America/Denver" },
  { label: "Pacific Time (US & Canada)", zone: "America/Los_Angeles" },
  { label: "Alaska Time", zone: "America/Anchorage" },
  { label: "Hawaii Time", zone: "Pacific/Honolulu" },
  { label: "UK / Ireland (London)", zone: "Europe/London" },
  { label: "Central European (Paris, Berlin)", zone: "Europe/Paris" },
  { label: "Eastern European (Athens, Helsinki)", zone: "Europe/Athens" },
  { label: "India (Kolkata)", zone: "Asia/Kolkata" },
  { label: "China (Shanghai)", zone: "Asia/Shanghai" },
  { label: "Japan (Tokyo)", zone: "Asia/Tokyo" },
  { label: "Australia Eastern (Sydney)", zone: "Australia/Sydney" },
];

// Fixed-offset / no-DST zones + pure UTC offsets, not returned by
// Intl.supportedValuesOf. Kept reachable in the full list for the "never
// shifts" case; all verified backend-valid. (The cryptic DST-combo names like
// "EST5EDT" are deliberately omitted - the Common group's city zones cover that
// with correct DST.)
const FIXED_EXTRAS = [
  "GMT",
  "EST",
  "MST",
  "HST",
  ...Array.from({ length: 12 }, (_, i) => `Etc/GMT+${i + 1}`),
  ...Array.from({ length: 14 }, (_, i) => `Etc/GMT-${i + 1}`),
];

function allZones(): string[] {
  const common = new Set(COMMON_ZONES.map((c) => c.zone));
  try {
    const canonical =
      (
        Intl as unknown as { supportedValuesOf?: (k: string) => string[] }
      ).supportedValuesOf?.("timeZone") ?? [];
    const merged = new Set<string>([...canonical, ...FIXED_EXTRAS]);
    // Common zones live in the Common group only; a Select value must be unique.
    for (const z of common) merged.delete(z);
    return [...merged].sort();
  } catch {
    return FIXED_EXTRAS.filter((z) => !common.has(z)).sort();
  }
}

export function TimezoneSelect({
  value,
  onValueChange,
  specialOptions,
  id,
}: {
  value: string;
  onValueChange: (v: string) => void;
  /** Leading non-zone options (sentinel value + label), rendered above the
   *  zone groups. */
  specialOptions?: { value: string; label: string }[];
  id?: string;
}) {
  const zones = useMemo(() => allZones(), []);

  return (
    <Select value={value} onValueChange={onValueChange}>
      <SelectTrigger id={id}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="max-h-72">
        {specialOptions?.length ? (
          <>
            {specialOptions.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
            <SelectSeparator />
          </>
        ) : null}
        <SelectGroup>
          <SelectLabel>Common</SelectLabel>
          {COMMON_ZONES.map((c) => (
            <SelectItem key={c.zone} value={c.zone}>
              {c.label}
            </SelectItem>
          ))}
        </SelectGroup>
        <SelectGroup>
          <SelectLabel>All time zones</SelectLabel>
          {zones.map((z) => (
            <SelectItem key={z} value={z}>
              {z}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}
