import { Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useGroups } from "@/hooks/use-groups";
import { useMembers } from "@/hooks/use-members";
import type {
  GroupRuleSpec,
  IncludePrivate,
  MemberRuleSpec,
  NotificationChannel,
  RuleAction,
} from "@/types/api";

// ---------- Layer 1 -------------------------------------------------------

export function L1Card({
  channel,
  onChange,
}: {
  channel: NotificationChannel;
  onChange: (patch: Partial<NotificationChannel>) => void;
}) {
  const value = channel.base_all_members ? "all" : "none";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Base set</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <RadioGroup
          value={value}
          onValueChange={(v) =>
            onChange({
              base_all_members: v === "all",
              ...(v === "none" ? { base_include_private: false } : {}),
            })
          }
        >
          <label className="flex items-center gap-2 cursor-pointer">
            <RadioGroupItem value="all" id="l1-all" />
            <span className="text-sm">All members</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <RadioGroupItem value="none" id="l1-none" />
            <span className="text-sm">No-one (use Layer 2/3 to include)</span>
          </label>
        </RadioGroup>

        {channel.base_all_members && (
          <label className="ml-6 flex items-center gap-2 cursor-pointer">
            <Checkbox
              checked={channel.base_include_private}
              onCheckedChange={(v) =>
                onChange({ base_include_private: v === true })
              }
            />
            <span className="text-sm">Also include private members</span>
          </label>
        )}
      </CardContent>
    </Card>
  );
}

// ---------- Layer 2 -------------------------------------------------------

export function L2Card({
  channel,
  onChange,
}: {
  channel: NotificationChannel;
  onChange: (patch: Partial<NotificationChannel>) => void;
}) {
  const { data: groups } = useGroups();
  const usedGroupIds = new Set(channel.group_rules.map((r) => r.group_id));
  const availableGroups = (groups ?? []).filter((g) => !usedGroupIds.has(g.id));

  function add(groupId: string) {
    const newRule: GroupRuleSpec = {
      group_id: groupId,
      rule: "include",
      include_private: "inherit",
    };
    onChange({ group_rules: [...channel.group_rules, newRule] });
  }

  function update(groupId: string, patch: Partial<GroupRuleSpec>) {
    onChange({
      group_rules: channel.group_rules.map((r) =>
        r.group_id === groupId ? { ...r, ...patch } : r,
      ),
    });
  }

  function remove(groupId: string) {
    onChange({
      group_rules: channel.group_rules.filter((r) => r.group_id !== groupId),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Group rules</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {channel.group_rules.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No group rules yet.
          </p>
        )}
        {channel.group_rules.map((r) => {
          const group = groups?.find((g) => g.id === r.group_id);
          return (
            <div
              key={r.group_id}
              className="flex flex-wrap items-center gap-2 rounded border bg-background px-3 py-2"
            >
              <span className="font-medium text-sm flex-1 min-w-32">
                {group?.name ?? "(deleted group)"}
              </span>
              <Select
                value={r.rule}
                onValueChange={(v) => update(r.group_id, { rule: v as RuleAction })}
              >
                <SelectTrigger className="h-8 w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="include">Include</SelectItem>
                  <SelectItem value="exclude">Exclude</SelectItem>
                </SelectContent>
              </Select>
              {r.rule === "include" && (
                <Select
                  value={r.include_private ?? "inherit"}
                  onValueChange={(v) =>
                    update(r.group_id, { include_private: v as IncludePrivate })
                  }
                >
                  <SelectTrigger className="h-8 w-44">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="inherit">
                      Privacy: inherit Layer 1
                    </SelectItem>
                    <SelectItem value="yes">
                      Privacy: include private
                    </SelectItem>
                    <SelectItem value="no">
                      Privacy: exclude private
                    </SelectItem>
                  </SelectContent>
                </Select>
              )}
              <Button
                variant="ghost"
                size="icon"
                aria-label="Remove rule"
                onClick={() => remove(r.group_id)}
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          );
        })}

        {availableGroups.length > 0 && (
          <div className="space-y-1">
            <Label htmlFor="layer-add-group" className="text-xs text-muted-foreground">Add group</Label>
            <Select onValueChange={add} value="">
              <SelectTrigger id="layer-add-group">
                <SelectValue placeholder="Choose a group..." />
              </SelectTrigger>
              <SelectContent>
                {availableGroups.map((g) => (
                  <SelectItem key={g.id} value={g.id}>
                    {g.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------- Layer 3 -------------------------------------------------------

export function L3Card({
  channel,
  onChange,
}: {
  channel: NotificationChannel;
  onChange: (patch: Partial<NotificationChannel>) => void;
}) {
  const { data: members } = useMembers();
  const usedIds = new Set(channel.member_rules.map((r) => r.member_id));
  const availableMembers = (members ?? []).filter((m) => !usedIds.has(m.id));

  function add(memberId: string) {
    const newRule: MemberRuleSpec = { member_id: memberId, rule: "include" };
    onChange({ member_rules: [...channel.member_rules, newRule] });
  }

  function update(memberId: string, patch: Partial<MemberRuleSpec>) {
    onChange({
      member_rules: channel.member_rules.map((r) =>
        r.member_id === memberId ? { ...r, ...patch } : r,
      ),
    });
  }

  function remove(memberId: string) {
    onChange({
      member_rules: channel.member_rules.filter((r) => r.member_id !== memberId),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Member rules (overrides)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {channel.member_rules.length === 0 && (
          <p className="text-sm text-muted-foreground">No member overrides.</p>
        )}
        {channel.member_rules.map((r) => {
          const member = members?.find((m) => m.id === r.member_id);
          return (
            <div
              key={r.member_id}
              className="flex items-center gap-2 rounded border bg-background px-3 py-2"
            >
              <span className="font-medium text-sm flex-1 min-w-32">
                {member?.display_name || member?.name || "(deleted member)"}
              </span>
              <Select
                value={r.rule}
                onValueChange={(v) =>
                  update(r.member_id, { rule: v as RuleAction })
                }
              >
                <SelectTrigger className="h-8 w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="include">Include</SelectItem>
                  <SelectItem value="exclude">Exclude</SelectItem>
                </SelectContent>
              </Select>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Remove rule"
                onClick={() => remove(r.member_id)}
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          );
        })}

        {availableMembers.length > 0 && (
          <div className="space-y-1">
            <Label htmlFor="layer-add-member" className="text-xs text-muted-foreground">Add member</Label>
            <Select onValueChange={add} value="">
              <SelectTrigger id="layer-add-member">
                <SelectValue placeholder="Choose a member..." />
              </SelectTrigger>
              <SelectContent>
                {availableMembers.map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.display_name || m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
