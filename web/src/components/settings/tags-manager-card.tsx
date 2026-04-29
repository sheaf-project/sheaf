import { type FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTags, useCreateTag, useUpdateTag, useDeleteTag } from "@/hooks/use-tags";
import { getMySystem } from "@/lib/systems";
import { ColorDot } from "@/components/color-dot";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";

export function TagsManagerCard() {
  const { data: tags } = useTags();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const createTag = useCreateTag();
  const updateTag = useUpdateTag();
  const deleteTag = useDeleteTag();
  const [newName, setNewName] = useState("");
  const [newColor, setNewColor] = useState("#10b981");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editColor, setEditColor] = useState("");
  const [deletingTag, setDeletingTag] =
    useState<{ id: string; name: string } | null>(null);

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!newName) return;
    createTag.mutate(
      { name: newName, color: newColor || null },
      { onSuccess: () => { setNewName(""); setNewColor("#10b981"); } },
    );
  }

  function startEdit(tag: { id: string; name: string; color: string | null }) {
    setEditingId(tag.id);
    setEditName(tag.name);
    setEditColor(tag.color ?? "#10b981");
  }

  function handleUpdate(e: FormEvent) {
    e.preventDefault();
    if (!editingId) return;
    updateTag.mutate(
      { id: editingId, data: { name: editName, color: editColor || null } },
      { onSuccess: () => setEditingId(null) },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Tags</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={handleCreate} className="flex items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label className="text-xs">New tag</Label>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Tag name"
            />
          </div>
          <Input
            type="color"
            value={newColor}
            onChange={(e) => setNewColor(e.target.value)}
            className="h-10 w-14 p-1"
          />
          <Button type="submit" size="sm" disabled={createTag.isPending || !newName}>
            Add
          </Button>
        </form>

        <div className="flex flex-wrap gap-2">
          {tags?.map((t) =>
            editingId === t.id ? (
              <form
                key={t.id}
                onSubmit={handleUpdate}
                className="flex items-center gap-1"
              >
                <Input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="h-7 w-24 text-xs"
                />
                <Input
                  type="color"
                  value={editColor}
                  onChange={(e) => setEditColor(e.target.value)}
                  className="h-7 w-10 p-0.5"
                />
                <Button type="submit" size="sm" variant="ghost" className="h-7 px-2 text-xs">
                  Save
                </Button>
              </form>
            ) : (
              <Badge
                key={t.id}
                variant="outline"
                className="cursor-pointer gap-1.5"
                onClick={() => startEdit(t)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setDeletingTag({ id: t.id, name: t.name });
                }}
              >
                <ColorDot color={t.color} />
                {t.name}
              </Badge>
            ),
          )}
        </div>
        {tags && tags.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Click to edit. Right-click to delete.
          </p>
        )}
      </CardContent>
      <DestructiveConfirmDialog
        open={!!deletingTag}
        onOpenChange={(open) => !open && setDeletingTag(null)}
        title="Delete tag"
        description={`Are you sure you want to delete "${deletingTag?.name}"?`}
        tier={system?.delete_confirmation ?? "none"}
        onConfirm={(confirm) =>
          deletingTag &&
          deleteTag.mutate(
            { id: deletingTag.id, confirm },
            { onSuccess: () => setDeletingTag(null) },
          )
        }
        loading={deleteTag.isPending}
      />
    </Card>
  );
}
