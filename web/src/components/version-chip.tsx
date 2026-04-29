import { Link } from "react-router";
import { FRONTEND_BUILD, buildLabel } from "@/lib/version";

export function VersionChip() {
  const label = buildLabel({
    gitTag: FRONTEND_BUILD.gitTag,
    gitCommit: FRONTEND_BUILD.gitCommit,
  });

  return (
    <Link
      to="/about"
      className="rounded-md border border-transparent px-1.5 py-0.5 font-mono hover:border-border hover:text-foreground"
      title="Build info & verification"
    >
      {label}
    </Link>
  );
}
