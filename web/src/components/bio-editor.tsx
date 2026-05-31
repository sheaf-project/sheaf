import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypeHighlight from "rehype-highlight";
import rehypeSlug from "rehype-slug";
import hljsLightUrl from "highlight.js/styles/github.min.css?url";
import hljsDarkUrl from "highlight.js/styles/github-dark.min.css?url";
import { useTheme } from "@/hooks/use-theme";
import { useShowImageBadges } from "@/hooks/use-preferences";
import { ImagePickerDialog } from "@/components/image-picker";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Bold,
  Code,
  FileCode,
  Heading2,
  ImagePlus,
  Italic,
  Link,
  List,
  ListOrdered,
  ListTodo,
  Quote,
  Strikethrough,
  Tag,
  Tags,
} from "lucide-react";
import { type AuthConfig, getAuthConfig } from "@/lib/auth";

const HLJS_LINK_ID = "hljs-theme-stylesheet";

function useHljsTheme() {
  const { effectiveMode } = useTheme();
  useEffect(() => {
    const href = effectiveMode === "dark" ? hljsDarkUrl : hljsLightUrl;
    let link = document.getElementById(
      HLJS_LINK_ID,
    ) as HTMLLinkElement | null;
    if (!link) {
      link = document.createElement("link");
      link.id = HLJS_LINK_ID;
      link.rel = "stylesheet";
      document.head.appendChild(link);
    }
    if (link.href !== new URL(href, document.baseURI).href) {
      link.href = href;
    }
  }, [effectiveMode]);
}

function isHostedImage(src: string, cdnBase: string | null) {
  if (src.startsWith("/v1/files/")) return true;
  if (cdnBase && src.startsWith(cdnBase + "/")) return true;
  return false;
}

function MarkdownPreview({
  content,
  showBadgesOverride,
}: {
  content: string;
  showBadgesOverride?: boolean;
}) {
  const [defaultBadges] = useShowImageBadges();
  const showBadges = showBadgesOverride ?? defaultBadges;
  const [cdnBase, setCdnBase] = useState<string | null>(null);
  useHljsTheme();

  useEffect(() => {
    getAuthConfig()
      .then((c: AuthConfig) => setCdnBase(c.file_cdn_base))
      .catch(() => {});
  }, []);

  if (!content) {
    return <p className="text-sm text-muted-foreground italic">Nothing here yet.</p>;
  }

  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          rehypeSlug,
          [
            rehypeAutolinkHeadings,
            { behavior: "wrap", properties: { className: "heading-anchor" } },
          ],
          [rehypeHighlight, { detect: true, ignoreMissing: true }],
        ]}
        components={{
          img: ({ src, alt, ...props }) => {
            const hosted = src ? isHostedImage(src, cdnBase) : false;
            return (
              <span className="relative inline-block">
                <img
                  src={src}
                  alt={alt}
                  {...props}
                  className="max-w-full rounded-md"
                />
                {showBadges && src && (
                  <span
                    className={`absolute top-1 right-1 rounded px-1 py-0.5 text-[10px] font-medium leading-none ${
                      hosted
                        ? "bg-green-500/80 text-white"
                        : "bg-yellow-500/80 text-white"
                    }`}
                  >
                    {hosted ? "hosted" : "external"}
                  </span>
                )}
              </span>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export function BioEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [tab, setTab] = useState<string>("write");
  const [showBadges, setShowBadges] = useShowImageBadges();
  const [imagePickerOpen, setImagePickerOpen] = useState(false);

  function insertAtCursor(text: string) {
    const ta = textareaRef.current;
    if (!ta) {
      onChange(value + text);
      return;
    }
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const newValue = value.slice(0, start) + text + value.slice(end);
    onChange(newValue);
    requestAnimationFrame(() => {
      ta.selectionStart = ta.selectionEnd = start + text.length;
      ta.focus();
    });
  }

  function wrapSelection(before: string, after: string) {
    const ta = textareaRef.current;
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const selected = value.slice(start, end);
    const newValue = value.slice(0, start) + before + selected + after + value.slice(end);
    onChange(newValue);
    requestAnimationFrame(() => {
      ta.selectionStart = start + before.length;
      ta.selectionEnd = end + before.length;
      ta.focus();
    });
  }

  function prefixLines(prefixFor: (i: number) => string) {
    const ta = textareaRef.current;
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    // Expand selection to full lines so prefixes land at line starts.
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    const nextNewline = value.indexOf("\n", end);
    const lineEnd = nextNewline === -1 ? value.length : nextNewline;
    const block = value.slice(lineStart, lineEnd);
    const lines = block.length === 0 ? [""] : block.split("\n");
    const out = lines.map((line, i) => prefixFor(i) + line).join("\n");
    const newValue = value.slice(0, lineStart) + out + value.slice(lineEnd);
    onChange(newValue);
    requestAnimationFrame(() => {
      ta.selectionStart = lineStart;
      ta.selectionEnd = lineStart + out.length;
      ta.focus();
    });
  }

  function wrapBlock(fenceBefore: string, fenceAfter: string) {
    const ta = textareaRef.current;
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const selected = value.slice(start, end);
    const before = value.slice(0, start);
    const after = value.slice(end);
    const leadNl = before.length === 0 || before.endsWith("\n") ? "" : "\n";
    const trailNl = after.length === 0 || after.startsWith("\n") ? "" : "\n";
    const block = `${leadNl}${fenceBefore}\n${selected}\n${fenceAfter}${trailNl}`;
    onChange(before + block + after);
    requestAnimationFrame(() => {
      const cursor = start + leadNl.length + fenceBefore.length + 1;
      ta.selectionStart = cursor;
      ta.selectionEnd = cursor + selected.length;
      ta.focus();
    });
  }

  return (
    <div className="space-y-1">
      <Tabs value={tab} onValueChange={setTab}>
        <div className="flex items-center justify-between">
          <TabsList className="h-8">
            <TabsTrigger value="write" className="text-xs px-2 py-1">Write</TabsTrigger>
            <TabsTrigger value="preview" className="text-xs px-2 py-1">Preview</TabsTrigger>
          </TabsList>
          {tab === "write" && (
            <div className="flex flex-wrap justify-end gap-0.5">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => wrapSelection("**", "**")}
                title="Bold"
              >
                <Bold className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => wrapSelection("*", "*")}
                title="Italic"
              >
                <Italic className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => wrapSelection("~~", "~~")}
                title="Strikethrough"
              >
                <Strikethrough className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => prefixLines(() => "## ")}
                title="Heading"
              >
                <Heading2 className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => prefixLines(() => "- ")}
                title="Bulleted list"
              >
                <List className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => prefixLines((i) => `${i + 1}. `)}
                title="Numbered list"
              >
                <ListOrdered className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => prefixLines(() => "- [ ] ")}
                title="Task list"
              >
                <ListTodo className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => prefixLines(() => "> ")}
                title="Quote"
              >
                <Quote className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => insertAtCursor("[text](url)")}
                title="Link"
              >
                <Link className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => wrapSelection("`", "`")}
                title="Inline code"
              >
                <Code className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => wrapBlock("```", "```")}
                title="Code block"
              >
                <FileCode className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => setImagePickerOpen(true)}
                title="Add image"
              >
                <ImagePlus className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}
        </div>
        <TabsContent value="write" className="mt-1 space-y-1">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="flex min-h-[120px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 resize-y"
            placeholder="Write a bio... (supports markdown)"
          />
        </TabsContent>
        <TabsContent value="preview" className="mt-1">
          <div className="min-h-[120px] rounded-md border border-input bg-background px-3 py-2">
            <div className="flex justify-end mb-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-1.5 text-[10px] text-muted-foreground"
                onClick={() => setShowBadges(!showBadges)}
                title={showBadges ? "Hide image source badges" : "Show image source badges"}
              >
                {showBadges ? <Tags className="h-3 w-3" /> : <Tag className="h-3 w-3" />}
                {showBadges ? "Hide badges" : "Show badges"}
              </Button>
            </div>
            <MarkdownPreview content={value} showBadgesOverride={showBadges} />
          </div>
        </TabsContent>
      </Tabs>
      <ImagePickerDialog
        open={imagePickerOpen}
        onOpenChange={setImagePickerOpen}
        onSelect={insertAtCursor}
      />
    </div>
  );
}

export { MarkdownPreview };
