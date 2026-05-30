import { apiFetch } from "@/lib/api-client";
import type { ShieldModeStatus } from "@/types/api";

/** Fetch the instance's shield-mode posture.
 *
 *  Unauthenticated; safe to call from any context. `feature_enabled`
 *  reflects the operator-side config and tells the UI whether to
 *  render the Privacy/Security toggle. `active` is true only while
 *  cf-shield is engaged.
 */
export function getShieldModeStatus() {
  return apiFetch<ShieldModeStatus>("/v1/shield-mode/status");
}
