/** Browser-side allowlist for image sources emitted by Sheaf itself. */
export function isHostedImage(src: string, cdnBase: string | null): boolean {
  if (src.startsWith("/v1/files/")) return true;
  return Boolean(cdnBase && src.startsWith(`${cdnBase}/`));
}

export function isPublicImageAllowed(
  src: string,
  allowData = false,
): boolean {
  return (
    src.startsWith("/v1/public/files/") ||
    (allowData && src.slice(0, "data:image/".length).toLowerCase() === "data:image/")
  );
}
