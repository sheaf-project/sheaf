import { type ChangeEvent, useState } from "react";
import { useNavigate } from "react-router";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  type SPPreviewSummary,
  type SPImportResult,
  previewImport,
  runImport,
} from "@/lib/sp-import";

type Step = "upload" | "preview" | "importing" | "done";

export function ImportPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SPPreviewSummary | null>(null);
  const [result, setResult] = useState<SPImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Options
  const [systemProfile, setSystemProfile] = useState(true);
  const [allMembers, setAllMembers] = useState(true);
  const [selectedMembers, setSelectedMembers] = useState<Set<string>>(new Set());
  const [customFronts, setCustomFronts] = useState(true);
  const [customFields, setCustomFields] = useState(true);
  const [groups, setGroups] = useState(true);
  const [frontHistory, setFrontHistory] = useState(false);

  async function handleFileSelect(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setError(null);
    try {
      const p = await previewImport(f);
      setPreview(p);
      setStep("preview");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to parse file");
    }
  }

  async function handleImport() {
    if (!file) return;
    setStep("importing");
    setError(null);
    try {
      const r = await runImport(file, {
        system_profile: systemProfile,
        member_ids: allMembers ? null : Array.from(selectedMembers),
        custom_fronts: customFronts,
        custom_fields: customFields,
        groups,
        front_history: frontHistory,
      });
      setResult(r);
      setStep("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
      setStep("preview");
    }
  }

  function toggleMember(id: string) {
    setSelectedMembers((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <>
      <PageHeader title="Import from SimplyPlural" />

      {error && (
        <div className="mb-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {step === "upload" && (
        <Card className="max-w-lg">
          <CardHeader>
            <CardTitle className="text-base">Upload SP export file</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Upload the JSON file from your SimplyPlural data export.
              You can request your export from SP&apos;s settings before they shut down.
            </p>
            <input
              type="file"
              accept=".json,application/json"
              onChange={handleFileSelect}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-2 file:text-sm file:font-medium file:text-primary-foreground hover:file:bg-primary/90"
            />
          </CardContent>
        </Card>
      )}

      {step === "preview" && preview && (
        <div className="grid gap-4 max-w-2xl">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Export summary
                {preview.system_name && (
                  <span className="ml-2 font-normal text-muted-foreground">
                    — {preview.system_name}
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3 text-sm">
              <div>Members: <strong>{preview.member_count}</strong></div>
              <div>Custom fronts: <strong>{preview.custom_front_count}</strong></div>
              <div>Front history: <strong>{preview.front_history_count}</strong></div>
              <div>Groups: <strong>{preview.group_count}</strong></div>
              <div>Custom fields: <strong>{preview.custom_field_count}</strong></div>
              <div>Notes: <strong>{preview.note_count}</strong></div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Import options</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Checkbox
                label="System profile (name, description, color)"
                checked={systemProfile}
                onChange={setSystemProfile}
              />
              <Checkbox
                label="Custom fronts"
                checked={customFronts}
                onChange={setCustomFronts}
              />
              <Checkbox
                label="Custom fields (definitions + values)"
                checked={customFields}
                onChange={setCustomFields}
              />
              <Checkbox
                label="Groups"
                checked={groups}
                onChange={setGroups}
              />
              <Checkbox
                label={`Front history (${preview.front_history_count.toLocaleString()} entries)`}
                checked={frontHistory}
                onChange={setFrontHistory}
              />

              <div className="space-y-2 border-t pt-3">
                <div className="flex items-center justify-between">
                  <Label>Members</Label>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => setAllMembers(!allMembers)}
                  >
                    {allMembers ? "Select specific" : "Import all"}
                  </Button>
                </div>
                {!allMembers && (
                  <div className="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
                    {preview.members.map((m) => (
                      <Badge
                        key={m.id}
                        variant={selectedMembers.has(m.id) ? "default" : "outline"}
                        className="cursor-pointer"
                        onClick={() => toggleMember(m.id)}
                      >
                        {m.name}
                      </Badge>
                    ))}
                  </div>
                )}
                {!allMembers && (
                  <p className="text-xs text-muted-foreground">
                    {selectedMembers.size} of {preview.member_count} selected
                  </p>
                )}
              </div>

              <Button onClick={handleImport} className="w-full">
                Import
              </Button>
            </CardContent>
          </Card>
        </div>
      )}

      {step === "importing" && (
        <Card className="max-w-lg">
          <CardContent className="py-8 text-center">
            <p className="text-muted-foreground">Importing data...</p>
          </CardContent>
        </Card>
      )}

      {step === "done" && result && (
        <Card className="max-w-lg">
          <CardHeader>
            <CardTitle className="text-base">Import complete</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-2 text-sm">
              {result.members_imported > 0 && (
                <div>Members: <strong>{result.members_imported}</strong></div>
              )}
              {result.custom_fronts_imported > 0 && (
                <div>Custom fronts: <strong>{result.custom_fronts_imported}</strong></div>
              )}
              {result.fronts_imported > 0 && (
                <div>Fronts: <strong>{result.fronts_imported}</strong></div>
              )}
              {result.groups_imported > 0 && (
                <div>Groups: <strong>{result.groups_imported}</strong></div>
              )}
              {result.custom_fields_imported > 0 && (
                <div>Custom fields: <strong>{result.custom_fields_imported}</strong></div>
              )}
            </div>
            {result.warnings.length > 0 && (
              <div className="space-y-1">
                <p className="text-sm font-medium text-yellow-500">Warnings:</p>
                {result.warnings.map((w, i) => (
                  <p key={i} className="text-xs text-muted-foreground">{w}</p>
                ))}
              </div>
            )}
            <Button onClick={() => navigate("/members")} className="w-full">
              View members
            </Button>
          </CardContent>
        </Card>
      )}
    </>
  );
}

function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-input"
      />
      {label}
    </label>
  );
}
