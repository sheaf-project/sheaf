import { toast } from "sonner";

import { ApiError } from "./api-error";
import { getShowTechnicalErrors } from "./developer-prefs-snapshot";

/**
 * Friendly summary for an HTTP status. Used when the user has NOT
 * opted into technical error details. Falls back to the supplied
 * fallback (or a generic message) for unmapped statuses.
 */
function friendlySummary(status: number, fallback?: string): string {
  if (status === 400) return "Invalid request.";
  if (status === 401) return "You need to sign in to do that.";
  if (status === 403) return "You don't have permission to do that.";
  if (status === 404) return "Not found.";
  if (status === 409) return "Conflict — refresh and try again.";
  if (status === 413) return "That's too large.";
  if (status === 422) return "We couldn't understand that request.";
  if (status === 423) return "Account temporarily locked.";
  if (status === 429) return "Slow down — too many requests.";
  if (status >= 500) return "Server error — please try again.";
  return fallback ?? "Something went wrong.";
}

/**
 * Statuses for which the api-client deliberately does NOT auto-toast,
 * leaving the caller (this helper, or an inline error display) to
 * handle. Currently just 409 - the others either fall under the
 * silent-refresh dance (pre-retry 401) or are toasted by the client.
 *
 * Calls to `showApiErrorToast` from a mutation `onError` toast only
 * when the status falls in this set, so we don't double-toast the
 * statuses the client already covered.
 */
const STATUSES_CLIENT_SKIPS: ReadonlySet<number> = new Set([409]);

/**
 * Compute the same friendly-or-technical string the toast helper would
 * use, but return it instead of toasting. For inline error display
 * (red text under a form, etc.) where a toast would be the wrong UI
 * shape. Same toggle, same fallback semantics.
 */
export function apiErrorMessage(err: unknown, fallback?: string): string {
  const showTechnical = getShowTechnicalErrors();
  if (err instanceof ApiError) {
    return showTechnical
      ? `[${err.status}] ${err.detail}`
      : friendlySummary(err.status, fallback);
  }
  if (err instanceof Error) {
    return showTechnical ? err.message : (fallback ?? err.message);
  }
  return fallback ?? "Something went wrong.";
}

/**
 * Toast an error with technical detail iff the user has opted in.
 *
 * Designed for two callsite shapes:
 *   - **Mutation `onError`**: the api-client already auto-toasts most
 *     statuses, so this helper only fires for statuses the client
 *     skipped (currently 409). Avoids double toasts.
 *   - **Standalone catch**: pass `{ force: true }` to always toast,
 *     for non-mutation paths that don't go through the api-client's
 *     auto-toast at all.
 *
 * When `show_technical_errors` is on, the toast reads
 * `[<status>] <backend detail>`. Off, a friendly summary is used,
 * falling back to the supplied `fallback` string for unmapped
 * statuses.
 */
export function showApiErrorToast(
  err: unknown,
  fallback?: string,
  opts: { force?: boolean } = {},
): void {
  const showTechnical = getShowTechnicalErrors();

  if (err instanceof ApiError) {
    if (!opts.force && !STATUSES_CLIENT_SKIPS.has(err.status)) {
      // The api-client already auto-toasted this; don't double up.
      return;
    }
    if (showTechnical) {
      toast.error(`[${err.status}] ${err.detail}`);
    } else {
      toast.error(friendlySummary(err.status, fallback));
    }
    return;
  }

  if (err instanceof Error) {
    toast.error(showTechnical ? err.message : (fallback ?? err.message));
    return;
  }

  toast.error(fallback ?? "Something went wrong.");
}
