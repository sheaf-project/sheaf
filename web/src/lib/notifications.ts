import type {
  ChannelCreate,
  ChannelCreateResponse,
  ChannelUpdate,
  DeleteResult,
  DestructiveConfirm,
  GroupRuleSpec,
  MemberRuleSpec,
  NotificationChannel,
  PreviewResponse,
  ReceivingChannelView,
  ReissueActivationResponse,
  TestDispatchResponse,
  WatchToken,
  WatchTokenCreate,
  WatchTokenUpdate,
} from "@/types/api";
import { apiFetch } from "./api-client";

// ---- watch tokens --------------------------------------------------------

export function listWatchTokens(systemId: string) {
  return apiFetch<WatchToken[]>(`/v1/systems/${systemId}/watch-tokens`);
}

export function createWatchToken(systemId: string, data: WatchTokenCreate) {
  return apiFetch<WatchToken>(`/v1/systems/${systemId}/watch-tokens`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function getWatchToken(tokenId: string) {
  return apiFetch<WatchToken>(`/v1/watch-tokens/${tokenId}`);
}

export function updateWatchToken(tokenId: string, data: WatchTokenUpdate) {
  return apiFetch<WatchToken>(`/v1/watch-tokens/${tokenId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function revokeWatchToken(
  tokenId: string,
  confirm?: DestructiveConfirm,
) {
  return apiFetch<DeleteResult>(`/v1/watch-tokens/${tokenId}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

// ---- channels ------------------------------------------------------------

export function listChannels(tokenId: string) {
  return apiFetch<NotificationChannel[]>(
    `/v1/watch-tokens/${tokenId}/channels`,
  );
}

export function createChannel(tokenId: string, data: ChannelCreate) {
  return apiFetch<ChannelCreateResponse>(
    `/v1/watch-tokens/${tokenId}/channels`,
    { method: "POST", body: JSON.stringify(data) },
  );
}

export function getChannel(channelId: string) {
  return apiFetch<NotificationChannel>(`/v1/channels/${channelId}`);
}

export function updateChannel(channelId: string, data: ChannelUpdate) {
  return apiFetch<NotificationChannel>(`/v1/channels/${channelId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteChannel(
  channelId: string,
  confirm?: DestructiveConfirm,
) {
  return apiFetch<DeleteResult>(`/v1/channels/${channelId}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export function enableChannel(channelId: string) {
  return apiFetch<NotificationChannel>(`/v1/channels/${channelId}/enable`, {
    method: "POST",
  });
}

export function disableChannel(channelId: string) {
  return apiFetch<NotificationChannel>(`/v1/channels/${channelId}/disable`, {
    method: "POST",
  });
}

export function duplicateChannel(channelId: string) {
  return apiFetch<ChannelCreateResponse>(
    `/v1/channels/${channelId}/duplicate`,
    { method: "POST" },
  );
}

export function reissueActivation(channelId: string) {
  return apiFetch<ReissueActivationResponse>(
    `/v1/channels/${channelId}/reissue-activation`,
    { method: "POST" },
  );
}

export function sendTest(channelId: string) {
  return apiFetch<TestDispatchResponse>(`/v1/channels/${channelId}/test`, {
    method: "POST",
  });
}

export function previewChannel(channelId: string, overrides?: ChannelUpdate) {
  return apiFetch<PreviewResponse>(`/v1/channels/${channelId}/preview`, {
    method: "POST",
    body: overrides ? JSON.stringify(overrides) : undefined,
  });
}

export function addGroupRule(channelId: string, rule: GroupRuleSpec) {
  return apiFetch<NotificationChannel>(
    `/v1/channels/${channelId}/group-rules`,
    { method: "POST", body: JSON.stringify(rule) },
  );
}

export function removeGroupRule(channelId: string, groupId: string) {
  return apiFetch<void>(
    `/v1/channels/${channelId}/group-rules/${groupId}`,
    { method: "DELETE" },
  );
}

export function addMemberRule(channelId: string, rule: MemberRuleSpec) {
  return apiFetch<NotificationChannel>(
    `/v1/channels/${channelId}/member-rules`,
    { method: "POST", body: JSON.stringify(rule) },
  );
}

export function removeMemberRule(channelId: string, memberId: string) {
  return apiFetch<void>(
    `/v1/channels/${channelId}/member-rules/${memberId}`,
    { method: "DELETE" },
  );
}

// ---- server config + per-user usage --------------------------------------

export interface NotificationsServerConfig {
  pushover: {
    shared_app_available: boolean;
    shared_app_min_debounce_seconds: number;
  };
}

export function getNotificationsServerConfig() {
  return apiFetch<NotificationsServerConfig>("/v1/notifications/server-config");
}

export interface MyPushoverUsage {
  month: string;
  tier: string;
  count: number;
  cap: number;
  enforced: boolean;
}

export function getMyPushoverUsage() {
  return apiFetch<MyPushoverUsage>("/v1/notifications/pushover-usage");
}

// ---- receiving (recipient-side, account-bound) ---------------------------

export function listReceiving() {
  return apiFetch<ReceivingChannelView[]>("/v1/notifications/receiving");
}

export function unsubscribeReceiving(channelId: string) {
  return apiFetch<void>(
    `/v1/notifications/receiving/${channelId}/unsubscribe`,
    { method: "POST" },
  );
}
