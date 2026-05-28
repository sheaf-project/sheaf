import { type FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useCustomFields, useCreateField, useUpdateField, useDeleteField } from "@/hooks/use-custom-fields";
import { getMySystem } from "@/lib/systems";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PendingDeleteBadge } from "@/components/pending-delete-badge";
import { cn } from "@/lib/utils";
import type { FieldType } from "@/types/api";

export function CustomFieldsCard() {
  const { data: fields } = useCustomFields();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const createField = useCreateField();
  const updateField = useUpdateField();
  const deleteField = useDeleteField();
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<FieldType>("text");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [deletingField, setDeletingField] =
    useState<{ id: string; name: string } | null>(null);

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!newName) return;
    createField.mutate(
      { name: newName, field_type: newType },
      { onSuccess: () => { setNewName(""); setNewType("text"); } },
    );
  }

  function startEdit(field: { id: string; name: string }) {
    setEditingId(field.id);
    setEditName(field.name);
  }

  function handleUpdate(e: FormEvent) {
    e.preventDefault();
    if (!editingId) return;
    updateField.mutate(
      { id: editingId, data: { name: editName } },
      { onSuccess: () => setEditingId(null) },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Custom fields</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={handleCreate} className="flex items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label htmlFor="new-custom-field-name" className="text-xs">Field name</Label>
            <Input
              id="new-custom-field-name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. Species, Role"
            />
          </div>
          <Select value={newType} onValueChange={(v) => setNewType(v as FieldType)}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="text">Text</SelectItem>
              <SelectItem value="number">Number</SelectItem>
              <SelectItem value="date">Date</SelectItem>
              <SelectItem value="boolean">Yes/No</SelectItem>
            </SelectContent>
          </Select>
          <Button type="submit" size="sm" disabled={createField.isPending || !newName}>
            Add
          </Button>
        </form>

        <div className="space-y-2">
          {fields?.map((f) =>
            editingId === f.id ? (
              <form
                key={f.id}
                onSubmit={handleUpdate}
                className="flex items-center gap-2"
              >
                <Input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="h-8 flex-1 text-sm"
                />
                <Button type="submit" size="sm" variant="ghost" className="h-8 px-2 text-xs">
                  Save
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-8 px-2 text-xs"
                  onClick={() => setEditingId(null)}
                >
                  Cancel
                </Button>
              </form>
            ) : (
              <div
                key={f.id}
                className={cn(
                  "flex items-center justify-between rounded-md border px-3 py-2 text-sm",
                  f.pending_delete_at && "opacity-60",
                )}
              >
                <span className="cursor-pointer" onClick={() => startEdit(f)}>
                  {f.name}
                  <span className="ml-2 text-xs text-muted-foreground">{f.field_type}</span>
                  <PendingDeleteBadge
                    finalizeAt={f.pending_delete_at}
                    className="ml-2"
                  />
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                  onClick={() => setDeletingField({ id: f.id, name: f.name })}
                >
                  Delete
                </Button>
              </div>
            ),
          )}
        </div>
        {fields && fields.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Click a field name to rename it. Values are set per-member in the member editor.
          </p>
        )}
      </CardContent>
      <DestructiveConfirmDialog
        open={!!deletingField}
        onOpenChange={(open) => !open && setDeletingField(null)}
        title="Delete custom field"
        description={`Are you sure you want to delete "${deletingField?.name}"? All values set on members will be lost.`}
        tier={system?.delete_confirmation ?? "none"}
        onConfirm={(confirm) =>
          deletingField &&
          deleteField.mutate(
            { id: deletingField.id, confirm },
            { onSuccess: () => setDeletingField(null) },
          )
        }
        loading={deleteField.isPending}
      />
    </Card>
  );
}
