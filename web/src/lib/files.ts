import { apiFetch } from "./api-client";

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
}

export function listFiles() {
  return apiFetch<UploadedFileInfo[]>("/v1/files/list");
}

export function deleteFile(fileId: string) {
  return apiFetch<{ deleted: boolean; key: string; freed_bytes: number }>(
    `/v1/files/${fileId}`,
    { method: "DELETE" },
  );
}
