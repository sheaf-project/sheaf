import { type FormEvent, useState } from "react";

import {
  useCreateRelationshipType,
  useDeleteRelationshipType,
  useUpdateRelationshipType,
} from "@/hooks/use-relationships";
import { RELATIONSHIP_PRESETS } from "@/types/api";
import type {
  RelationshipPreset,
  RelationshipSymmetry,
  RelationshipType,
} from "@/types/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const SYMMETRY_LABELS: { value: RelationshipSymmetry; label: string }[] = [
  { value: "symmetric", label: "Symmetric (one label)" },
  { value: "directional", label: "Directional (two labels)" },
  { value: "either", label: "Either (both / mutual)" },
];

/** What the swatch shows when the type has no colour. A native colour input
 *  has no empty state, so "none" is carried in our own state and the swatch
 *  falls back to this; the Clear button is the only way back to it. */
const NO_COLOR_SWATCH = "#94a3b8";

const HEX7 = /^#[0-9a-f]{6}$/i;

/**
 * The new-relationship-type form, on its own so every screen that needs a type
 * can offer one instead of sending people to Settings and back. Embedded bare
 * in the Settings card, and wrapped by `RelationshipTypeDialog` everywhere
 * else.
 */
export function RelationshipTypeForm({
  idPrefix = "rel",
  onCreated,
  onCancel,
}: {
  /** Distinguishes the field ids when more than one copy could be mounted. */
  idPrefix?: string;
  /** The created type, so a caller can preselect it in its own picker. */
  onCreated?: (type: RelationshipType) => void;
  /** When given, the actions render as a dialog footer with a Cancel. */
  onCancel?: () => void;
}) {
  const createType = useCreateRelationshipType();
  const [presetLabel, setPresetLabel] = useState("");
  const [name, setName] = useState("");
  const [symmetry, setSymmetry] = useState<RelationshipSymmetry>("symmetric");
  const [forwardLabel, setForwardLabel] = useState("");
  const [reverseLabel, setReverseLabel] = useState("");
  // null is a real value here, not "unset": a type may have no colour at all.
  const [color, setColor] = useState<string | null>(null);

  const isSymmetric = symmetry === "symmetric";

  function applyPreset(label: string) {
    setPresetLabel(label);
    const preset: RelationshipPreset | undefined = RELATIONSHIP_PRESETS.find(
      (p) => p.label === label,
    );
    if (!preset) return;
    setName(preset.name);
    setSymmetry(preset.symmetry);
    setForwardLabel(preset.forward_label);
    setReverseLabel(preset.reverse_label ?? "");
    setColor(preset.color ?? null);
  }

  function reset() {
    setPresetLabel("");
    setName("");
    setSymmetry("symmetric");
    setForwardLabel("");
    setReverseLabel("");
    setColor(null);
  }

  const colorValid = color === null || HEX7.test(color);
  const valid =
    name.trim() !== "" &&
    forwardLabel.trim() !== "" &&
    (isSymmetric || reverseLabel.trim() !== "") &&
    colorValid;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    createType.mutate(
      {
        name: name.trim(),
        symmetry,
        forward_label: forwardLabel.trim(),
        reverse_label: isSymmetric ? null : reverseLabel.trim(),
        color,
      },
      {
        onSuccess: (created) => {
          reset();
          onCreated?.(created);
        },
      },
    );
  }

  const actions = (
    <>
      {onCancel && (
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      )}
      <Button type="submit" disabled={createType.isPending || !valid}>
        {createType.isPending ? "Creating..." : "Create type"}
      </Button>
    </>
  );

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor={`${idPrefix}-preset`}>Start from a preset</Label>
        <Select value={presetLabel} onValueChange={applyPreset}>
          <SelectTrigger id={`${idPrefix}-preset`} className="w-full">
            <SelectValue placeholder="Start from a preset..." />
          </SelectTrigger>
          <SelectContent>
            {RELATIONSHIP_PRESETS.map((p) => (
              <SelectItem key={p.label} value={p.label}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${idPrefix}-name`}>Name</Label>
        <Input
          id={`${idPrefix}-name`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Partner"
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${idPrefix}-symmetry`}>Kind</Label>
        <Select
          value={symmetry}
          onValueChange={(v) => setSymmetry(v as RelationshipSymmetry)}
        >
          <SelectTrigger id={`${idPrefix}-symmetry`} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SYMMETRY_LABELS.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${idPrefix}-forward`}>
          {isSymmetric ? "Label" : "Forward label (source side)"}
        </Label>
        <Input
          id={`${idPrefix}-forward`}
          value={forwardLabel}
          onChange={(e) => setForwardLabel(e.target.value)}
          placeholder={isSymmetric ? "e.g. partner" : "e.g. parent"}
          required
        />
      </div>
      {!isSymmetric && (
        <div className="space-y-2">
          <Label htmlFor={`${idPrefix}-reverse`}>
            Reverse label (target side)
          </Label>
          <Input
            id={`${idPrefix}-reverse`}
            value={reverseLabel}
            onChange={(e) => setReverseLabel(e.target.value)}
            placeholder="e.g. child"
            required
          />
        </div>
      )}
      <RelationshipColorField
        idPrefix={idPrefix}
        color={color}
        onChange={setColor}
      />
      {onCancel ? <DialogFooter>{actions}</DialogFooter> : actions}
    </form>
  );
}

/** Optional colour for a type, with a way back to none. Shared with the edit
 *  dialog so setting and clearing a colour work the same in both places. */
export function RelationshipColorField({
  idPrefix,
  color,
  onChange,
}: {
  idPrefix: string;
  color: string | null;
  onChange: (color: string | null) => void;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={`${idPrefix}-color`}>Color</Label>
      <div className="flex items-center gap-2">
        <Input
          id={`${idPrefix}-color`}
          type="color"
          value={color ?? NO_COLOR_SWATCH}
          onChange={(e) => onChange(e.target.value)}
          className="h-10 w-14 p-1"
        />
        <Input
          value={color ?? ""}
          onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
          placeholder="No color"
          className="flex-1"
        />
        {color !== null && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onChange(null)}
          >
            Clear
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Optional. Tints this type wherever it shows up; clear it to go back to
        the plain style.
      </p>
    </div>
  );
}

/**
 * Edit an existing type's labels/colour. One component for both the Settings
 * page and the graph page's Manage types dialog, so the two surfaces cannot
 * drift. Symmetry is fixed at creation: changing it would silently re-read
 * every existing edge of the type, so that stays a delete-and-recreate.
 */
export function EditTypeDialog({
  type,
  onOpenChange,
}: {
  type: RelationshipType;
  onOpenChange: (open: boolean) => void;
}) {
  const updateType = useUpdateRelationshipType();
  const [name, setName] = useState(type.name);
  const [forwardLabel, setForwardLabel] = useState(type.forward_label);
  const [reverseLabel, setReverseLabel] = useState(type.reverse_label ?? "");
  const [color, setColor] = useState<string | null>(type.color);

  const isSymmetric = type.symmetry === "symmetric";
  const valid =
    name.trim() !== "" &&
    forwardLabel.trim() !== "" &&
    (isSymmetric || reverseLabel.trim() !== "") &&
    (color === null || HEX7.test(color));

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    updateType.mutate(
      {
        id: type.id,
        data: {
          name: name.trim(),
          forward_label: forwardLabel.trim(),
          reverse_label: isSymmetric ? null : reverseLabel.trim(),
          // An explicit null really does clear the colour server-side.
          color,
        },
      },
      { onSuccess: () => onOpenChange(false) },
    );
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit relationship type</DialogTitle>
          <DialogDescription>
            The kind ({type.symmetry}) can&apos;t be changed after creation. To
            switch between symmetric and directional, delete this type and make
            a new one.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="rel-edit-name">Name</Label>
            <Input
              id="rel-edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="rel-edit-forward">
              {isSymmetric ? "Label" : "Forward label (source side)"}
            </Label>
            <Input
              id="rel-edit-forward"
              value={forwardLabel}
              onChange={(e) => setForwardLabel(e.target.value)}
              required
            />
          </div>
          {!isSymmetric && (
            <div className="space-y-2">
              <Label htmlFor="rel-edit-reverse">
                Reverse label (target side)
              </Label>
              <Input
                id="rel-edit-reverse"
                value={reverseLabel}
                onChange={(e) => setReverseLabel(e.target.value)}
                required
              />
            </div>
          )}
          <RelationshipColorField
            idPrefix="rel-edit"
            color={color}
            onChange={setColor}
          />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={updateType.isPending || !valid}>
              {updateType.isPending ? "Saving..." : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Confirm-and-delete for a type. Shared like EditTypeDialog, and blunt about
 *  the blast radius: deleting a type removes every edge drawn with it. */
export function DeleteTypeDialog({
  type,
  onOpenChange,
}: {
  type: RelationshipType;
  onOpenChange: (open: boolean) => void;
}) {
  const deleteType = useDeleteRelationshipType();

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete relationship type</DialogTitle>
          <DialogDescription>
            Delete &quot;{type.name}&quot;? This also removes every relationship
            between members or groups that uses this type. This cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() =>
              deleteType.mutate(type.id, {
                onSuccess: () => onOpenChange(false),
              })
            }
            disabled={deleteType.isPending}
          >
            {deleteType.isPending ? "Deleting..." : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The same form as a dialog, for the screens where relationship types are
 *  used rather than managed. */
export function RelationshipTypeDialog({
  onOpenChange,
  onCreated,
}: {
  onOpenChange: (open: boolean) => void;
  onCreated?: (type: RelationshipType) => void;
}) {
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New relationship type</DialogTitle>
          <DialogDescription>
            A type is the vocabulary for your relationships (partner,
            parent/child, protector). Create one here and it is ready to use
            straight away.
          </DialogDescription>
        </DialogHeader>
        <RelationshipTypeForm
          idPrefix="rel-dialog"
          onCancel={() => onOpenChange(false)}
          onCreated={(type) => {
            onCreated?.(type);
            onOpenChange(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
