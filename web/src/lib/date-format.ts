import { format, parseISO } from "date-fns";
import type { DateFormat } from "@/types/api";

const formatStrings: Record<DateFormat, string> = {
  dmy: "dd/MM/yyyy",
  mdy: "MM/dd/yyyy",
  ymd: "yyyy-MM-dd",
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

export const dateFormatLabels: Record<DateFormat, string> = {
  dmy: "DD/MM/YYYY",
  mdy: "MM/DD/YYYY",
  ymd: "YYYY-MM-DD",
};
