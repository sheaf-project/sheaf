import { type ChangeEvent, useState } from "react";
import { Link, useNavigate } from "react-router";
import { Loader2Icon } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  type SPPreviewSummary,
  previewImport as previewSP,
} from "@/lib/sp-import";
import {
  type SheafPreviewSummary,
  previewSheafImport,
} from "@/lib/sheaf-import";
import {
  type PKPreviewSummary,
  previewImportFromFile as previewPKFile,
  previewImportFromApi as previewPKApi,
} from "@/lib/pk-import";
import {
  type TBPreviewSummary,
  previewImport as previewTB,
} from "@/lib/tb-import";
import {
  createApiImport,
  createFileImport,
  newIdempotencyKey,
} from "@/lib/imports";
import { Input } from "@/components/ui/input";

type Source = "choose" | "sheaf" | "sp" | "pk" | "tb";
// "importing" shows a brief spinner while the enqueue POST is in
// flight; on success the flow navigates to /imports/:id, which owns
// the running/done UI. There's no "done" step here any more.
type Step = "upload" | "preview" | "importing";
type PKMethod = "choose" | "file" | "api";

export function ImportPage() {
  const [source, setSource] = useState<Source>("choose");

  return (
    <>
      <PageHeader title="Import data">
        <Button variant="outline" size="sm" asChild>
          <Link to="/imports">Import history</Link>
        </Button>
      </PageHeader>
      {source === "choose" && <SourcePicker onSelect={setSource} />}
      {source === "sheaf" && (
        <SheafImportFlow onBack={() => setSource("choose")} />
      )}
      {source === "sp" && (
        <SPImportFlow onBack={() => setSource("choose")} />
      )}
      {source === "pk" && (
        <PKImportFlow onBack={() => setSource("choose")} />
      )}
      {source === "tb" && (
        <TBImportFlow onBack={() => setSource("choose")} />
      )}
    </>
  );
}

function SourcePicker({ onSelect }: { onSelect: (s: Source) => void }) {
  return (
    <div className="grid gap-4 max-w-lg">
      <Card
        className="cursor-pointer hover:border-primary transition-colors"
        onClick={() => onSelect("sheaf")}
      >
        <CardHeader>
          <CardTitle className="text-base">Import from Sheaf</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Import from a Sheaf data export (JSON). Use this to restore a backup
            or migrate between Sheaf instances.
          </p>
        </CardContent>
      </Card>
      <Card
        className="cursor-pointer hover:border-primary transition-colors"
        onClick={() => onSelect("sp")}
      >
        <CardHeader>
          <CardTitle className="text-base">Import from SimplyPlural</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Import from a SimplyPlural data export (JSON).
          </p>
        </CardContent>
      </Card>
      <Card
        className="cursor-pointer hover:border-primary transition-colors"
        onClick={() => onSelect("pk")}
      >
        <CardHeader>
          <CardTitle className="text-base">Import from PluralKit</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Pull from your PluralKit account using a token (the same one you
            use for <code>pk;token</code>), or upload a PK data export file.
          </p>
        </CardContent>
      </Card>
      <Card
        className="cursor-pointer hover:border-primary transition-colors"
        onClick={() => onSelect("tb")}
      >
        <CardHeader>
          <CardTitle className="text-base">Import from Tupperbox</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Upload a Tupperbox export file (run <code>tb!export</code> on
            Discord). Tupperbox doesn't track fronting, so only your tuppers
            and groups come across. Proxy brackets and per-tupper tags are
            dropped since Sheaf doesn't proxy Discord messages.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sheaf import flow
// ---------------------------------------------------------------------------

function SheafImportFlow({ onBack }: { onBack: () => void }) {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SheafPreviewSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  // One idempotency key per flow visit — a double-click on Import
  // reuses it, so the server dedupes instead of enqueueing twice.
  const [idemKey] = useState(newIdempotencyKey);

  const [systemProfile, setSystemProfile] = useState(true);
  const [allMembers, setAllMembers] = useState(true);
  const [selectedMembers, setSelectedMembers] = useState<Set<string>>(new Set());
  const [importFronts, setImportFronts] = useState(true);
  const [importGroups, setImportGroups] = useState(true);
  const [importTags, setImportTags] = useState(true);
  const [importFields, setImportFields] = useState(true);

  async function handleFileSelect(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setError(null);
    try {
      const p = await previewSheafImport(f);
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
      const job = await createFileImport({
        source: "sheaf_file",
        file,
        idempotencyKey: idemKey,
        options: {
          system_profile: systemProfile,
          member_ids: allMembers ? null : Array.from(selectedMembers),
          fronts: importFronts,
          groups: importGroups,
          tags: importTags,
          custom_fields: importFields,
        },
      });
      navigate(`/imports/${job.id}`);
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
      {error && <ErrorBanner message={error} />}

      {step === "upload" && (
        <Card className="max-w-lg">
          <CardHeader>
            <CardTitle className="text-base">Upload Sheaf export file</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Upload a JSON file from Sheaf&apos;s data export.
            </p>
            <input
              type="file"
              accept=".json,application/json"
              onChange={handleFileSelect}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-2 file:text-sm file:font-medium file:text-primary-foreground hover:file:bg-primary/90"
            />
            <Button variant="outline" size="sm" onClick={onBack}>
              Back
            </Button>
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
              <div>Fronts: <strong>{preview.front_count}</strong></div>
              <div>Groups: <strong>{preview.group_count}</strong></div>
              <div>Tags: <strong>{preview.tag_count}</strong></div>
              <div>Custom fields: <strong>{preview.custom_field_count}</strong></div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Import options</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Checkbox
                label="System profile (name, description, color, tag)"
                checked={systemProfile}
                onChange={setSystemProfile}
              />
              <Checkbox
                label={`Fronts (${preview.front_count.toLocaleString()} entries)`}
                checked={importFronts}
                onChange={setImportFronts}
              />
              <Checkbox
                label="Groups"
                checked={importGroups}
                onChange={setImportGroups}
              />
              <Checkbox
                label="Tags"
                checked={importTags}
                onChange={setImportTags}
              />
              <Checkbox
                label="Custom fields (definitions + values)"
                checked={importFields}
                onChange={setImportFields}
              />

              <MemberSelector
                members={preview.members}
                totalCount={preview.member_count}
                allMembers={allMembers}
                setAllMembers={setAllMembers}
                selectedMembers={selectedMembers}
                toggleMember={toggleMember}
              />

              <Button onClick={handleImport} className="w-full">
                Import
              </Button>
            </CardContent>
          </Card>
        </div>
      )}

      {step === "importing" && <ImportingCard />}
    </>
  );
}

// ---------------------------------------------------------------------------
// SP import flow (same as before, extracted)
// ---------------------------------------------------------------------------

function SPImportFlow({ onBack }: { onBack: () => void }) {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SPPreviewSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [idemKey] = useState(newIdempotencyKey);

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
      const p = await previewSP(f);
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
      const job = await createFileImport({
        source: "simplyplural_file",
        file,
        idempotencyKey: idemKey,
        options: {
          system_profile: systemProfile,
          member_ids: allMembers ? null : Array.from(selectedMembers),
          custom_fronts: customFronts,
          custom_fields: customFields,
          groups,
          front_history: frontHistory,
        },
      });
      navigate(`/imports/${job.id}`);
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
      {error && <ErrorBanner message={error} />}

      {step === "upload" && (
        <Card className="max-w-lg">
          <CardHeader>
            <CardTitle className="text-base">Upload SP export file</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Upload the JSON file from your SimplyPlural data export.
            </p>
            <input
              type="file"
              accept=".json,application/json"
              onChange={handleFileSelect}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-2 file:text-sm file:font-medium file:text-primary-foreground hover:file:bg-primary/90"
            />
            <Button variant="outline" size="sm" onClick={onBack}>
              Back
            </Button>
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

              <MemberSelector
                members={preview.members}
                totalCount={preview.member_count}
                allMembers={allMembers}
                setAllMembers={setAllMembers}
                selectedMembers={selectedMembers}
                toggleMember={toggleMember}
              />

              <Button onClick={handleImport} className="w-full">
                Import
              </Button>
            </CardContent>
          </Card>
        </div>
      )}

      {step === "importing" && <ImportingCard />}
    </>
  );
}

// ---------------------------------------------------------------------------
// PluralKit import flow
// ---------------------------------------------------------------------------

function PKImportFlow({ onBack }: { onBack: () => void }) {
  const navigate = useNavigate();
  const [method, setMethod] = useState<PKMethod>("choose");
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  // Token lives only in component state; we never persist it. Kept in a
  // ref-discipline mental model: read on submit, then implicitly discarded
  // when the user navigates away or finishes the flow.
  const [token, setToken] = useState<string>("");
  const [preview, setPreview] = useState<PKPreviewSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [idemKey] = useState(newIdempotencyKey);
  // The PK API preview round-trips to pluralkit.me and can take a few
  // seconds — without an explicit busy state the user gets no feedback
  // and clicks again, firing duplicate calls (and sometimes a 429).
  const [apiBusy, setApiBusy] = useState(false);

  const [systemProfile, setSystemProfile] = useState(true);
  const [allMembers, setAllMembers] = useState(true);
  const [selectedMembers, setSelectedMembers] = useState<Set<string>>(new Set());
  const [groups, setGroups] = useState(true);
  const [frontHistory, setFrontHistory] = useState(false);

  async function handleFileSelect(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setError(null);
    try {
      const p = await previewPKFile(f);
      setPreview(p);
      setStep("preview");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to parse file");
    }
  }

  async function handleApiPreview() {
    if (!token.trim() || apiBusy) {
      if (!token.trim()) setError("Enter your PluralKit token first.");
      return;
    }
    setError(null);
    setApiBusy(true);
    try {
      const p = await previewPKApi(token);
      setPreview(p);
      setStep("preview");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not reach PluralKit. Check the token and try again.",
      );
    } finally {
      setApiBusy(false);
    }
  }

  async function handleImport() {
    setStep("importing");
    setError(null);
    const options = {
      system_profile: systemProfile,
      member_ids: allMembers ? null : Array.from(selectedMembers),
      groups,
      front_history: frontHistory,
    };
    try {
      const job =
        method === "file" && file
          ? await createFileImport({
              source: "pluralkit_file",
              file,
              idempotencyKey: idemKey,
              options,
            })
          : await createApiImport({
              pkToken: token,
              idempotencyKey: idemKey,
              options,
            });
      // Token has served its purpose; clear it so it's not lingering on
      // the page for the rest of the session. (It's also encrypted at
      // rest server-side and wiped when the job finishes.)
      setToken("");
      navigate(`/imports/${job.id}`);
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
      {error && <ErrorBanner message={error} />}

      {method === "choose" && (
        <div className="grid gap-4 max-w-lg">
          <Card
            className="cursor-pointer hover:border-primary transition-colors"
            onClick={() => setMethod("api")}
          >
            <CardHeader>
              <CardTitle className="text-base">Connect with a token</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Use your PluralKit token (run <code>pk;token</code> in any
                Discord server PluralKit is in to get one). Sheaf forwards it
                once to fetch your system, then drops it. Nothing is stored.
              </p>
            </CardContent>
          </Card>
          <Card
            className="cursor-pointer hover:border-primary transition-colors"
            onClick={() => setMethod("file")}
          >
            <CardHeader>
              <CardTitle className="text-base">Upload an export file</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Use the JSON file from <code>pk;export</code> if you'd rather
                not paste a token.
              </p>
            </CardContent>
          </Card>
          <Button variant="outline" size="sm" onClick={onBack} className="w-fit">
            Back
          </Button>
        </div>
      )}

      {method === "file" && step === "upload" && (
        <Card className="max-w-lg">
          <CardHeader>
            <CardTitle className="text-base">Upload PluralKit export file</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Run <code>pk;export</code> on Discord, then upload the JSON
              attachment PluralKit DMs you.
            </p>
            <input
              type="file"
              accept=".json,application/json"
              onChange={handleFileSelect}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-2 file:text-sm file:font-medium file:text-primary-foreground hover:file:bg-primary/90"
            />
            <Button variant="outline" size="sm" onClick={() => setMethod("choose")}>
              Back
            </Button>
          </CardContent>
        </Card>
      )}

      {method === "api" && step === "upload" && (
        <Card className="max-w-lg">
          <CardHeader>
            <CardTitle className="text-base">Connect to PluralKit</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Paste the token from <code>pk;token</code>. We use it once to
              fetch your system data and discard it. The token is never
              stored on the server or in your browser.
            </p>
            <div className="space-y-2">
              <Label htmlFor="pk-token">PluralKit token</Label>
              <Input
                id="pk-token"
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="abcd1234..."
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div className="flex gap-2">
              <Button
                onClick={handleApiPreview}
                disabled={!token.trim() || apiBusy}
              >
                {apiBusy && (
                  <Loader2Icon className="size-4 animate-spin" />
                )}
                {apiBusy ? "Retrieving from PluralKit…" : "Continue"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={apiBusy}
                onClick={() => {
                  setToken("");
                  setMethod("choose");
                }}
              >
                Back
              </Button>
            </div>
            {apiBusy && (
              <p className="text-xs text-muted-foreground">
                Fetching your system from the PluralKit API — this can
                take a few seconds for systems with a long switch history.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {step === "preview" && preview && (
        <div className="grid gap-4 max-w-2xl">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Preview
                {preview.system_name && (
                  <span className="ml-2 font-normal text-muted-foreground">
                    {preview.system_name}
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3 text-sm">
              <div>Members: <strong>{preview.member_count}</strong></div>
              <div>Groups: <strong>{preview.group_count}</strong></div>
              <div>
                Switches:{" "}
                <strong>
                  {method === "api" && preview.switch_count >= 100
                    ? "100+"
                    : preview.switch_count.toLocaleString()}
                </strong>
              </div>
              {preview.earliest_switch && preview.latest_switch && (
                <div className="col-span-2 text-xs text-muted-foreground">
                  Switch range: {new Date(preview.earliest_switch).toLocaleDateString()}
                  {" "}to{" "}
                  {new Date(preview.latest_switch).toLocaleDateString()}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Import options</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Checkbox
                label="System profile (tag, color, avatar — only fills in fields you've left blank)"
                checked={systemProfile}
                onChange={setSystemProfile}
              />
              <Checkbox
                label="Groups"
                checked={groups}
                onChange={setGroups}
              />
              <Checkbox
                label={
                  method === "api" && preview.switch_count >= 100
                    ? "Front history (full pull, may take a moment for large logs)"
                    : `Front history (${preview.switch_count.toLocaleString()} switches)`
                }
                checked={frontHistory}
                onChange={setFrontHistory}
              />

              <MemberSelector
                members={preview.members}
                totalCount={preview.member_count}
                allMembers={allMembers}
                setAllMembers={setAllMembers}
                selectedMembers={selectedMembers}
                toggleMember={toggleMember}
              />

              <Button onClick={handleImport} className="w-full">
                Import
              </Button>
            </CardContent>
          </Card>
        </div>
      )}

      {step === "importing" && <ImportingCard />}
    </>
  );
}

// ---------------------------------------------------------------------------
// Tupperbox import flow
// ---------------------------------------------------------------------------

function TBImportFlow({ onBack }: { onBack: () => void }) {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<TBPreviewSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [idemKey] = useState(newIdempotencyKey);

  const [allMembers, setAllMembers] = useState(true);
  const [selectedMembers, setSelectedMembers] = useState<Set<string>>(new Set());
  const [groups, setGroups] = useState(true);

  async function handleFileSelect(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setError(null);
    try {
      const p = await previewTB(f);
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
      const job = await createFileImport({
        source: "tupperbox_file",
        file,
        idempotencyKey: idemKey,
        options: {
          member_ids: allMembers ? null : Array.from(selectedMembers),
          groups,
        },
      });
      navigate(`/imports/${job.id}`);
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
      {error && <ErrorBanner message={error} />}

      {step === "upload" && (
        <Card className="max-w-lg">
          <CardHeader>
            <CardTitle className="text-base">Upload Tupperbox export file</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Run <code>tb!export</code> on Discord, then upload the JSON
              attachment Tupperbox DMs you.
            </p>
            <input
              type="file"
              accept=".json,application/json"
              onChange={handleFileSelect}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-2 file:text-sm file:font-medium file:text-primary-foreground hover:file:bg-primary/90"
            />
            <Button variant="outline" size="sm" onClick={onBack}>
              Back
            </Button>
          </CardContent>
        </Card>
      )}

      {step === "preview" && preview && (
        <div className="grid gap-4 max-w-2xl">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Export summary</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3 text-sm">
              <div>Members: <strong>{preview.member_count}</strong></div>
              <div>Groups: <strong>{preview.group_count}</strong></div>
              <div className="col-span-2 text-xs text-muted-foreground">
                Tupperbox doesn't track fronting, system metadata, or
                pronouns/colour per tupper. Proxy brackets, banners, and
                per-tupper tags are dropped on import.
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Import options</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Checkbox
                label="Groups"
                checked={groups}
                onChange={setGroups}
              />

              <MemberSelector
                members={preview.members}
                totalCount={preview.member_count}
                allMembers={allMembers}
                setAllMembers={setAllMembers}
                selectedMembers={selectedMembers}
                toggleMember={toggleMember}
              />

              <Button onClick={handleImport} className="w-full">
                Import
              </Button>
            </CardContent>
          </Card>
        </div>
      )}

      {step === "importing" && <ImportingCard />}
    </>
  );
}

// ---------------------------------------------------------------------------
// Shared components
// ---------------------------------------------------------------------------

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="mb-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
      {message}
    </div>
  );
}

function ImportingCard() {
  return (
    <Card className="max-w-lg">
      <CardContent className="py-8 text-center">
        <p className="text-muted-foreground">Importing data...</p>
      </CardContent>
    </Card>
  );
}

function MemberSelector({
  members,
  totalCount,
  allMembers,
  setAllMembers,
  selectedMembers,
  toggleMember,
}: {
  members: { id: string; name: string }[];
  totalCount: number;
  allMembers: boolean;
  setAllMembers: (v: boolean) => void;
  selectedMembers: Set<string>;
  toggleMember: (id: string) => void;
}) {
  return (
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
          {members.map((m) => (
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
          {selectedMembers.size} of {totalCount} selected
        </p>
      )}
    </div>
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
