import { useState } from "react";

import { useRelationshipTypes } from "@/hooks/use-relationships";
import type { RelationshipType } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ColorDot } from "@/components/color-dot";
import { PendingDeleteBadge } from "@/components/pending-delete-badge";
import {
  DeleteTypeDialog,
  EditTypeDialog,
  RelationshipTypeForm,
} from "@/components/relationship-type-dialog";
import { summariseType } from "@/lib/relationship-types";
import { cn } from "@/lib/utils";

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
                  className={cn(
                    "flex items-center justify-between rounded-md border px-3 py-2 text-sm",
                    t.pending_delete_at && "opacity-60",
                  )}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <ColorDot color={t.color} />
                    <div className="min-w-0">
                      <div className="flex min-w-0 items-center gap-2">
                        <p className="font-medium truncate">{t.name}</p>
                        <PendingDeleteBadge
                          finalizeAt={t.pending_delete_at}
                          className="shrink-0"
                        />
                      </div>
                      <p className="text-xs text-muted-foreground truncate">
                        {summariseType(t)}
                      </p>
                    </div>
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
                      disabled={!!t.pending_delete_at}
                      title={
                        t.pending_delete_at
                          ? "Already queued for deletion. Cancel from Settings -> Safety."
                          : undefined
                      }
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
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">New type</CardTitle>
      </CardHeader>
      <CardContent>
        <RelationshipTypeForm />
      </CardContent>
    </Card>
  );
}
