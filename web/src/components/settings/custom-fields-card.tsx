import { type FormEvent, type ReactNode, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  useCustomFields,
  useCreateField,
  useUpdateField,
  useDeleteField,
} from "@/hooks/use-custom-fields";
import { getMySystem } from "@/lib/systems";
import { getSystemSafety } from "@/lib/system-safety";
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
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { ApiError } from "@/lib/api-error";
import { showApiErrorToast } from "@/lib/api-errors";
import { cn } from "@/lib/utils";
import { Plus, X } from "lucide-react";
import type {
  CustomField,
  DeleteConfirmation,
  DestructiveConfirm,
  FieldType,
  PrivacyLevel,
} from "@/types/api";

/** Same three words, in the same order, as the member, group and per-edge
 *  privacy selects: "who may see this" is one question, so it gets one
 *  vocabulary. */
const FIELD_PRIVACY_LEVELS: { value: PrivacyLevel; label: string }[] = [
  { value: "private", label: "Private" },
  { value: "friends", label: "Friends only" },
  { value: "public", label: "Public" },
];

/** Permission, never a promise: a public field still has to be added to a
 *  shared view before anyone sees it. The second sentence is the one people
 *  most need, because the control sits next to a per-member editor and the
 *  obvious guess is wrong. */
const FIELD_PRIVACY_HELP =
  "Public means this field can appear on shared views and public profiles, but only on a view you added it to. It applies to this field on every member; there is no per-member setting.";

/**
 * The per-definition privacy setting: the three-level select, the step-up
 * prompt a raise can come back asking for, and the note saying a staged raise
 * has not happened yet.
 *
 * A clone of `RelationshipPrivacyControl`'s shape rather than a reuse of it:
 * that component is wired to an edge (its props, its two mutation hooks and
 * its copy all speak `visibility`), and bending it into a shared control would
 * mean rewriting the surfaces it already serves. Worth doing in one deliberate
 * pass across edges, groups and fields; not worth doing sideways from here.
 */
function FieldPrivacyControl({
  field,
  children,
  trailing,
}: {
  field: CustomField;
  /** The line's own content, to the left of the select. */
  children?: ReactNode;
  /** Extra controls beside the select (the delete button). */
  trailing?: ReactNode;
}) {
  const updateField = useUpdateField();
  const { formatDate } = useDateFormatters();
  // Read only to pick the re-auth tier for a staged raise; both are cached
  // queries the rest of the app already keeps warm.
  const { data: safety } = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const [pendingRaise, setPendingRaise] = useState<{
    privacy: PrivacyLevel;
    tier: DeleteConfirmation;
  } | null>(null);

  /** Move this field to another privacy level.
   *
   * Sent without credentials first: only a raise that would actually put the
   * field in front of someone is answered with a 400 asking for them, so the
   * common case stays a single click and the prompt only appears when it is
   * genuinely a step-up. Lowering is never gated.
   */
  function changePrivacy(next: PrivacyLevel) {
    updateField.mutate(
      { id: field.id, data: { privacy: next }, skipErrorToast: true },
      {
        onError: (err) => {
          if (
            err instanceof ApiError &&
            err.status === 400 &&
            (err.detail === "Password required" ||
              err.detail === "TOTP code required")
          ) {
            setPendingRaise({
              privacy: next,
              tier:
                safety?.settings.auth_tier ??
                system?.delete_confirmation ??
                "password",
            });
            return;
          }
          showApiErrorToast(err, "Couldn't update this field.", { force: true });
        },
      },
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        {children}
        <div className="flex shrink-0 items-center gap-1">
          <Select
            value={field.privacy}
            onValueChange={(v) => changePrivacy(v as PrivacyLevel)}
            disabled={updateField.isPending}
          >
            <SelectTrigger className="h-6 w-28 text-xs" aria-label="Privacy">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FIELD_PRIVACY_LEVELS.map((l) => (
                <SelectItem key={l.value} value={l.value}>
                  {l.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {trailing}
        </div>
      </div>
      {/* A staged raise has not happened yet, and the level shown in the
          select is still the truth until it does. Say both. */}
      {field.privacy_activates_at && (
        <p className="text-[11px] text-amber-600 dark:text-amber-500">
          {field.pending_privacy ?? "public"} - activates{" "}
          {formatDate(field.privacy_activates_at)}. Until then this stays{" "}
          {field.privacy}.
        </p>
      )}
      <DestructiveConfirmDialog
        open={!!pendingRaise}
        onOpenChange={(open) => !open && setPendingRaise(null)}
        title="Confirm public visibility change"
        description="Publishing this field can reveal its value for every member shown through an existing public profile or share link. Confirm now; it takes effect after your System Safety grace period."
        tier={pendingRaise?.tier ?? "none"}
        actionLabel="Confirm change"
        actionLabelLoading="Saving..."
        loading={updateField.isPending}
        onConfirm={(confirm?: DestructiveConfirm) => {
          if (!pendingRaise) return;
          updateField.mutate(
            { id: field.id, data: { privacy: pendingRaise.privacy, ...confirm } },
            { onSuccess: () => setPendingRaise(null) },
          );
        }}
      />
    </div>
  );
}

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
                  "rounded-md border px-3 py-2 text-sm",
                  f.pending_delete_at && "opacity-60",
                )}
              >
                <FieldPrivacyControl
                  field={f}
                  trailing={
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
                  }
                >
                  <span
                    className="min-w-0 cursor-pointer"
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
                </FieldPrivacyControl>
              </div>
            ),
          )}
        </div>
        {fields && fields.length > 0 && (
          <>
            <p className="text-xs text-muted-foreground">
              Click a field name to rename or edit its choices. The field's
              type cannot be changed after creation. Values are set per-member
              in the member editor.
            </p>
            <p className="text-xs text-muted-foreground">
              {FIELD_PRIVACY_HELP}
            </p>
          </>
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
