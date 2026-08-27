import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import * as api from "@/lib/sharing";
import { useAuth } from "@/hooks/use-auth";
import type { ShareGrantCreate, ShareViewCreate, ShareViewUpdate } from "@/types/api";

export const sharingKeys = {
  views: ["share-views"] as const,
  view: (id: string) => ["share-views", id] as const,
  preview: (id: string) => ["share-views", id, "preview"] as const,
  grants: ["share-grants"] as const,
  audit: ["sharing-audit"] as const,
};

function invalidateAll(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: sharingKeys.views });
  qc.invalidateQueries({ queryKey: sharingKeys.grants });
  qc.invalidateQueries({ queryKey: sharingKeys.audit });
}

// --- Views ---

export function useShareViews() {
  return useQuery({ queryKey: sharingKeys.views, queryFn: api.listShareViews });
}

export function useCreateShareView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ShareViewCreate) => api.createShareView(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sharingKeys.views });
      toast.success("View created");
    },
  });
}

export function useUpdateShareView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
      skipErrorToast = false,
    }: {
      id: string;
      data: ShareViewUpdate;
      skipErrorToast?: boolean;
    }) => api.updateShareView(id, data, skipErrorToast),
    onSuccess: () => invalidateAll(qc),
  });
}

export function useDeleteShareView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteShareView(id),
    onSuccess: () => {
      invalidateAll(qc);
      toast.success("View deleted");
    },
  });
}

// --- View contents ---

export function useAddViewMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      viewId,
      memberId,
      reauth,
      skipErrorToast = false,
    }: {
      viewId: string;
      memberId: string;
      reauth?: { password?: string; totp_code?: string };
      skipErrorToast?: boolean;
    }) => api.addViewMember(viewId, memberId, reauth, skipErrorToast),
    onSuccess: () => invalidateAll(qc),
  });
}

export function useRemoveViewMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ viewId, memberId }: { viewId: string; memberId: string }) =>
      api.removeViewMember(viewId, memberId),
    onSuccess: () => invalidateAll(qc),
  });
}

export function useAddViewGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      viewId,
      groupId,
      reauth,
      skipErrorToast = false,
    }: {
      viewId: string;
      groupId: string;
      reauth?: { password?: string; totp_code?: string };
      skipErrorToast?: boolean;
    }) => api.addViewGroup(viewId, groupId, reauth, skipErrorToast),
    onSuccess: (res) => {
      invalidateAll(qc);
      const skips: string[] = [];
      if (res.skipped_never_shareable)
        skips.push(`${res.skipped_never_shareable} never-shareable`);
      if (res.skipped_not_public)
        skips.push(`${res.skipped_not_public} not public`);
      const suffix = skips.length ? `, skipped ${skips.join(" and ")}` : "";
      toast.success(
        `Added ${res.added} member${res.added === 1 ? "" : "s"} from group${suffix}`,
      );
    },
  });
}

export function useRemoveViewGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ viewId, groupId, removeMembers }: { viewId: string; groupId: string; removeMembers: boolean }) =>
      api.removeViewGroup(viewId, groupId, removeMembers),
    onSuccess: () => invalidateAll(qc),
  });
}

export function useAddViewField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      viewId,
      fieldId,
      reauth,
      skipErrorToast = false,
    }: {
      viewId: string;
      fieldId: string;
      reauth?: { password?: string; totp_code?: string };
      skipErrorToast?: boolean;
    }) => api.addViewField(viewId, fieldId, reauth, skipErrorToast),
    onSuccess: () => invalidateAll(qc),
  });
}

export function useRemoveViewField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ viewId, fieldId }: { viewId: string; fieldId: string }) =>
      api.removeViewField(viewId, fieldId),
    onSuccess: () => invalidateAll(qc),
  });
}

// --- Grants ---

export function useShareGrants() {
  return useQuery({ queryKey: sharingKeys.grants, queryFn: api.listShareGrants });
}

export function useCreateShareGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ShareGrantCreate) => api.createShareGrant(data),
    onSuccess: () => invalidateAll(qc),
  });
}

export function useRotateShareGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.rotateShareGrant(id),
    onSuccess: () => invalidateAll(qc),
  });
}

export function useRevokeShareGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.revokeShareGrant(id),
    onSuccess: () => {
      invalidateAll(qc);
      toast.success("Unpublished");
    },
  });
}

// --- Audit ---

export function useShareAudit() {
  return useQuery({ queryKey: sharingKeys.audit, queryFn: api.getShareAudit });
}

// --- Preview ---

/**
 * The view as a visitor would receive it. Only fetched while the preview is
 * actually open (`enabled`), and re-fetched every time it is: an owner opens
 * this straight after changing something, so a cached answer from before the
 * change is the one thing it must not show.
 *
 * Deliberately NOT polled. The real public page polls so that a revocation
 * reaches a visitor's open tab; a preview is a thing the owner opened, looked
 * at, and will close, and there is no revocation for it to notice.
 */
export function useSharePreview(viewId: string, enabled: boolean) {
  return useQuery({
    queryKey: sharingKeys.preview(viewId),
    queryFn: () => api.getSharePreview(viewId),
    enabled,
    staleTime: 0,
    refetchOnMount: "always",
  });
}

// --- Attestation ---

export function useAttestAdult() {
  const { refreshUser } = useAuth();
  return useMutation({
    mutationFn: () => api.attestAdult(),
    onSuccess: async () => {
      // The 18+ flag rides on the user object; refresh it so gates re-evaluate.
      await refreshUser();
    },
  });
}
