import { type FormEvent, type ReactNode, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  useCustomFields,
  useCreateField,
  useUpdateField,
  useDeleteField,
  useReorderFields,
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PendingDeleteBadge } from "@/components/pending-delete-badge";
import { SliderReadout } from "@/components/slider-field";
import { sliderOptions } from "@/lib/slider-fields";
import {
  PrivacyLevelSelect,
  PublishingOffNote,
} from "@/components/privacy-level-select";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { isStepUpRequiredError, showApiErrorToast } from "@/lib/api-errors";
import { cn } from "@/lib/utils";
import { ChevronDown, ChevronUp, Plus, X } from "lucide-react";
import type {
  CustomField,
  DeleteConfirmation,
  DestructiveConfirm,
  FieldType,
  PrivacyLevel,
  SliderOptions,
} from "@/types/api";

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
          if (isStepUpRequiredError(err)) {
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
          {/* Read-modify-write straight onto the stored definition, so the
              select's value IS the saved level - a field already public keeps
              Public offered and can still be lowered. */}
          <PrivacyLevelSelect
            value={field.privacy}
            onValueChange={changePrivacy}
            savedValue={field.privacy}
            disabled={updateField.isPending}
            className="h-6 w-28 text-xs"
            ariaLabel="Privacy"
          />
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
        description="Publishing this field can reveal its value for every member shown through an existing public profile or share link. Confirm now; if you have a grace period set, it takes effect after your System Safety window."
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
  slider: "Slider",
};

const FIELD_TYPES_WITH_CHOICES: ReadonlySet<FieldType> = new Set([
  "select",
  "multiselect",
]);

/** Gradients offered by name. Two or three stops each; the owner can also
 *  pick their own colours. Ids are what the options store, so an import from
 *  an app with the same idea can land on a named one. */
const GRADIENT_PRESETS: { id: string; label: string; stops: string[] }[] = [
  { id: "none", label: "No gradient", stops: [] },
  { id: "cool-warm", label: "Cool to warm", stops: ["#5B92C9", "#E08D5E"] },
  { id: "calm-intense", label: "Calm to intense", stops: ["#7BAA7E", "#D6534D"] },
  { id: "sad-happy", label: "Low to high", stops: ["#5479AE", "#E4C44F"] },
  { id: "soft-hard", label: "Soft to hard", stops: ["#F4EBD8", "#A89B8C", "#3A2E4D"] },
  { id: "day-night", label: "Day to night", stops: ["#F4D86E", "#1E2444"] },
  { id: "rose-dusk", label: "Rose dusk", stops: ["#F4B89E", "#D17A8E", "#5B4378"] },
  { id: "sage-meadow", label: "Sage meadow", stops: ["#C5D9B5", "#4A6B47"] },
  { id: "monochrome", label: "Monochrome", stops: ["#E4DEEC", "#3A2E4D"] },
  { id: "custom", label: "Custom colours", stops: [] },
];

function presetFor(gradient: string[] | null): string {
  if (!gradient || gradient.length === 0) return "none";
  const match = GRADIENT_PRESETS.find(
    (p) => p.stops.length > 0 && p.stops.join(",") === gradient.map((c) => c.toUpperCase()).join(","),
  );
  return match ? match.id : "custom";
}

/** What the API accepts: the labeled kind has fixed bounds and no unit, so
 *  only send the parts that mean something for the chosen mode. */
function sliderOptionsForWire(o: SliderOptions): Record<string, unknown> {
  const labels = {
    left: o.labels.left?.trim() || null,
    center: o.labels.center?.trim() || null,
    right: o.labels.right?.trim() || null,
  };
  const gradient = o.gradient && o.gradient.length >= 2 ? o.gradient : null;
  if (o.mode === "numeric") {
    return {
      mode: "numeric",
      min: o.min,
      max: o.max,
      step: o.step,
      unit: o.unit?.trim() || null,
      labels,
      gradient,
    };
  }
  return { mode: "labeled", labels, gradient };
}

function defaultSliderOptions(): SliderOptions {
  return {
    mode: "labeled",
    min: 0,
    max: 100,
    step: null,
    unit: null,
    labels: { left: null, center: null, right: null },
    gradient: null,
  };
}

/** Everything a slider field needs: mode, bounds, labels, track colours. */
function SliderOptionsEditor({
  value,
  onChange,
  idPrefix,
}: {
  value: SliderOptions;
  onChange: (next: SliderOptions) => void;
  idPrefix: string;
}) {
  // "Custom colours" starts from whatever stops are showing, so without this
  // flag the picker would snap straight back to the preset those stops match
  // and the swatches would never appear.
  const [customColours, setCustomColours] = useState(false);
  const preset = customColours && value.gradient ? "custom" : presetFor(value.gradient);
  const patch = (p: Partial<SliderOptions>) => onChange({ ...value, ...p });
  const patchLabels = (side: "left" | "center" | "right", text: string) =>
    patch({ labels: { ...value.labels, [side]: text.trim() ? text : null } });
  const numeric = value.mode === "numeric";
  const previewOptions = sliderOptions(value);

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="text-xs">Kind</Label>
        <RadioGroup
          value={value.mode}
          onValueChange={(m) =>
            patch(
              m === "numeric"
                ? { mode: "numeric", min: 0, max: 10, step: null }
                : { mode: "labeled", min: 0, max: 100, step: null, unit: null },
            )
          }
          className="flex flex-wrap gap-4"
        >
          <div className="flex items-center gap-2">
            <RadioGroupItem value="labeled" id={`${idPrefix}-slider-labeled`} />
            <Label htmlFor={`${idPrefix}-slider-labeled`} className="text-xs font-normal">
              Between two labels (0 to 100)
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <RadioGroupItem value="numeric" id={`${idPrefix}-slider-numeric`} />
            <Label htmlFor={`${idPrefix}-slider-numeric`} className="text-xs font-normal">
              A number with a range
            </Label>
          </div>
        </RadioGroup>
      </div>

      {numeric ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="space-y-1">
            <Label htmlFor={`${idPrefix}-slider-min`} className="text-xs">Min</Label>
            <Input
              id={`${idPrefix}-slider-min`}
              type="number"
              className="h-8 text-sm"
              value={value.min}
              onChange={(e) => patch({ min: Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${idPrefix}-slider-max`} className="text-xs">Max</Label>
            <Input
              id={`${idPrefix}-slider-max`}
              type="number"
              className="h-8 text-sm"
              value={value.max}
              onChange={(e) => patch({ max: Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${idPrefix}-slider-step`} className="text-xs">Step</Label>
            <Input
              id={`${idPrefix}-slider-step`}
              type="number"
              className="h-8 text-sm"
              placeholder="any"
              min={0}
              value={value.step ?? ""}
              onChange={(e) =>
                patch({ step: e.target.value === "" ? null : Number(e.target.value) })
              }
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${idPrefix}-slider-unit`} className="text-xs">Unit</Label>
            <Input
              id={`${idPrefix}-slider-unit`}
              className="h-8 text-sm"
              placeholder="e.g. cm"
              maxLength={12}
              value={value.unit ?? ""}
              onChange={(e) => patch({ unit: e.target.value.trim() ? e.target.value : null })}
            />
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-3 gap-2">
        {(["left", "center", "right"] as const).map((side) => (
          <div key={side} className="space-y-1">
            <Label htmlFor={`${idPrefix}-slider-label-${side}`} className="text-xs">
              {side === "left" ? "Left label" : side === "center" ? "Centre label" : "Right label"}
            </Label>
            <Input
              id={`${idPrefix}-slider-label-${side}`}
              className="h-8 text-sm"
              maxLength={50}
              placeholder={side === "center" ? "optional" : numeric ? "optional" : side === "left" ? "e.g. Calm" : "e.g. Intense"}
              value={value.labels[side] ?? ""}
              onChange={(e) => patchLabels(side, e.target.value)}
            />
          </div>
        ))}
      </div>

      <div className="space-y-1">
        <Label htmlFor={`${idPrefix}-slider-gradient`} className="text-xs">Track colours</Label>
        <Select
          value={preset}
          onValueChange={(id) => {
            setCustomColours(id === "custom");
            if (id === "none") patch({ gradient: null });
            else if (id === "custom") patch({ gradient: value.gradient ?? ["#5B92C9", "#E08D5E"] });
            else patch({ gradient: GRADIENT_PRESETS.find((p) => p.id === id)?.stops ?? null });
          }}
        >
          <SelectTrigger id={`${idPrefix}-slider-gradient`} className="h-8 w-56 text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {GRADIENT_PRESETS.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                <span className="flex items-center gap-2">
                  {p.stops.length > 0 && (
                    <span
                      className="inline-block h-3 w-10 rounded-full"
                      style={{ background: `linear-gradient(to right, ${p.stops.join(", ")})` }}
                    />
                  )}
                  {p.label}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {preset === "custom" && value.gradient && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {value.gradient.map((colour, i) => (
              <span key={i} className="flex items-center gap-1">
                <input
                  type="color"
                  aria-label={`Colour ${i + 1}`}
                  value={colour}
                  onChange={(e) => {
                    const next = [...value.gradient!];
                    next[i] = e.target.value.toUpperCase();
                    patch({ gradient: next });
                  }}
                  className="h-8 w-10 cursor-pointer rounded border bg-transparent p-0.5"
                />
                {value.gradient!.length > 2 && (
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-foreground"
                    aria-label={`Remove colour ${i + 1}`}
                    onClick={() => patch({ gradient: value.gradient!.filter((_, j) => j !== i) })}
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </span>
            ))}
            {value.gradient.length < 8 && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => patch({ gradient: [...value.gradient!, "#888888"] })}
              >
                <Plus className="mr-1 h-3 w-3" />
                Add colour
              </Button>
            )}
          </div>
        )}
      </div>

      <div className="space-y-1">
        <Label className="text-xs">Preview</Label>
        <SliderReadout
          value={previewOptions.min + (previewOptions.max - previewOptions.min) * 0.62}
          options={previewOptions}
        />
      </div>
    </div>
  );
}

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
  const reorderFields = useReorderFields();
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<FieldType>("text");
  const [newChoices, setNewChoices] = useState<string[]>([]);
  const [newSlider, setNewSlider] = useState<SliderOptions>(defaultSliderOptions);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editChoices, setEditChoices] = useState<string[]>([]);
  const [editSlider, setEditSlider] = useState<SliderOptions>(defaultSliderOptions);
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
    } else if (newType === "slider") {
      body.options = sliderOptionsForWire(newSlider);
    }
    createField.mutate(body, {
      onSuccess: () => {
        setNewName("");
        setNewType("text");
        setNewChoices([]);
        setNewSlider(defaultSliderOptions());
      },
    });
  }

  function startEdit(field: CustomField) {
    setEditingId(field.id);
    setEditName(field.name);
    setEditType(field.field_type);
    setEditChoices(choicesFromOptions(field.options));
    setEditSlider(field.field_type === "slider" ? sliderOptions(field.options) : defaultSliderOptions());
  }

  /** Swap a field with its neighbour and send the FULL ordered id list, so
   *  the server's order = list-index assignment reproduces exactly what is
   *  on screen. */
  function moveField(index: number, delta: -1 | 1) {
    if (!fields) return;
    const j = index + delta;
    if (j < 0 || j >= fields.length) return;
    const ids = fields.map((f) => f.id);
    [ids[index], ids[j]] = [ids[j], ids[index]];
    reorderFields.mutate(ids);
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
    } else if (editType === "slider") {
      data.options = sliderOptionsForWire(editSlider);
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
          {newType === "slider" && (
            <div className="rounded-md border bg-muted/30 p-3">
              <SliderOptionsEditor idPrefix="new" value={newSlider} onChange={setNewSlider} />
            </div>
          )}
        </form>

        <div className="space-y-2">
          {fields?.map((f, i) =>
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
                {editType === "slider" && (
                  <SliderOptionsEditor
                    idPrefix={`edit-${f.id}`}
                    value={editSlider}
                    onChange={setEditSlider}
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
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        disabled={i === 0 || reorderFields.isPending}
                        onClick={() => moveField(i, -1)}
                        aria-label="Move up"
                      >
                        <ChevronUp className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        disabled={
                          i === (fields?.length ?? 0) - 1 ||
                          reorderFields.isPending
                        }
                        onClick={() => moveField(i, 1)}
                        aria-label="Move down"
                      >
                        <ChevronDown className="h-3.5 w-3.5" />
                      </Button>
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
                    </>
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
            {/* Once for the whole list, where the field help already lives,
                rather than repeated under every row. A list can be mixed - a
                field already public keeps Public selectable in its own row -
                and the note's second sentence is what covers that case. */}
            <PublishingOffNote />
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
