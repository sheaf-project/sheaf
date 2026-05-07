import { apiFetch } from "./api-client";
import type {
  DeleteResult,
  DestructiveConfirm,
  Poll,
  PollAudit,
  PollCreate,
  PollServerConfig,
  PollVote,
  VoteCast,
} from "@/types/api";

export async function listPolls(): Promise<Poll[]> {
  return apiFetch<Poll[]>("/v1/polls");
}

export async function getPollServerConfig(): Promise<PollServerConfig> {
  return apiFetch<PollServerConfig>("/v1/polls/server-config");
}

export async function getPoll(id: string): Promise<Poll> {
  return apiFetch<Poll>(`/v1/polls/${id}`);
}

export async function createPoll(body: PollCreate): Promise<Poll> {
  return apiFetch<Poll>("/v1/polls", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function deletePoll(
  id: string,
  confirm?: DestructiveConfirm,
): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/v1/polls/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export async function castVote(
  pollId: string,
  body: VoteCast,
): Promise<PollVote> {
  return apiFetch<PollVote>(`/v1/polls/${pollId}/votes`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function withdrawVote(
  pollId: string,
  votedAsMemberId: string,
): Promise<void> {
  await apiFetch<void>(
    `/v1/polls/${pollId}/votes/${votedAsMemberId}`,
    { method: "DELETE" },
  );
}

export async function getAudit(pollId: string): Promise<PollAudit> {
  return apiFetch<PollAudit>(`/v1/polls/${pollId}/audit`);
}
