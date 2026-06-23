import { useState } from "react";
import { CircleHelp } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/**
 * A single "type this -> get that" row. `syntax` is shown verbatim in a
 * monospace block; `description` explains what it produces. Kept as plain
 * text (not live-rendered) so the reference stays predictable and matches
 * exactly what the BioEditor renderer (react-markdown + remark-gfm,
 * CommonMark line-break rules) actually does.
 */
function Row({ syntax, description }: { syntax: string; description: string }) {
  return (
    <div className="grid grid-cols-1 gap-1 py-2 sm:grid-cols-2 sm:gap-3">
      <pre className="overflow-x-auto rounded bg-muted px-2 py-1.5 text-xs whitespace-pre-wrap break-words">
        <code>{syntax}</code>
      </pre>
      <p className="text-xs text-muted-foreground sm:self-center">{description}</p>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-0.5">
      <h3 className="text-sm font-semibold">{title}</h3>
      <div className="divide-y divide-border">{children}</div>
    </section>
  );
}

/**
 * A help button + dialog documenting the markdown the editor supports.
 * Reusable anywhere the BioEditor (or its preview) is mounted. The trigger
 * is a ghost icon button; pass `className` to match the surrounding toolbar
 * (the BioEditor toolbar uses `h-7 w-7 p-0`).
 */
export function MarkdownHelp({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className={cn("h-7 w-7 p-0", className)}
          title="Formatting help"
          aria-label="Formatting help"
        >
          <CircleHelp className="h-3.5 w-3.5" />
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Formatting with markdown</DialogTitle>
          <DialogDescription>
            These fields use markdown. Type the syntax on the left to get the
            result on the right; switch to the Preview tab to see it rendered.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {/* The question that prompted this whole thing, up top. */}
          <Section title="Line breaks and paragraphs">
            <Row
              syntax={"First line\\\nSecond line"}
              description="End a line with a backslash to break to the next line WITHOUT starting a new paragraph. (Two spaces at the end of the line do the same thing, but they are invisible and easy to lose.)"
            />
            <Row
              syntax={"A paragraph.\n\nA second paragraph."}
              description="Leave a blank line between blocks to start a new paragraph (with spacing)."
            />
            <Row
              syntax={"Just\npressing enter once"}
              description="A single newline on its own does nothing visible: the two lines flow together as one. Use a trailing backslash (above) or a blank line."
            />
          </Section>

          <Section title="Text styling">
            <Row syntax={"**bold**"} description="Bold text." />
            <Row syntax={"*italic*"} description="Italic text." />
            <Row syntax={"~~strikethrough~~"} description="Struck-through text." />
            <Row syntax={"`inline code`"} description="Inline monospace code." />
          </Section>

          <Section title="Headings">
            <Row
              syntax={"# Big heading\n## Smaller heading\n### Smaller still"}
              description="One to six # characters, then a space. More hashes means a smaller heading."
            />
          </Section>

          <Section title="Lists">
            <Row
              syntax={"- first\n- second\n- third"}
              description="Bulleted list (a dash or * then a space). Indent with spaces to nest."
            />
            <Row
              syntax={"1. first\n2. second\n3. third"}
              description="Numbered list."
            />
            <Row
              syntax={"- [ ] to do\n- [x] done"}
              description="Task list with checkboxes."
            />
          </Section>

          <Section title="Links and images">
            <Row
              syntax={"[link text](https://example.com)"}
              description="A link. A bare URL on its own also becomes a clickable link."
            />
            <Row
              syntax={"![description](https://example.com/pic.png)"}
              description="An image. Use the image button in the toolbar to upload one (hosted images get a green badge in the preview, external ones a yellow badge)."
            />
          </Section>

          <Section title="Quotes and code">
            <Row syntax={"> quoted text"} description="A blockquote." />
            <Row
              syntax={"```\ncode block\n```"}
              description="A code block (three backticks above and below)."
            />
            <Row
              syntax={"```python\nprint('hi')\n```"}
              description="Add a language after the opening backticks for syntax highlighting."
            />
          </Section>

          <Section title="Tables and dividers">
            <Row
              syntax={"| a | b |\n| - | - |\n| 1 | 2 |"}
              description="A table: header row, a divider row of dashes, then the data rows."
            />
            <Row syntax={"---"} description="Three dashes on their own line draw a horizontal divider." />
          </Section>

          <p className="text-xs text-muted-foreground">
            Raw HTML is not supported and will show as plain text.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}
