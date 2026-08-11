import type {
  PublicFrontingView,
  PublicMemberView,
  PublicRelationshipsView,
  PublicSystemView,
} from "@/types/api";

import { apiFetch } from "./api-client";

/**
 * The anonymous public-profile surface. These are reachable without an
 * account; apiFetch simply sends no Authorization header when the viewer
 * isn't logged in. A profile that is private, unpublished, revoked, or on an
 * instance with the feature off returns an identical 404 (no existence
 * oracle), which the page renders as a generic "not available".
 *
 * A public profile is located by the system's UUID (`/systems/{id}`); a share
 * link by its opaque token (`/shared/{token}`). Both project the same shapes.
 */

type Source =
  | { kind: "system"; systemId: string }
  | { kind: "link"; token: string };

function base(src: Source): string {
  return src.kind === "system"
    ? `/v1/public/systems/${src.systemId}`
    : `/v1/public/shared/${encodeURIComponent(src.token)}`;
}

export function getPublicSystem(src: Source) {
  return apiFetch<PublicSystemView>(base(src), { skipErrorToast: true });
}

export function getPublicMembers(src: Source) {
  return apiFetch<PublicMemberView[]>(`${base(src)}/members`, {
    skipErrorToast: true,
  });
}

export function getPublicFronting(src: Source) {
  return apiFetch<PublicFrontingView>(`${base(src)}/fronting`, {
    skipErrorToast: true,
  });
}

export function getPublicRelationships(src: Source) {
  return apiFetch<PublicRelationshipsView>(`${base(src)}/relationships`, {
    skipErrorToast: true,
  });
}
