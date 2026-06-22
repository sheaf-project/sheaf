# Importing data into Sheaf

Sheaf can import from three sources today:

- **SimplyPlural** — JSON export file from your SP account.
- **PluralKit** — either the JSON file produced by `pk;export`, or a live pull
  from the PluralKit API using your account token (`pk;token`).
- **Sheaf** — JSON file produced by `/v1/export`. Use this to restore a backup
  or migrate between Sheaf instances.

All three flows live at **Settings → Import data** (or directly at `/import`).
Each one previews what it found before writing anything to your system, so you
can deselect members or skip front history before committing.

---

## PluralKit import

PluralKit is a Discord-first plural system bot with a different data model
than SP/Sheaf. The importer reconciles those differences so you can move (or
mirror) your PK system into Sheaf without losing structure.

### Two ingestion paths

There are two ways to get the data in. Both produce the same result.

**File upload** — DM `pk;export` to PluralKit on Discord. PK replies with a
JSON attachment. Upload it here. Nothing leaves your browser except the file
content itself.

**Live API** — Run `pk;token` on Discord. PK DMs you a token. Paste it into
Sheaf. The token is forwarded once to `api.pluralkit.me`, then dropped:

- It is not written to disk.
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
| Switches → fronts | The PK switch log is converted to Sheaf front intervals. See below. |
| Proxy tags | **Not imported.** Sheaf doesn't have a Discord-bridge concept yet; these are PK-bot-only data. |
| Discord-specific config | Not imported (`tts`, `keep_proxy`, `autoproxy_enabled`, message counts, etc.). |
| System description | **Not pulled by default.** Sheaf system descriptions are heavily user-styled; silent overwrite at import would be the kind of thing that reads as a bug. Edit it manually if you want PK's description in Sheaf. |

### Switch log → front intervals

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
(`members: []`) close the previous Front and don't open a new one — they
preserve "nobody fronting" gaps in your timeline.

A member who fronts continuously across several switches will end up in
several consecutive Front records. The
[coalesce-contiguous-fronts](../CHANGELOG.md) feature reassembles them on
display so the dashboard shows one continuous "fronting since 09:00" rather
than a fresh start for each switch.

### What you can toggle at import time

The preview screen shows what was found and lets you control:

- **System profile** — copy PK system tag/color/avatar onto Sheaf system if
  not already set.
- **Groups** — import groups and their member memberships.
- **Front history** — off by default. PK switch logs can run thousands of
  entries; turning this on can take a moment for large systems on the live
  API path (one paginated request per ~100 switches).
- **Member selection** — pick exactly which members to bring across. Switches
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
  statistics and listed separately on the Members page).
- Custom field definitions and per-member values.
- Groups with parent hierarchy and member memberships.
- Front history (off by default; SP exports can be large).
- Notes are detected but not yet imported (the journal feature has a
  different data shape; a future migration pass will pull these in).

---

## Sheaf import

For round-trip backups or migrating between instances. Pulls members, fronts,
groups, tags, custom fields, and journal entries verbatim from a JSON file
produced by `/v1/export`.

Image bytes are not embedded in the sync JSON export — only S3 keys. A
re-import on a different instance will keep the keys but won't show the
images themselves unless those bytes are present in the destination's S3
bucket. The async `/v1/export/jobs` endpoint produces a zip with image bytes
included; the import side that consumes those zips is on the roadmap (see
[CHANGELOG.md](../CHANGELOG.md)).

## OpenPlural import

For moving data in from any app that speaks the
[OpenPlural](https://github.com/skylartaylor/openplural) v0.1 standard, including
a Sheaf OpenPlural export. Accepts either a bare `.json` document or an
`.openplural.zip` bundle (`openplural.json` + `assets/`); the runner detects
which by content. The envelope is translated back to the native shape and run
through the same importer as a Sheaf re-import, so dedup, the member cap, the
image-restore pipeline (for bundles), and the avatar-policy gate all apply. A
file whose `openplural_version` this build does not understand is rejected
rather than partially imported.

Sheaf round-trips its own OpenPlural exports losslessly: anything the v0.1 spec
cannot model rides under `extensions.sheaf.*` and is restored on import. See
[OPENPLURAL.md](OPENPLURAL.md) for the full mapping, the per-version
implementation log, and the known gaps.

---

## API surface

All importer endpoints require an authenticated session (or an API key with
the `import:write` scope) and operate on the caller's own system.

| Method | Path | Body |
|---|---|---|
| POST | `/v1/import/simplyplural/preview` | multipart `file` |
| POST | `/v1/import/simplyplural` | multipart `file` + query options |
| POST | `/v1/import/pluralkit/preview` | multipart `file` |
| POST | `/v1/import/pluralkit` | multipart `file` + query options |
| POST | `/v1/import/pluralkit-api/preview` | JSON `{token}` |
| POST | `/v1/import/pluralkit-api` | JSON `{token, options}` |
| POST | `/v1/import/sheaf/preview` | multipart `file` |
| POST | `/v1/import/sheaf` | multipart `file` + query options |

Each preview endpoint returns a small summary (counts of members, groups,
switches/fronts, notes; in the PK case also the earliest and latest switch
timestamps so you can decide whether to opt into front history). The
non-preview endpoints actually write to the database and return a result
object with `*_imported` counters and a list of human-readable warnings.

For the option fields supported by each, see the OpenAPI docs at
`/v1/docs` on your instance.
