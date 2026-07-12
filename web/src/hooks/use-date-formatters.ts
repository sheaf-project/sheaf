import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { getAccessToken } from "@/lib/api-client";
import {
  dateTimeLocalToIso as rawDateTimeLocalToIso,
  formatBirthday as rawFormatBirthday,
  formatDate as rawFormatDate,
  formatDateTime as rawFormatDateTime,
  toDateTimeLocalValue as rawToDateTimeLocalValue,
} from "@/lib/date-format";
import { getMySystem } from "@/lib/systems";
import type { DateFormat } from "@/types/api";

import { browserTimeZone, useTimezone } from "./use-timezone";

/**
 * The blessed way to format dates in a component: returns formatters already
 * bound to the account's `date_format` and the resolved display timezone, so
 * call sites don't thread either through by hand and can't accidentally render
 * in the browser's zone. Both underlying queries share the ["system","me"] key
 * and dedupe with the rest of the app.
 */
export function useDateFormatters() {
  const { resolvedTimeZone } = useTimezone();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
    staleTime: 5 * 60 * 1000,
    enabled: getAccessToken() !== null,
  });
  const dateFormat: DateFormat = system?.date_format ?? "ymd";

  return useMemo(() => {
    // Concrete zone for datetime-local conversion: the resolved preference, or
    // the browser's own zone when "automatic".
    const inputZone = resolvedTimeZone ?? browserTimeZone();
    return {
      dateFormat,
      /** Resolved display zone; undefined = browser-local ("automatic"). */
      timeZone: resolvedTimeZone,
      formatDate: (d: string | null | undefined) =>
        rawFormatDate(d, dateFormat, resolvedTimeZone),
      formatDateTime: (
        d: string | null | undefined,
        opts?: { stamp?: boolean },
      ) => rawFormatDateTime(d, dateFormat, resolvedTimeZone, opts),
      formatBirthday: (d: string | null | undefined) =>
        rawFormatBirthday(d, dateFormat),
      /** Stored UTC ISO -> datetime-local input value, in the display zone. */
      toDateTimeLocal: (iso: string | null | undefined) =>
        rawToDateTimeLocalValue(iso, inputZone),
      /** datetime-local input value -> UTC ISO, interpreted in the display zone. */
      fromDateTimeLocal: (value: string) =>
        rawDateTimeLocalToIso(value, inputZone),
    };
  }, [dateFormat, resolvedTimeZone]);
}
