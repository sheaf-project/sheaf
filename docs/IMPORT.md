# Importing data into Sheaf

Sheaf imports from ten sources. All of them live at **Settings -> Import data** (or
directly at `/import`), and all of them follow the same shape: pick a file, look at the
preview, then commit. Nothing is written to your system until you do.

| Source | What it takes | `source=` |
|---|---|---|
| SimplyPlural | JSON export file from your SP account | `simplyplural_file` |
| PluralKit | the JSON from `pk;export` | `pluralkit_file` |
| PluralKit (live) | your `pk;token`, pulled from the PK API | `pluralkit_api` |
| Tupperbox | the JSON from `tb!export` | `tupperbox_file` |
| PluralSpace | the export zip (`manifest.json` + `data.json` + `media/`) | `pluralspace_file` |
| Prism | a `.prism` file, plus its passphrase | `prism_file` |
| Ampersand | the JSON export (`{revision, config, database}`) | `ampersand_file` |
| PluralPort | a `.json` document or a `.pluralport.zip` bundle | `pluralport_file` |
| Sheaf | the JSON from `/v1/export` | `sheaf_file` |
| Sheaf (with images) | the export-with-images zip from `/v1/export/jobs` | `sheaf_archive` |

**Octocon** and compatible forks are covered by the PluralKit file importer: they emit a
PluralKit-shaped export, so upload it as a PluralKit file. There is no separate Octocon
source to pick.

---

## How an import runs

1. **Preview.** The file is parsed and summarised without touching your system: counts of
   what was found, and warnings for anything that will be deduplicated, shortened, or
   capped. The PluralKit preview also reports the earliest and latest switch timestamps, so
   you can decide whether you want front history at all.
2. **Queue.** Committing creates an import *job* and returns immediately. Imports run in a
   background runner, so a large file does not hold a request open and closing the tab does
   not cancel it.
3. **Poll.** The job moves `pending` -> `running` -> `complete`, `failed`, or `cancelled`.
   The web UI polls every couple of seconds while it is live.
4. **Report.** A finished job carries per-entity counts plus an event log of info,
   warnings, and per-record errors. `complete` means the run finished cleanly at the file
   level, and can still contain per-record errors worth reading. `failed` means it aborted
   at the file or schema level and nothing was written: a hard failure rolls the whole
   transaction back rather than leaving a half-populated system.

A job can be cancelled while it is still `pending`. Once it is `running` there is no
mid-flight cancel; the request is refused rather than leaving the walk half-applied.

Every job carries an idempotency key, which the client generates. Submitting the same key
twice returns the original job instead of starting a second one, so a retried request or a
double-clicked button cannot import your data twice.

Credentials never outlive the job that needs them. A PluralKit token or a Prism passphrase
is encrypted at rest against that job's own id and wiped when the job finalises, along with
the uploaded file itself. The report row lives on afterwards so you can still read what
happened.

---

## PluralKit import

PluralKit is a Discord-first plural system bot with a different data model
than SP/Sheaf. The importer reconciles those differences so you can move (or
mirror) your PK system into Sheaf without losing structure.

### Two ingestion paths

There are two ways to get the data in. Both produce the same result.

**File upload** - DM `pk;export` to PluralKit on Discord. PK replies with a
JSON attachment. Upload it here. Nothing leaves your browser except the file
content itself.

**Live API** - Run `pk;token` on Discord. PK DMs you a token. Paste it into
Sheaf. The token is forwarded to `api.pluralkit.me` for as long as the job needs it:

- It is never written to disk in the clear: while the job is queued it is encrypted at
  rest, bound to that job's id, and it is wiped when the job finishes.
- It is not stored in your browser's localStorage.
- It is not logged on the server.
- It is cleared from the import form's React state once the import finishes.

If you'd rather not paste a token at all, use the file upload path instead.
Both produce the same result.

### What gets imported

| PluralKit data | How it lands in Sheaf |
|---|---|
| System name, tag, color, avatar | Filled in **only on fields you've left blank**. Won't overwrite anything you've already set. |
| Members | Created with name, display name, color, pronouns, avatar URL, description, birthday. Each member's PK HID (e.g. `wyyetr`) is stored in `pluralkit_id` so you can cross-reference between the two. |
| Member privacy | Collapsed to Sheaf's tri-level `privacy` enum. Uses PK's `visibility` field if present; otherwise falls back to "all-public if every per-field flag is public, else private". |
| Birthdays with no year | PK uses `0004-MM-DD` as the year-less sentinel. Sheaf collapses these to `MM-DD`. |
| Groups | Created as Sheaf groups with their member memberships intact. PK groups don't nest, so there's no parent-link pass. |
| Switches -> fronts | The PK switch log is converted to Sheaf front intervals. See below. |
| Proxy tags | **Not imported.** Sheaf doesn't have a Discord-bridge concept yet; these are PK-bot-only data. |
| Discord-specific config | Not imported (`tts`, `keep_proxy`, `autoproxy_enabled`, message counts, etc.). |
| System description | **Not pulled by default.** Sheaf system descriptions are heavily user-styled; silent overwrite at import would be the kind of thing that reads as a bug. Edit it manually if you want PK's description in Sheaf. |

### Switch log to front intervals

PK and Sheaf model fronting differently:

- **PluralKit** records *switches*, point-in-time events that say "from this
  moment, the fronter set is now {Alice, Bob}". The previous switch is
  implicitly superseded.
- **Sheaf** records *front intervals*, each with a `started_at`, optional
  `ended_at`, and a member set.

The importer walks PK switches **oldest-to-newest** and converts them as
follows:

```
PK switches (sorted ascending):
  09:00  {Alice}
  10:00  {Alice, Bob}
  11:00  {Carol}
  12:00  {}             # nobody fronting
```

becomes

```
Sheaf fronts:
  Front #1: started 09:00, ended 10:00, members [Alice]
  Front #2: started 10:00, ended 11:00, members [Alice, Bob]
  Front #3: started 11:00, ended 12:00, members [Carol]
```

Each new switch closes the previous Front and opens a new one. Empty switches
(`members: []`) close the previous Front and don't open a new one - they
preserve "nobody fronting" gaps in your timeline.

A member who fronts continuously across several switches will end up in
several consecutive Front records. The
[coalesce-contiguous-fronts](../CHANGELOG.md) feature reassembles them on
display so the dashboard shows one continuous "fronting since 09:00" rather
than a fresh start for each switch.

### What you can toggle at import time

The preview screen shows what was found and lets you control:

- **System profile** - copy PK system tag/color/avatar onto Sheaf system if
  not already set.
- **Groups** - import groups and their member memberships.
- **Front history** - off by default. PK switch logs can run thousands of
  entries; turning this on can take a moment for large systems on the live
  API path (one paginated request per ~100 switches).
- **Member selection** - pick exactly which members to bring across. Switches
  that reference members you deselected are still walked, but those members
  are silently dropped from the resulting Front records (you'll see a
  warning).

### Rate limiting & retries

The live API path throttles itself to roughly one paginated request every
600ms, well under PluralKit's 2 req/sec/token limit. If PK rate-limits us
anyway (HTTP 429), the import aborts cleanly with an error and nothing is
written. Retry after a minute.

If your token is rejected (401/403), the importer surfaces a clear error
without exposing the token in the response.

### Re-running an import

Imports are additive. Running the same import twice will create duplicate
members, groups, and fronts. There's no de-dup pass against existing
`pluralkit_id` matches in v1; if you want to refresh from PK, delete the
old members first or import into a fresh Sheaf system.

A bidirectional PK sync (with conflict resolution and de-dup) is in the
roadmap as a separate feature on top of one-shot import.

### Things that don't have a PK equivalent

A few Sheaf concepts have no source data in a PK export and stay unset on
imported members:

- Tags (Sheaf-only; you can add them after).
- Custom fields and values.
- Member journals.
- Member-level "friends" privacy (only public/private maps from PK).

---

## SimplyPlural import

The SP importer parses the JSON file from SP's data export. It supports the
same preview-then-import flow as PK and covers:

- System profile (name, description, color).
- Members with avatar, pronouns, color, description, birthday, privacy.
- Custom fronts (imported as Members with `is_custom_front=true`, so they
  show up in the fronter list and groups but are excluded from member-count
  statistics and listed separately on the Members page). Every importer that
  brings custom fronts across creates them with **keep fronting private** on,
  so an imported "Asleep" never announces itself on a shared page until you
  release the guard yourself; a Sheaf backup or a PluralPort file that
  records the setting restores what it recorded instead.
- Custom field definitions and per-member values.
- Groups with parent hierarchy and member memberships.
- Front history (off by default; SP exports can be large).
- Chat messages, brought onto the system message board.

SP notes are counted but **not** imported: the report tells you how many were found and
left behind.

---

## Tupperbox import

Tupperbox is a Discord proxy bot with a deliberately small data model: no system
metadata, no fronting log, no custom fields, no privacy levels. An import therefore
collapses to members and groups, with their names, avatars, and any description Tupperbox
held. There is nothing to decide about front history because there is none in the file.

---

## PluralSpace import

Takes PluralSpace's export zip, which carries a `manifest.json`, a `data.json`, and a
`media/` directory of image bytes that are restored through the normal image pipeline.

Two shape mismatches are worth knowing about before you commit:

- PluralSpace's per-member `role` values have no Sheaf equivalent, so they come across as
  **tags** (toggleable in the preview).
- PluralSpace keeps chat in named channels; Sheaf has one system message board. The
  channels are flattened onto it, and the report names each channel that was collapsed.

---

## Prism import

A `.prism` file is an encrypted envelope, so this importer needs the passphrase as well as
the file. The passphrase is handled like the PluralKit token: encrypted at rest for the
life of the job and wiped when it finishes. Both the preview and the commit path are rate
limited, because deriving the key from the passphrase is deliberately expensive.

Two conversions to expect: Prism's sleep sessions have no Sheaf equivalent and are dropped
with a warning, and Prism's slider custom-field type collapses to a text field.

---

## Ampersand import

Ampersand's JSON export carries its images inline as base64 data URIs, which are decoded
and stored like any other upload.

The structural difference is that Ampersand's *systems* are what Sheaf calls groups: a file
describing several Ampersand systems imports as one Sheaf system containing a group per
Ampersand system. Ampersand's `role` becomes a tag, and `age` becomes an auto-created "Age"
custom field. Ampersand's `config` block (which includes its app-lock password hash) is
never read.

---

## Sheaf import

For round-trip backups or migrating between instances. There are two flavours, and the
preview works out which one you handed it.

**The JSON** (`source=sheaf_file`), from `GET /v1/export`. Brings members, fronts, groups,
tags, custom fields, relationships, journal entries, messages and polls. It is idempotent:
members are matched against your existing roster, other records by exact match, so
re-importing the same file does not duplicate your system. Image *bytes* are not in this
file, so internal image references are stripped rather than left pointing at keys that do
not exist on the instance you are importing into.

**The with-images zip** (`source=sheaf_archive`), from `POST /v1/export/jobs`. The same
walk, plus the images: every image the JSON references is restored as your own uploaded
file under a fresh key, and the references are rewritten to match. So avatars, banners and
bio images survive a move between instances, including between instances using different
storage backends. Your storage quota still applies, and images beyond the per-job restore
cap are skipped with a warning rather than failing the import.

One thing deliberately does not round-trip: your front-history retention window. A restore
must never silently arm a deletion policy, so that setting stays as it is on the receiving
system and you turn it on yourself if you want it.

## PluralPort import

For moving data in from any app that speaks the
[PluralPort](https://github.com/PluralPort/spec) v0.1 standard (formerly
OpenPlural), including a Sheaf PluralPort export. Accepts either a bare
`.json` document or a `.pluralport.zip` bundle (`pluralport.json` +
`assets/`); the runner detects which by content. The envelope is translated
back to the native shape and run through the same importer as a Sheaf
re-import, so dedup, the member cap, the image-restore pipeline (for
bundles), and the avatar-policy gate all apply. A file whose
`pluralport_version` this build does not understand is rejected rather than
partially imported.

Files from before the format's rename still import: the deprecated
`openplural_version` envelope key is accepted as a v0.1 alias
(`pluralport_version` wins if a file carries both), and a bundle whose inner
document is still named `openplural.json` reads the same as a current one.

Sheaf round-trips its own PluralPort exports losslessly: anything the v0.1 spec
cannot model rides under `extensions.sheaf.*` and is restored on import. See
[PLURALPORT.md](PLURALPORT.md) for the full mapping, the per-version
implementation log, and the known gaps.

---

## Limits

Two different kinds of limit can stop an import, and they mean different things.

**Your member cap** is the account limit that also applies when you create members by
hand. It is checked before anything is written, including before any image is stored, so an
import that would take you over it fails cleanly rather than part-way.

**Per-job row caps** bound how much of one entity type a single import will process:
fronts, journal entries, messages, revisions, polls, groups, tags, custom fields,
relationship types, and relationships. These exist so one pathological file cannot
monopolise the runner, not to limit how much you may own, and they are generous (hundreds
of thousands of fronts by default). The preview warns before you commit, and the message
names the count, the cap, and the two ways out: split the file, or ask the operator to
raise it. Self-hosters set their own; see [SELFHOSTING.md](SELFHOSTING.md).

Separately, an uploaded import file is capped at 100 MB.

If you have front-history retention turned on, any preview that contains front history
warns you: history older than your window will be aged out once the import grace period is
up. Raise or turn off the window before importing if you want to keep it.

---

## API surface

All importer endpoints require an authenticated session (or an API key with
the `import:write` scope) and operate on the caller's own system.

Previews are synchronous and write nothing:

| Method | Path | Body |
|---|---|---|
| POST | `/v1/import/simplyplural/preview` | multipart `file` |
| POST | `/v1/import/pluralkit/preview` | multipart `file` |
| POST | `/v1/import/pluralkit-api/preview` | JSON `{token}` |
| POST | `/v1/import/tupperbox/preview` | multipart `file` |
| POST | `/v1/import/pluralspace/preview` | multipart `file` |
| POST | `/v1/import/prism/preview` | multipart `file` + `passphrase` |
| POST | `/v1/import/ampersand/preview` | multipart `file` |
| POST | `/v1/import/pluralport/preview` | multipart `file` |
| POST | `/v1/import/sheaf/preview` | multipart `file` |

There is no separate archive preview: `/v1/import/sheaf/preview` accepts either the JSON or
the zip and tells you which it got (`archive`, `image_count`), so a client knows whether to
submit the job as `sheaf_file` or `sheaf_archive`.

Committing goes through the job API:

| Method | Path | Body |
|---|---|---|
| POST | `/v1/imports/file` | multipart `file`, `source`, `idempotency_key`, optional `options` (a JSON string), optional `credential` (the Prism passphrase). Returns 202 and the job. |
| POST | `/v1/imports/api` | JSON `{source: "pluralkit_api", idempotency_key, pk_token, options}`. Returns 202 and the job. |
| GET | `/v1/imports` | Job history: `limit` (1-100), `cursor` (the `created_at` of the last row you saw), `include_archived`. Summaries only, no event log. |
| GET | `/v1/imports/{job_id}` | One job: status, counts, and the full event report. |
| DELETE | `/v1/imports/{job_id}` | Cancel a `pending` job, or archive a finished one. 409 on a job that is already running. |

`source=openplural_file` is still accepted on `/v1/imports/file` as a deprecated alias for
`pluralport_file`, as is `/v1/import/openplural/preview`.

For the option fields each source supports, see the OpenAPI docs at `/v1/docs` on your
instance.
