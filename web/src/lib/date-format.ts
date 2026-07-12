import { format, isValid, parse, parseISO } from "date-fns";
import { formatInTimeZone, fromZonedTime } from "date-fns-tz";
import type { DateFormat } from "@/types/api";

// This is the one blessed date/time formatter. Everything user-facing should
// render through here (directly, or via the `useDateFormatters` hook which
// binds the account date-format + the resolved display timezone) so a single
// place controls both the day/month order and the timezone. Formatting a date
// with raw `toLocaleDateString` / date-fns `format` elsewhere silently renders
// in the browser's zone and dodges the timezone preference - see the lint
// guard.
//
// `timeZone` is an IANA zone name. When omitted the formatter renders in the
// browser's local zone (the historical behaviour), which is exactly what the
// "automatic" preference resolves to.

const formatStrings: Record<DateFormat, string> = {
  dmy: "dd/MM/yyyy",
  mdy: "MM/dd/yyyy",
  ymd: "yyyy-MM-dd",
};

// Year-less birthdays (the "no birth year" option) render month + day only,
// in the same order as the user's full-date format.
const monthDayFormatStrings: Record<DateFormat, string> = {
  dmy: "dd/MM",
  mdy: "MM/dd",
  ymd: "MM-dd",
};

const formatStringsWithTime: Record<DateFormat, string> = {
  dmy: "dd/MM/yyyy HH:mm",
  mdy: "MM/dd/yyyy h:mm a",
  ymd: "yyyy-MM-dd HH:mm",
};

function formatIn(date: Date, fmt: string, timeZone?: string): string {
  return timeZone ? formatInTimeZone(date, timeZone, fmt) : format(date, fmt);
}

/**
 * Short, DST-aware zone abbreviation for `date` in `timeZone` (or the
 * browser's zone when omitted). Returns "EST"/"EDT" for zones that observe
 * DST, "GMT-5" for fixed-offset zones, or "" if it can't be resolved. This is
 * the stamp appended to absolute times so a rendered timestamp is never
 * ambiguous about which clock it is in.
 */
export function zoneAbbrev(date: Date, timeZone?: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone,
      timeZoneName: "short",
    }).formatToParts(date);
    return parts.find((p) => p.type === "timeZoneName")?.value ?? "";
  } catch {
    return "";
  }
}

export function formatDate(
  dateStr: string | null | undefined,
  dateFormat: DateFormat = "ymd",
  timeZone?: string,
): string {
  if (!dateStr) return "";
  try {
    return formatIn(parseISO(dateStr), formatStrings[dateFormat], timeZone);
  } catch {
    return dateStr;
  }
}

export function formatDateTime(
  dateStr: string | null | undefined,
  dateFormat: DateFormat = "ymd",
  timeZone?: string,
  opts?: { stamp?: boolean },
): string {
  if (!dateStr) return "";
  // Absolute times carry their zone by default so they're unambiguous; pass
  // `{ stamp: false }` for the rare spot where the zone is already implied.
  const stamp = opts?.stamp ?? true;
  try {
    const d = parseISO(dateStr);
    const base = formatIn(d, formatStringsWithTime[dateFormat], timeZone);
    if (!stamp) return base;
    const abbr = zoneAbbrev(d, timeZone);
    return abbr ? `${base} ${abbr}` : base;
  } catch {
    return dateStr;
  }
}

/**
 * Format a member birthday in the user's configured date order.
 *
 * Birthdays come in two shapes: a full `YYYY-MM-DD` date, or a year-less
 * `MM-DD` (when the member opted out of a birth year). Each is rendered in
 * the user's chosen order - with the year for a full date, month/day only
 * for a year-less one. Falls back to the raw string if it does not parse.
 *
 * Birthdays are calendar dates, not instants, so they are timezone-neutral by
 * construction and take no `timeZone` argument.
 */
export function formatBirthday(
  dateStr: string | null | undefined,
  dateFormat: DateFormat = "ymd",
): string {
  if (!dateStr) return "";
  try {
    // A year-less birthday is `MM-DD` (two parts); a full date is
    // `YYYY-MM-DD` (three).
    if (dateStr.split("-").length === 2) {
      const parsed = parse(dateStr, "MM-dd", new Date(2000, 0, 1));
      if (!isValid(parsed)) return dateStr;
      return format(parsed, monthDayFormatStrings[dateFormat]);
    }
    return format(parseISO(dateStr), formatStrings[dateFormat]);
  } catch {
    return dateStr;
  }
}

export const dateFormatLabels: Record<DateFormat, string> = {
  dmy: "DD/MM/YYYY",
  mdy: "MM/DD/YYYY",
  ymd: "YYYY-MM-DD",
};

// ---------------------------------------------------------------------------
// <input type="datetime-local"> conversion, timezone-aware.
//
// A datetime-local input holds a bare wall-clock string (YYYY-MM-DDTHH:mm) with
// no zone. To keep entry consistent with display, we render/parse that
// wall-clock in the SAME zone timestamps are displayed in, not the browser's.
// `timeZone` must be a concrete IANA zone (callers pass the resolved zone,
// falling back to the browser's); the `useDateFormatters` hook binds it for you.
// ---------------------------------------------------------------------------

/** Stored UTC ISO -> the wall-clock value to put in a datetime-local input,
 *  as it reads in `timeZone`. */
export function toDateTimeLocalValue(
  iso: string | null | undefined,
  timeZone: string,
): string {
  if (!iso) return "";
  try {
    return formatInTimeZone(parseISO(iso), timeZone, "yyyy-MM-dd'T'HH:mm");
  } catch {
    return "";
  }
}

/** A datetime-local input's wall-clock value -> a UTC ISO string, interpreting
 *  the wall-clock as being in `timeZone`. Returns null for empty/invalid. */
export function dateTimeLocalToIso(
  value: string,
  timeZone: string,
): string | null {
  if (!value) return null;
  try {
    return fromZonedTime(value, timeZone).toISOString();
  } catch {
    return null;
  }
}
