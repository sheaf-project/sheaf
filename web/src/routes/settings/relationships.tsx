import { type FormEvent, useState } from "react";

import {
  useRelationshipTypes,
  useCreateRelationshipType,
  useUpdateRelationshipType,
  useDeleteRelationshipType,
} from "@/hooks/use-relationships";
import { RELATIONSHIP_PRESETS } from "@/types/api";
import type {
  RelationshipPreset,
  RelationshipSymmetry,
  RelationshipType,
} from "@/types/api";
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

/** A one-line, human-readable summary of how a type reads. */
function summariseType(t: {
  symmetry: RelationshipSymmetry;
  forward_label: string;
  reverse_label: string | null;
}): string {
  if (t.symmetry === "symmetric") return t.forward_label;
  return `${t.forward_label} -> ${t.reverse_label ?? "?"}`;
}

export function SettingsRelationshipsPage() {
  const { data: types } = useRelationshipTypes();
  const [editing, setEditing] = useState<RelationshipType | null>(null);
  const [deleting, setDeleting] = useState<RelationshipType | null>(null);

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Relationship types</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Define the kinds of relationship you can draw between members or
            between groups (e.g. partner, parent/child, protector). Symmetric
            types read the same both ways; directional and &quot;either&quot;
            types have a separate label for each end.
          </p>
          {types && types.length > 0 ? (
            <div className="space-y-2">
              {types.map((t) => (
                <div
                  key={t.id}
                  className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                >
                  <div className="min-w-0">
                    <p className="font-medium truncate">{t.name}</p>
                    <p className="text-xs text-muted-foreground truncate">
                      {summariseType(t)}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => setEditing(t)}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-xs text-destructive hover:text-destructive"
                      onClick={() => setDeleting(t)}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              No relationship types yet. Add one below to start linking members
              and groups.
            </p>
          )}
        </CardContent>
      </Card>

      <NewTypeCard />

      {editing && (
        <EditTypeDialog
          type={editing}
          onOpenChange={(open) => !open && setEditing(null)}
        />
      )}

      {deleting && (
        <DeleteTypeDialog
          type={deleting}
          onOpenChange={(open) => !open && setDeleting(null)}
        />
      )}
    </>
  );
}

function NewTypeCard() {
  const createType = useCreateRelationshipType();
  const [presetLabel, setPresetLabel] = useState("");
  const [name, setName] = useState("");
  const [symmetry, setSymmetry] = useState<RelationshipSymmetry>("symmetric");
  const [forwardLabel, setForwardLabel] = useState("");
  const [reverseLabel, setReverseLabel] = useState("");

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
  }

  function reset() {
    setPresetLabel("");
    setName("");
    setSymmetry("symmetric");
    setForwardLabel("");
    setReverseLabel("");
  }

  const valid =
    name.trim() !== "" &&
    forwardLabel.trim() !== "" &&
    (isSymmetric || reverseLabel.trim() !== "");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    createType.mutate(
      {
        name: name.trim(),
        symmetry,
        forward_label: forwardLabel.trim(),
        reverse_label: isSymmetric ? null : reverseLabel.trim(),
      },
      { onSuccess: reset },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">New type</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="rel-preset">Start from a preset</Label>
            <Select value={presetLabel} onValueChange={applyPreset}>
              <SelectTrigger id="rel-preset" className="w-full">
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
            <Label htmlFor="rel-name">Name</Label>
            <Input
              id="rel-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Partner"
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="rel-symmetry">Kind</Label>
            <Select
              value={symmetry}
              onValueChange={(v) => setSymmetry(v as RelationshipSymmetry)}
            >
              <SelectTrigger id="rel-symmetry" className="w-full">
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
            <Label htmlFor="rel-forward">
              {isSymmetric ? "Label" : "Forward label (source side)"}
            </Label>
            <Input
              id="rel-forward"
              value={forwardLabel}
              onChange={(e) => setForwardLabel(e.target.value)}
              placeholder={isSymmetric ? "e.g. partner" : "e.g. parent"}
              required
            />
          </div>
          {!isSymmetric && (
            <div className="space-y-2">
              <Label htmlFor="rel-reverse">Reverse label (target side)</Label>
              <Input
                id="rel-reverse"
                value={reverseLabel}
                onChange={(e) => setReverseLabel(e.target.value)}
                placeholder="e.g. child"
                required
              />
            </div>
          )}
          <Button type="submit" disabled={createType.isPending || !valid}>
            {createType.isPending ? "Creating..." : "Create type"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function EditTypeDialog({
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

  const isSymmetric = type.symmetry === "symmetric";
  const valid =
    name.trim() !== "" &&
    forwardLabel.trim() !== "" &&
    (isSymmetric || reverseLabel.trim() !== "");

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

function DeleteTypeDialog({
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
