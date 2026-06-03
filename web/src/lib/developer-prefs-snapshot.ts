/**
 * Cycle-free module-level snapshot of the `show_technical_errors`
 * developer pref. Imported by both `api-client.ts` (sync read at toast
 * time) and `use-developer-prefs.ts` (writes from React on backend
 * value change / user toggle). Keeping it standalone avoids a
 * dependency cycle between those two files.
 */

let _showTechnicalErrors = false;

export function getShowTechnicalErrors(): boolean {
  return _showTechnicalErrors;
}

export function setShowTechnicalErrorsSnapshot(next: boolean): void {
  _showTechnicalErrors = next;
}
