import { Button } from "@/components/ui/button";
import type { PendingAction, PendingActionType } from "@/types/api";

const actionLabels: Record<PendingActionType, string> = {
  member_delete: "Delete member",
  group_delete: "Delete group",
  tag_delete: "Delete tag",
  field_delete: "Delete custom field",
  front_delete: "Delete front entry",
};

function timeRemaining(finalizeAfter: string): string {
  const target = new Date(finalizeAfter).getTime();
  const now = Date.now();
  const ms = target - now;
  if (ms <= 0) return "finalizing…";
  const hours = Math.ceil(ms / 3_600_000);
  if (hours < 24) return `in ${hours}h`;
  const days = Math.ceil(ms / 86_400_000);
  return `in ${days} day${days === 1 ? "" : "s"}`;
}

export function PendingActionRow({
  action,
  onCancel,
  cancelling,
}: {
  action: PendingAction;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const label = actionLabels[action.action_type] ?? action.action_type;
  const fronting = action.fronting_member_names;

  return (
    <div className="flex items-start gap-3 rounded-md border px-3 py-2 text-sm">
      <div className="flex-1 min-w-0 space-y-0.5">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">{label}</span>
          <span className="text-muted-foreground truncate">
            {action.target_label}
          </span>
        </div>
        <div className="text-xs text-muted-foreground">
          Finalizes {timeRemaining(action.finalize_after)}
          {fronting.length > 0 && (
            <> · {fronting.join(", ")} {fronting.length === 1 ? "was" : "were"} fronting</>
          )}
        </div>
      </div>
      <Button
        variant="outline"
        size="sm"
        className="h-7 text-xs"
        onClick={onCancel}
        disabled={cancelling}
      >
        {cancelling ? "Cancelling…" : "Cancel"}
      </Button>
    </div>
  );
}
