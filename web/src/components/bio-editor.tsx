import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { uploadFile } from "@/lib/files";
import { useShowImageBadges } from "@/hooks/use-preferences";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ImagePlus, Bold, Italic, Link, Code, Tags, Tag } from "lucide-react";

function isHostedImage(src: string) {
  return src.startsWith("/v1/files/serve/");
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

  if (!content) {
    return <p className="text-sm text-muted-foreground italic">Nothing here yet.</p>;
  }

  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img: ({ src, alt, ...props }) => {
            const hosted = src ? isHostedImage(src) : false;
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
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [tab, setTab] = useState<string>("write");
  const [showBadges, setShowBadges] = useShowImageBadges();

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
    // Restore cursor position after the inserted text
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

  const [uploadError, setUploadError] = useState("");

  async function handleImageUpload(file: File) {
    setUploadError("");
    setUploading(true);
    try {
      const res = await uploadFile(file);
      insertAtCursor(`![image](${res.url})`);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleImageUpload(file);
    e.target.value = "";
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
            <div className="flex gap-0.5">
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
                title="Code"
              >
                <Code className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                title="Upload image"
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
          {uploadError && (
            <p className="text-xs text-destructive">{uploadError}</p>
          )}
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
      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp"
        className="hidden"
        onChange={handleFileChange}
      />
    </div>
  );
}

export { MarkdownPreview };
