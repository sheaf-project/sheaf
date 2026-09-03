import * as React from "react";
import type { VariantProps } from "class-variance-authority";

import { badgeVariants } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/** A badge whose "why" is actually reachable.
 *
 *  These badges say the short thing ("dormant", "won't show: archived") and
 *  keep the sentence that explains it somewhere else. That somewhere else used
 *  to be a native `title=`, which is the worst place for it: it never appears
 *  on a touch device at all, screen readers treat it as optional and several
 *  skip it, and it cannot be reached from the keyboard. The badges that need
 *  it most are the ones telling somebody their member is not being shown or
 *  their share link is not serving, so "the explanation is there if your
 *  pointer is a mouse" is not good enough.
 *
 *  So: one focusable trigger, the sentence in a popover that opens on hover,
 *  on focus and on tap, and the same sentence in `aria-label` so a screen
 *  reader gets it without opening anything. A popover rather than a tooltip
 *  because the app has no tooltip primitive and a tooltip would reintroduce
 *  the touch problem - Radix's own tooltip is hover/focus only by design.
 *
 *  One component for all three of them on purpose. They drifted before: three
 *  badges, three copies of the same `title=` idea, and no way to fix the
 *  accessibility of one without remembering the other two.
 */
export function ReasonBadge({
  label,
  reason,
  variant,
  className,
}: {
  /** The badge's visible words. Kept as they were - this component changes how
   *  the explanation is reached, not what either half says. */
  label: string;
  /** The sentence that used to live in `title=`, verbatim. */
  reason: string;
  /** Set when the badge stands on its own and needs the badge chrome. Left
   *  unset by the small chips that live INSIDE another badge and bring their
   *  own type scale, so they keep looking like chips. */
  variant?: VariantProps<typeof badgeVariants>["variant"];
  className?: string;
}) {
  const [open, setOpen] = React.useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          // The whole sentence, not just the badge's two words: a screen
          // reader user should not have to open a popover to find out why
          // their member is not being published.
          aria-label={`${label}. ${reason}`}
          // Hover for the mouse, because that is what the `title=` did and
          // taking it away would be a regression for everybody who already
          // knows to hover. Touch gets the tap below, which is the case the
          // `title=` never covered.
          onPointerEnter={(e) => {
            if (e.pointerType === "mouse") setOpen(true);
          }}
          onPointerLeave={(e) => {
            if (e.pointerType === "mouse") setOpen(false);
          }}
          // Keyboard focus opens it; a tap must not, because on touch the
          // tap is focus THEN click, and letting focus open it left the click
          // to toggle it straight back shut - the first tap did nothing,
          // which is the one case this component exists to fix.
          onFocus={(e) => {
            if (e.currentTarget.matches(":focus-visible")) setOpen(true);
          }}
          onBlur={() => setOpen(false)}
          // Click and tap always OPEN rather than toggle. Radix's trigger
          // would toggle, which reads as "my click dismissed the tooltip"
          // when the mouse had already opened it on hover. Closing is a tap
          // or click anywhere else (the popover dismisses on outside press),
          // Escape, or moving the mouse away.
          onClick={(e) => {
            e.preventDefault();
            setOpen(true);
          }}
          className={cn(
            "inline-flex cursor-help items-center rounded-sm underline decoration-dotted underline-offset-2 outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
            variant && badgeVariants({ variant }),
            className,
          )}
        >
          {label}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        collisionPadding={8}
        className="w-auto max-w-64 p-2 text-xs font-normal leading-snug"
        // Focus never leaves the trigger. Opening on hover must not yank it
        // across the page, and closing on hover-out must not yank it back to a
        // badge the user was not typing on.
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        {reason}
      </PopoverContent>
    </Popover>
  );
}
