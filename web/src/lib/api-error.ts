/**
 * Standalone error class so non-api-client modules (toast helper,
 * route guards, etc.) can `instanceof`-check or construct without
 * importing the fetch wrapper.
 */
export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}
