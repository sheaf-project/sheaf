import { format, isValid, parse, parseISO } from "date-fns";
import type { DateFormat } from "@/types/api";

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

export function formatDate(
  dateStr: string | null | undefined,
  dateFormat: DateFormat = "ymd",
): string {
  if (!dateStr) return "";
  try {
    return format(parseISO(dateStr), formatStrings[dateFormat]);
  } catch {
    return dateStr;
  }
}

export function formatDateTime(
  dateStr: string | null | undefined,
  dateFormat: DateFormat = "ymd",
): string {
  if (!dateStr) return "";
  try {
    return format(parseISO(dateStr), formatStringsWithTime[dateFormat]);
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
