import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Numbered-page navigation: First / Prev / [page numbers with ellipsis] / Next / Last.
 * Shows up to ~7 page buttons around the current page; uses "…" gaps for
 * long lists so very long histories don't blow out the layout.
 */
function pageWindow(current: number, total: number, span = 2): (number | "ellipsis-left" | "ellipsis-right")[] {
  if (total <= 1) return [];
  const out: (number | "ellipsis-left" | "ellipsis-right")[] = [];
  const lo = Math.max(2, current - span);
  const hi = Math.min(total - 1, current + span);
  out.push(1);
  if (lo > 2) out.push("ellipsis-left");
  for (let p = lo; p <= hi; p++) out.push(p);
  if (hi < total - 1) out.push("ellipsis-right");
  if (total > 1) out.push(total);
  return out;
}

export interface PageNavProps {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
}

export function PageNav({ page, totalPages, onChange }: PageNavProps) {
  if (totalPages <= 1) return null;
  const items = pageWindow(page, totalPages);
  return (
    <nav
      className="flex flex-wrap items-center justify-center gap-1"
      aria-label="Pagination"
    >
      <Button
        size="sm"
        variant="ghost"
        className="h-8 px-2"
        disabled={page <= 1}
        onClick={() => onChange(1)}
        aria-label="First page"
      >
        <ChevronsLeft className="size-3.5" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-8 px-2"
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
        aria-label="Previous page"
      >
        <ChevronLeft className="size-3.5" />
      </Button>
      {items.map((it, idx) =>
        typeof it === "number" ? (
          <Button
            key={it}
            size="sm"
            variant={it === page ? "default" : "ghost"}
            className="h-8 min-w-8 px-2"
            onClick={() => onChange(it)}
            aria-current={it === page ? "page" : undefined}
            aria-label={`Page ${it}`}
          >
            {it}
          </Button>
        ) : (
          <span
            key={`${it}-${idx}`}
            className="px-1 text-muted-foreground select-none"
            aria-hidden
          >
            …
          </span>
        ),
      )}
      <Button
        size="sm"
        variant="ghost"
        className="h-8 px-2"
        disabled={page >= totalPages}
        onClick={() => onChange(page + 1)}
        aria-label="Next page"
      >
        <ChevronRight className="size-3.5" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-8 px-2"
        disabled={page >= totalPages}
        onClick={() => onChange(totalPages)}
        aria-label="Last page"
      >
        <ChevronsRight className="size-3.5" />
      </Button>
    </nav>
  );
}
