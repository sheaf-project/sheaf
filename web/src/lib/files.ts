import { apiFetch } from "./api-client";

interface UploadResponse {
  url: string;
  key: string;
}

export function uploadFile(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<UploadResponse>("/v1/files/upload", {
    method: "POST",
    body: form,
  });
}
