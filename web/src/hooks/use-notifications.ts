import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import * as api from "@/lib/notifications";
import type {
  ChannelCreate,
  ChannelUpdate,
  WatchTokenCreate,
  WatchTokenUpdate,
} from "@/types/api";

export const notificationKeys = {
  tokens: (systemId: string) => ["watch-tokens", systemId] as const,
  channels: (tokenId: string) => ["channels", tokenId] as const,
  channel: (channelId: string) => ["channel", channelId] as const,
  preview: (channelId: string) => ["channel-preview", channelId] as const,
  receiving: ["notifications-receiving"] as const,
};

// Watch tokens

export function useWatchTokens(systemId: string | undefined) {
  return useQuery({
    queryKey: systemId ? notificationKeys.tokens(systemId) : ["watch-tokens"],
    queryFn: () => api.listWatchTokens(systemId!),
    enabled: !!systemId,
  });
}

export function useCreateWatchToken(systemId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: WatchTokenCreate) => api.createWatchToken(systemId!, data),
    onSuccess: () => {
      if (systemId) {
        qc.invalidateQueries({ queryKey: notificationKeys.tokens(systemId) });
      }
      toast.success("Watcher created");
    },
  });
}

export function useUpdateWatchToken(systemId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: WatchTokenUpdate }) =>
      api.updateWatchToken(id, data),
    onSuccess: () => {
      if (systemId) {
        qc.invalidateQueries({ queryKey: notificationKeys.tokens(systemId) });
      }
    },
  });
}

export function useRevokeWatchToken(systemId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      confirm,
    }: {
      id: string;
      confirm?: { password?: string; totp_code?: string };
    }) => api.revokeWatchToken(id, confirm),
    onSuccess: () => {
      if (systemId) {
        qc.invalidateQueries({ queryKey: notificationKeys.tokens(systemId) });
        qc.invalidateQueries({ queryKey: ["system-safety"] });
      }
      toast.success("Watcher revoked");
    },
  });
}

// Channels

export function useChannels(tokenId: string | undefined) {
  return useQuery({
    queryKey: tokenId ? notificationKeys.channels(tokenId) : ["channels"],
    queryFn: () => api.listChannels(tokenId!),
    enabled: !!tokenId,
  });
}

export function useChannel(channelId: string | undefined) {
  return useQuery({
    queryKey: channelId ? notificationKeys.channel(channelId) : ["channel"],
    queryFn: () => api.getChannel(channelId!),
    enabled: !!channelId,
  });
}

export function useCreateChannel(tokenId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ChannelCreate) => api.createChannel(tokenId!, data),
    onSuccess: () => {
      if (tokenId) {
        qc.invalidateQueries({ queryKey: notificationKeys.channels(tokenId) });
      }
    },
  });
}

export function useUpdateChannel(channelId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ChannelUpdate) => api.updateChannel(channelId!, data),
    onSuccess: (channel) => {
      qc.invalidateQueries({ queryKey: notificationKeys.channel(channel.id) });
      qc.invalidateQueries({
        queryKey: notificationKeys.channels(channel.watch_token_id),
      });
      qc.invalidateQueries({ queryKey: notificationKeys.preview(channel.id) });
      toast.success("Channel updated");
    },
  });
}

export function useDeleteChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      channelId,
      confirm,
    }: {
      channelId: string;
      confirm?: { password?: string; totp_code?: string };
    }) => api.deleteChannel(channelId, confirm),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["channels"] });
      qc.invalidateQueries({ queryKey: ["system-safety"] });
      toast.success("Channel deleted");
    },
  });
}

export function useToggleChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ channelId, enable }: { channelId: string; enable: boolean }) =>
      enable ? api.enableChannel(channelId) : api.disableChannel(channelId),
    onSuccess: (channel) => {
      qc.invalidateQueries({ queryKey: notificationKeys.channel(channel.id) });
      qc.invalidateQueries({
        queryKey: notificationKeys.channels(channel.watch_token_id),
      });
      toast.success(
        channel.destination_state === "active" ? "Channel enabled" : "Channel paused",
      );
    },
  });
}

export function useDuplicateChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (channelId: string) => api.duplicateChannel(channelId),
    onSuccess: (resp) => {
      qc.invalidateQueries({
        queryKey: notificationKeys.channels(resp.channel.watch_token_id),
      });
      toast.success("Channel duplicated");
    },
  });
}

export function useReissueActivation() {
  return useMutation({
    mutationFn: (channelId: string) => api.reissueActivation(channelId),
  });
}

export function useSendTest() {
  return useMutation({
    mutationFn: (channelId: string) => api.sendTest(channelId),
    onSuccess: (result) => {
      if (result.delivered) {
        toast.success("Test notification sent");
      } else {
        toast.error(`Test failed: ${result.error ?? "unknown error"}`);
      }
    },
  });
}

export function useChannelPreview(channelId: string | undefined) {
  return useMutation({
    mutationFn: (overrides?: ChannelUpdate) =>
      api.previewChannel(channelId!, overrides),
  });
}

// Receiving (account-bound subscriptions)

export function useReceiving() {
  return useQuery({
    queryKey: notificationKeys.receiving,
    queryFn: api.listReceiving,
  });
}

export function useUnsubscribeReceiving() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (channelId: string) => api.unsubscribeReceiving(channelId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: notificationKeys.receiving });
      toast.success("Unsubscribed");
    },
  });
}
