import { type FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  useCustomFields,
  useCreateField,
  useUpdateField,
  useDeleteField,
} from "@/hooks/use-custom-fields";
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
import { Plus, X } from "lucide-react";
import type { CustomField, FieldType } from "@/types/api";

const FIELD_TYPE_LABEL: Record<FieldType, string> = {
  text: "Text",
  number: "Number",
  date: "Date",
  boolean: "Yes/No",
  select: "Select (single)",
  multiselect: "Multi-select",
};

const FIELD_TYPES_WITH_CHOICES: ReadonlySet<FieldType> = new Set([
  "select",
  "multiselect",
]);

function choicesFromOptions(
  options: CustomField["options"] | null | undefined,
): string[] {
  if (!options) return [];
  const raw = (options as { choices?: unknown }).choices;
  return Array.isArray(raw) ? (raw.filter((c) => typeof c === "string") as string[]) : [];
}

/** Choices editor: append / edit / remove. Used both in the create form
 *  and the rename-+-edit inline form. */
function ChoicesEditor({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">Choices</Label>
      <div className="space-y-1">
        {value.map((choice, i) => (
          <div key={i} className="flex items-center gap-1">
            <Input
              value={choice}
              onChange={(e) => {
                const next = [...value];
                next[i] = e.target.value;
                onChange(next);
              }}
              placeholder={`Option ${i + 1}`}
              className="h-8 text-sm"
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0"
              onClick={() => onChange(value.filter((_, j) => j !== i))}
              aria-label="Remove choice"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={() => onChange([...value, ""])}
        >
          <Plus className="h-3 w-3 mr-1" />
          Add choice
        </Button>
      </div>
    </div>
  );
}

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
  const [newChoices, setNewChoices] = useState<string[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editChoices, setEditChoices] = useState<string[]>([]);
  const [editType, setEditType] = useState<FieldType>("text");
  const [deletingField, setDeletingField] = useState<{
    id: string;
    name: string;
  } | null>(null);

  function selectNewType(value: FieldType) {
    // Reset the choices buffer when the user picks a non-choices type
    // so a stale list doesn't ride through to a submit with the wrong
    // field type.
    setNewType(value);
    if (!FIELD_TYPES_WITH_CHOICES.has(value)) setNewChoices([]);
  }

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!newName) return;
    const body: Parameters<typeof createField.mutate>[0] = {
      name: newName,
      field_type: newType,
    };
    if (FIELD_TYPES_WITH_CHOICES.has(newType)) {
      const trimmed = newChoices.map((c) => c.trim()).filter(Boolean);
      // Send choices when present; omit `options` entirely to opt into
      // freeform mode (matches the mobile / backend default).
      if (trimmed.length > 0) {
        body.options = { choices: trimmed };
      }
    }
    createField.mutate(body, {
      onSuccess: () => {
        setNewName("");
        setNewType("text");
        setNewChoices([]);
      },
    });
  }

  function startEdit(field: CustomField) {
    setEditingId(field.id);
    setEditName(field.name);
    setEditType(field.field_type);
    setEditChoices(choicesFromOptions(field.options));
  }

  function handleUpdate(e: FormEvent) {
    e.preventDefault();
    if (!editingId) return;
    const data: Parameters<typeof updateField.mutate>[0]["data"] = {
      name: editName,
    };
    if (FIELD_TYPES_WITH_CHOICES.has(editType)) {
      const trimmed = editChoices.map((c) => c.trim()).filter(Boolean);
      data.options = trimmed.length > 0 ? { choices: trimmed } : null;
    }
    updateField.mutate(
      { id: editingId, data },
      { onSuccess: () => setEditingId(null) },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Custom fields</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={handleCreate} className="space-y-2">
          <div className="flex items-end gap-2">
            <div className="flex-1 space-y-1">
              <Label htmlFor="new-custom-field-name" className="text-xs">
                Field name
              </Label>
              <Input
                id="new-custom-field-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Species, Role"
              />
            </div>
            <Select
              value={newType}
              onValueChange={(v) => selectNewType(v as FieldType)}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.entries(FIELD_TYPE_LABEL) as [FieldType, string][]).map(
                  ([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ),
                )}
              </SelectContent>
            </Select>
            <Button
              type="submit"
              size="sm"
              disabled={createField.isPending || !newName}
            >
              Add
            </Button>
          </div>
          {FIELD_TYPES_WITH_CHOICES.has(newType) && (
            <div className="rounded-md border bg-muted/30 p-3">
              <ChoicesEditor value={newChoices} onChange={setNewChoices} />
              <p className="mt-2 text-xs text-muted-foreground">
                Leave empty for freeform values (any text accepted). When set,
                only the listed choices are valid.
              </p>
            </div>
          )}
        </form>

        <div className="space-y-2">
          {fields?.map((f) =>
            editingId === f.id ? (
              <form
                key={f.id}
                onSubmit={handleUpdate}
                className="space-y-2 rounded-md border p-2"
              >
                <div className="flex items-center gap-2">
                  <Input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="h-8 flex-1 text-sm"
                  />
                  <Button
                    type="submit"
                    size="sm"
                    variant="ghost"
                    className="h-8 px-2 text-xs"
                  >
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
                </div>
                {FIELD_TYPES_WITH_CHOICES.has(editType) && (
                  <ChoicesEditor
                    value={editChoices}
                    onChange={setEditChoices}
                  />
                )}
              </form>
            ) : (
              <div
                key={f.id}
                className={cn(
                  "flex items-center justify-between rounded-md border px-3 py-2 text-sm",
                  f.pending_delete_at && "opacity-60",
                )}
              >
                <span
                  className="cursor-pointer"
                  onClick={() => startEdit(f)}
                >
                  {f.name}
                  <span className="ml-2 text-xs text-muted-foreground">
                    {FIELD_TYPE_LABEL[f.field_type] ?? f.field_type}
                  </span>
                  {FIELD_TYPES_WITH_CHOICES.has(f.field_type) && (
                    <span className="ml-2 text-xs text-muted-foreground">
                      {choicesFromOptions(f.options).length > 0
                        ? `· ${choicesFromOptions(f.options).length} choice${
                            choicesFromOptions(f.options).length === 1
                              ? ""
                              : "s"
                          }`
                        : "· freeform"}
                    </span>
                  )}
                  <PendingDeleteBadge
                    finalizeAt={f.pending_delete_at}
                    className="ml-2"
                  />
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                  onClick={() =>
                    setDeletingField({ id: f.id, name: f.name })
                  }
                >
                  Delete
                </Button>
              </div>
            ),
          )}
        </div>
        {fields && fields.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Click a field name to rename or edit its choices. The field's
            type cannot be changed after creation. Values are set per-member
            in the member editor.
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
