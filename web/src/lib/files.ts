import { apiFetch } from "./api-client";
import type { DeleteQueued, DestructiveConfirm } from "@/types/api";

interface UploadResponse {
  url: string;
  key: string;
}

export function uploadFile(file: File, purpose: "avatar" | "bio" = "avatar"): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<UploadResponse>(`/v1/files/upload?purpose=${purpose}`, {
    method: "POST",
    body: form,
  });
}

export interface StorageUsage {
  used_bytes: number;
  quota_bytes: number; // 0 = unlimited
}

export function getStorageUsage() {
  return apiFetch<StorageUsage>("/v1/files/usage");
}

export interface CleanupResult {
  orphaned: number;
  freed_bytes: number;
  dry_run: boolean;
  keys?: string[];
}

export function cleanupFiles() {
  return apiFetch<CleanupResult>("/v1/files/cleanup", { method: "POST" });
}

export interface UploadedFileInfo {
  id: string;
  key: string;
  url: string;
  purpose: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
  /** Pending-delete grace timestamp from System Safety; null when not
   *  queued. Drives the warning marker on the thumbnail + full badge in
   *  the file detail modal. */
  pending_delete_at: string | null;
}

export function listFiles() {
  return apiFetch<UploadedFileInfo[]>("/v1/files/list");
}

export type FileReferenceKind =
  | "system_avatar"
  | "member_avatar"
  | "member_bio"
  | "journal"
  | "revision";

export interface FileReference {
  kind: FileReferenceKind;
  label: string;
  target_type: string;
  target_id: string;
}

/** Where an uploaded file is currently referenced. Empty `references` means
 * the file is an orphan (nothing breaks if it's deleted). */
export function getFileReferences(fileId: string) {
  return apiFetch<{ key: string; references: FileReference[] }>(
    `/v1/files/${fileId}/references`,
  );
}

export interface FileDeleted {
  deleted: boolean;
  key: string;
  freed_bytes: number;
}

/** Delete an uploaded file. Returns FileDeleted on an immediate delete, or
 * DeleteQueued (202) when System Safety's image-delete grace period applies.
 * `confirm` carries the step-up password / TOTP required by the system's
 * delete-confirmation tier. */
export function deleteFile(fileId: string, confirm?: DestructiveConfirm) {
  return apiFetch<FileDeleted | DeleteQueued>(`/v1/files/${fileId}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}
