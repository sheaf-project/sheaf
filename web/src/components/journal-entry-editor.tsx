import { type FormEvent, Suspense, lazy, useState } from "react";
import { MemberPillsInput } from "@/components/member-pills-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Member } from "@/types/api";

const BioEditor = lazy(() =>
  import("@/components/bio-editor").then((m) => ({ default: m.BioEditor })),
);

export interface JournalEntryEditorValue {
  title: string;
  body: string;
  authorMemberIds: string[];
}

export function JournalEntryEditor({
  initial,
  members,
  saving,
  onSubmit,
  onCancel,
  submitLabel = "Save",
}: {
  initial?: Partial<JournalEntryEditorValue>;
  members: Member[];
  saving?: boolean;
  onSubmit: (value: JournalEntryEditorValue) => void;
  onCancel?: () => void;
  submitLabel?: string;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [body, setBody] = useState(initial?.body ?? "");
  const [authorMemberIds, setAuthorMemberIds] = useState<string[]>(
    initial?.authorMemberIds ?? [],
  );
  const canSubmit = body.trim().length > 0 && !saving;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    onSubmit({ title: title.trim(), body, authorMemberIds });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="space-y-1">
        <Label className="text-sm">Title (optional)</Label>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Untitled — will use date in lists"
          maxLength={200}
        />
      </div>
      <div className="space-y-1">
        <Label className="text-sm">Authors</Label>
        <MemberPillsInput
          members={members}
          selectedIds={authorMemberIds}
          onChange={setAuthorMemberIds}
          placeholder="Type to search members…"
        />
        <p className="text-xs text-muted-foreground">
          Defaults to whoever is fronting. Empty = account fallback.
        </p>
      </div>
      <div className="space-y-1">
        <Label className="text-sm">Body</Label>
        <Suspense
          fallback={
            <p className="text-sm text-muted-foreground">Loading editor…</p>
          }
        >
          <BioEditor value={body} onChange={setBody} />
        </Suspense>
      </div>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={!canSubmit}>
          {saving ? "Saving…" : submitLabel}
        </Button>
        {onCancel && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onCancel}
            disabled={saving}
          >
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
