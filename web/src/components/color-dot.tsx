export function ColorDot({ color, className }: { color: string | null; className?: string }) {
  if (!color) return null;
  return (
    <span
      className={`inline-block h-3 w-3 rounded-full shrink-0 ${className ?? ""}`}
      style={{ backgroundColor: color }}
    />
  );
}
