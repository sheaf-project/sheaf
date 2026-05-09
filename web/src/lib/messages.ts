import { apiFetch } from "./api-client";
import type {
  BoardKind,
  BoardSummary,
  ContentRevision,
  DeleteResult,
  DestructiveConfirm,
  FrontStartPrompt,
  Message,
  MessageCreate,
  MessageUpdate,
  MessagesPage,
  NotifyOnFrontSettings,
  UnpinRevisionResponse,
  UnreadCounts,
} from "@/types/api";

function boardQuery(
  board_kind: BoardKind,
  board_member_id?: string | null,
): string {
  const params = new URLSearchParams({ board_kind });
  if (board_kind === "member" && board_member_id) {
    params.set("board_member_id", board_member_id);
  }
  return params.toString();
}

export async function listBoards(
  callerMemberId?: string,
): Promise<BoardSummary[]> {
  const q = callerMemberId
    ? `?caller_member_id=${encodeURIComponent(callerMemberId)}`
    : "";
  return apiFetch<BoardSummary[]>(`/v1/messages/boards${q}`);
}

export async function listBoardMessages(
  board_kind: BoardKind,
  board_member_id: string | null,
  callerMemberId?: string,
): Promise<MessagesPage> {
  const params = new URLSearchParams({ board_kind });
  if (board_kind === "member" && board_member_id) {
    params.set("board_member_id", board_member_id);
  }
  if (callerMemberId) {
    params.set("caller_member_id", callerMemberId);
  }
  return apiFetch<MessagesPage>(`/v1/messages?${params.toString()}`);
}

export async function postMessage(body: MessageCreate): Promise<Message> {
  return apiFetch<Message>("/v1/messages", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function editMessage(
  id: string,
  body: MessageUpdate,
): Promise<Message> {
  return apiFetch<Message>(`/v1/messages/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function deleteMessage(
  id: string,
  confirm?: DestructiveConfirm,
): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/v1/messages/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export async function deleteThread(
  id: string,
  confirm?: DestructiveConfirm,
): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/v1/messages/${id}/thread`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export async function markBoardSeen(
  member_id: string,
  board_kind: BoardKind,
  board_member_id: string | null,
): Promise<void> {
  await apiFetch<void>("/v1/messages/mark-seen", {
    method: "POST",
    body: JSON.stringify({ member_id, board_kind, board_member_id }),
  });
}

export async function getUnread(memberId: string): Promise<UnreadCounts> {
  return apiFetch<UnreadCounts>(
    `/v1/messages/unread?caller_member_id=${encodeURIComponent(memberId)}`,
  );
}

export async function getFrontStartPrompt(
  memberId: string,
): Promise<FrontStartPrompt> {
  return apiFetch<FrontStartPrompt>(
    `/v1/messages/front-start-prompt?member_id=${encodeURIComponent(memberId)}`,
  );
}

export async function getNotifySettings(
  memberId: string,
): Promise<NotifyOnFrontSettings> {
  return apiFetch<NotifyOnFrontSettings>(
    `/v1/messages/notify-settings/${memberId}`,
  );
}

export async function setNotifySettings(
  memberId: string,
  body: NotifyOnFrontSettings,
): Promise<NotifyOnFrontSettings> {
  return apiFetch<NotifyOnFrontSettings>(
    `/v1/messages/notify-settings/${memberId}`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    },
  );
}

export function listMessageRevisions(id: string): Promise<ContentRevision[]> {
  return apiFetch<ContentRevision[]>(`/v1/messages/${id}/revisions`);
}

export function restoreMessageRevision(
  id: string,
  revisionId: string,
): Promise<Message> {
  return apiFetch<Message>(`/v1/messages/${id}/restore-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId }),
  });
}

export function pinMessageRevision(
  id: string,
  revisionId: string,
): Promise<ContentRevision> {
  return apiFetch<ContentRevision>(`/v1/messages/${id}/pin-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId }),
  });
}

export function unpinMessageRevision(
  id: string,
  revisionId: string,
  confirm?: DestructiveConfirm,
): Promise<UnpinRevisionResponse> {
  return apiFetch<UnpinRevisionResponse>(`/v1/messages/${id}/unpin-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId, ...(confirm ?? {}) }),
  });
}

export { boardQuery };
