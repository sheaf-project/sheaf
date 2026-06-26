# Sheaf OpenPlural support: implementation log

[OpenPlural](https://github.com/skylartaylor/openplural) is a draft (v0.1)
interchange format for plural-system data: systems, members, fronting history,
groups, taxonomy, custom fields, notes, and assets, with a registered
`extensions.<app>` namespace for anything an app models that the core spec does
not yet cover. The point of it is that one good exporter beats N pairwise
converters: map your records to the OpenPlural core once and every other adopter
can read them.

Sheaf is a founding adopter. Its exporter lives in
`sheaf/services/openplural_export.py` and the inverse importer in
`sheaf/services/openplural_import.py`; the import runner that wires them to the
async job system is `sheaf/services/openplural_import_runner.py`.

## How to use this file

Every Sheaf OpenPlural export stamps two versions in its `producer` block:

- `producer.app_version` - the Sheaf release that produced the file (e.g.
  `1.1.0`), taken from package metadata.
- `producer.exporter_version` - the version of the *mapping logic* itself
  (`OPENPLURAL_IMPL_VERSION`, currently `0.1.0`). This moves independently of
  the Sheaf release whenever the field mapping changes in a way worth tracking.

When you are handed an export and need to know exactly how that build mapped its
data - which fields went to core records, what got parked under
`extensions.sheaf.*`, and what the known limitations were - read the producer
stamp, then find the matching `## Sheaf X.Y.Z` section in the implementation log
below. The mapping is intentionally diagnosable from the file alone.

This document is the per-field rationale and running changelog; the code
comments point back here rather than duplicating it.

## Producer and version stamping

The envelope carries a `producer` object describing what wrote it:

| Field | Value | Source |
|---|---|---|
| `producer.app` | `"Sheaf"` | `APP_NAME` |
| `producer.app_id` | `"sheaf"` | `APP_ID`, the registered namespace key |
| `producer.app_version` | the Sheaf release, e.g. `"1.1.0"` | package metadata (`sheaf.__version__`) |
| `producer.exporter_version` | `"0.1.0"` | `OPENPLURAL_IMPL_VERSION` |

Separately, the top-level `openplural_version` field records the *spec* version
the file targets, currently `"0.1"` (`OPENPLURAL_VERSION`). This is not the
Sheaf version and not the exporter version - it is the contract the document
claims to satisfy.

The importer is strict about it. `SUPPORTED_VERSIONS = {"0.1"}`, and
`_check_version` raises an `ImportPayloadError` for any other
`openplural_version` rather than silently part-importing a document it does not
understand. A file stamped, say, `openplural_version: "0.2"` will be rejected by
a build that only knows `0.1`; this is deliberate per the spec.

## Provenance and lineage

Sheaf records where data came from at two levels.

### Per-record `source_refs`

A record that originated in another app carries a `source_refs[]` entry so a
later sync or reconciliation pass can match it back to its origin. Today the
only producer of these is the PluralKit importer: a member that was imported
from PluralKit keeps its PK HID, and on export that becomes

```json
"source_refs": [
  {"app": "pluralkit", "collection": "members", "id": "wyyetr"}
]
```

On the way back in, `_pluralkit_id` pulls the `pluralkit` ref out of
`source_refs` and restores it to the member's `pluralkit_id` field, so the
cross-reference survives a round-trip.

### File-level `extensions.sheaf.lineage[]`

The envelope also carries a forward-compatible lineage chain under
`extensions.sheaf.lineage`. Each export appends one entry describing the hop:

```json
"extensions": {
  "sheaf": {
    "lineage": [
      {
        "app": "sheaf",
        "app_version": "1.1.0",
        "exporter_version": "0.1.0",
        "exported_at": "2026-06-18T12:00:00Z"
      }
    ]
  }
}
```

**Limitation, stated honestly:** Sheaf does not yet *persist* inherited lineage
across a database round-trip. There is no column for it, so when you import a
file and then re-export, the new file starts a fresh, Sheaf-only lineage chain
rather than accumulating the prior hops. The structure is emitted now purely for
forward-compatibility; full accumulation across round-trips is tracked against
upstream issue #7. On import, `_note_lineage` surfaces any incoming lineage as
an `info` event on the import job ("file carries lineage from N prior
export(s)") but does not store it. `build_envelope` does accept an
`inherited_lineage` argument and will prepend it when given - the gap is purely
that nothing in the DB layer can hand it back on the next export yet.

## Delivery shapes

The same `build_envelope` builder backs two delivery shapes, which differ only
in how assets travel.

### Sync JSON: `GET /v1/export?format=openplural`

A single JSON document. Assets are **uri-only**: each `Asset` carries its avatar
or image URL but no bytes. Because there are no blobs, the export emits a
top-level `info` warning with code `asset_uri_only`:

```json
{"level": "info", "code": "asset_uri_only",
 "message": "Assets are referenced by URL only; export with images (the .openplural.zip bundle) to include the binary blobs."}
```

Use this for a quick portable copy of structured data (systems, members, fronts,
groups, tags, custom fields, journals); the images themselves only resolve if
the destination can reach those URLs.

### Async bundle: `.openplural.zip`

Produced by the async export job path. The zip contains `openplural.json` plus
an `assets/<storage_key>` entry for every internal image blob. In this shape each
bundled asset additionally carries pointers under its own namespace:

```json
{
  "id": "…",
  "kind": "avatar",
  "uri": "/v1/files/<key>",
  "extensions": {
    "sheaf": {
      "bundle_path": "assets/<storage_key>",
      "storage_key": "<storage_key>"
    }
  }
}
```

Only Sheaf-internal references get a `bundle_path`; external CDN URLs (Gravatar,
DiceBear, a user-typed link) stay uri-only in both shapes because Sheaf has no
bytes to bundle for them. The bare storage key is recoverable from the unchanged
`uri` via `_to_internal_key`, which is how the importer's `_AssetMap` lines the
in-zip blob back up with its asset.

Note that the official bundle-path convention is still pending upstream issue #9.
Until that lands, the pointer lives in Sheaf's namespace and `uri` is always kept
present so the document stays spec-valid for an app that does not understand
`extensions.sheaf.bundle_path`.

---

## Sheaf 1.1.0 - initial OpenPlural v0.1 support

First release with OpenPlural import/export. `OPENPLURAL_IMPL_VERSION = 0.1.0`,
targeting spec `openplural_version 0.1`. The exporter is a pure transform over
the native Article-20 export dict (version `"2"`); the importer is its inverse
and translates back to that same native dict before delegating to the native
importer, so all the import guards (member cap, safe-JSON, decompressed-size
bound, avatar normalisation, business caps, fresh UUIDs, tenant scoping) live in
one place and cannot drift per-format.

### Direct mappings

Sheaf fields that land in OpenPlural core records.

| Sheaf (native export) | OpenPlural core record / field |
|---|---|
| `system.id` / `name` / `description` / `tag` / `color` | `System.id` / `name` / `description` / `tag` / `color` |
| `system.avatar_url` | `Asset` (kind `avatar`) + `System.avatar_asset_id` |
| `system.privacy` | `System.privacy` (visibility bucket; see edge cases) |
| `members[].id` / `name` / `display_name` / `description` / `pronouns` / `color` | `Member.id` / `name` / `display_name` / `description` / `pronouns` / `color` |
| `members[].avatar_url` | `Asset` (kind `avatar`) + `Member.avatar_asset_id` |
| `members[].banner_url` | `Asset` (kind `banner`) + `Member.banner_asset_id` |
| `members[].is_custom_front` | `Member.is_custom_front` |
| `members[].created_at` | `Member.created_at` |
| `members[].privacy` | `Member.privacy` (visibility bucket) |
| `members[].birthday` | `Member.birthday` precision sub-record (see below) |
| `members[].pluralkit_id` | `Member.source_refs[]` (app `pluralkit`; see provenance) |
| `groups[].id` / `name` / `description` / `color` | `Group.id` / `name` / `description` / `color` |
| `groups[].parent_id` | `Group.parent_group_id` |
| `groups[].member_ids` | `GroupMembership[]` (one row per id) |
| `tags[].id` / `name` / `color` | `TaxonomyTerm` (kind `tag`) `.id` / `name` / `color` |
| `tags[].member_ids` | `TaxonomyAssignment[]` (subject_type `member`) |
| `custom_fields[].id` / `name` / `field_type` / `options` | `CustomFieldDefinition.id` / `name` / `field_type` / `options` |
| `custom_fields[].order` | `CustomFieldDefinition.sort_order` |
| `custom_fields[].privacy` | `CustomFieldDefinition.privacy` (visibility bucket) |
| `custom_fields[].values[]` (`member_id`, `value`) | `CustomFieldValue` (subject_type `member`, `field_id`, `subject_id`, `value`) |
| `fronts[].id` / `started_at` / `ended_at` | `FrontPeriod.id` / `started_at` / `ended_at` |
| `fronts[].member_ids` | `FrontPeriod.assignments[]` (one per id, `front_role: "member"`) |
| `fronts[].custom_status` | `FrontPeriod.status` (free text) |
| `journals[].id` / `title` / `body` / `created_at` / `updated_at` | `Note.id` / `title` / `body` / `created_at` / `updated_at` |
| `journals[].visibility` | `Note.visibility` (visibility bucket) |
| `journals[].author_member_ids` | `Note.author_member_ids` |
| `journals[].image_keys` | `Asset` (kind `image`) + `Note.attachment_asset_ids` |
| `images` / internal blobs | `assets[]` (deduplicated by storage key / URL) |

#### Birthday precision mapping

Sheaf stores a birthday as a flat string, either with or without a year, and the
exporter (`_birthday`) derives an OpenPlural precision-aware sub-record from its
shape:

| Sheaf stored value | OpenPlural `birthday` |
|---|---|
| `"YYYY-MM-DD"` (3 parts) | `{"value": "YYYY-MM-DD", "precision": "day", "year_visible": true}` |
| `"MM-DD"` (2 parts, year-less) | `{"value": "MM-DD", "precision": "month_day", "year_visible": false}` |
| anything else | `{"value": <raw>, "precision": "unknown", "year_visible": false}` (handed across opaquely rather than dropped) |

On import, `_birthday_to_native` simply reads `birthday.value` back into the flat
Sheaf string (and tolerates a plain string too).

### `extensions.sheaf.*` (platform-specific data)

Everything Sheaf models that OpenPlural v0.1 has no core record for is preserved
losslessly under the registered `sheaf` namespace, so a round-trip restores it
and another app can at least carry it forward. None of this is lost; it is just
opaque to apps that do not speak Sheaf.

#### Record-level extensions

| Record | `extensions.sheaf` key(s) | Why it is not core (yet) |
|---|---|---|
| System | `note` | Sheaf's system note is separate from `description` and encrypted at rest; no distinct core field for it. |
| System | `date_format`, `replace_fronts_default`, `coalesce_contiguous_fronts`, `delete_confirmation` | App-specific display/behaviour preferences with no spec home. |
| System | `safety`, `retention` | System Safety and retention config; awaits a v0.2 safety module. |
| Member | `note` | Encrypted-at-rest member note, distinct from `description`. |
| Member | `emoji` | No core member-emoji field yet; a candidate shared optional in a later spec (Prism has an analogue). |
| Member | `quick_switch_pin` | Sheaf quick-switch convenience field, app-specific. |
| Member | `notify_on_front_global`, `notify_on_front_self`, `notify_on_front_member_ids` | Front-notification preferences; await a notifications module. |
| Note (journal) | `member_id` | Sheaf journals can be scoped to one member; the core `Note` has no owning-member field. |
| Note (journal) | `author_member_names` | Denormalised author names retained alongside `author_member_ids` for display fidelity. |
| Board post | `board_kind` | Distinguishes system board vs per-member wall; no core distinction yet. |
| Board post | `parent_message_id` | Single-level reply pointer; parks here until `BoardPost.parent_post_id` lands (issue #2). |

#### File-level extensions

These are the native sub-sections with no OpenPlural v0.1 core representation,
carried verbatim under `extensions.sheaf.<key>` (the `_EXT_PASSTHROUGH_SECTIONS`
tuple), plus the lineage chain.

| Key | Why it is parked here |
|---|---|
| `polls` | Polls (options/votes/events) await the v0.2 polls module. |
| `reminders` | Reminders await the v0.2 reminders module. |
| `messages` | Board posts also surface in the `boards` module, but the full native message shape is retained here for lossless round-trip. |
| `revisions` | Journal / member-bio edit history; awaits a spec revision-history shape. |
| `watch_tokens` | Notification watch tokens / channels; await a notifications/export module. |
| `uploaded_files` | The sync JSON's file inventory (no bytes); meaningless without the async zip but kept so nothing is silently dropped. |
| `lineage` | Provenance chain (see Provenance and lineage above). |

On import these are read straight back: messages prefer the `boards` module shape
when present and fall back to the passthrough section otherwise; the rest are
lifted directly into the native dict.

### Edge cases and decisions

- **Privacy / visibility buckets.** Sheaf's `PrivacyLevel`
  (`public` / `friends` / `private`) maps 1:1 onto the OpenPlural visibility
  vocabulary (`{public, friends, private, trusted, unknown}`), rounding
  anything unrecognised to the strictest-safe `"unknown"` rather than
  guessing. Note the *shape*: on **system / member / custom-field** the spec
  models privacy as a Privacy **object** `{"visibility": ..., "source": ...}`,
  so the exporter emits `{"visibility": <bucket>}` (Sheaf has no raw `source`
  detail to carry) and the importer reads the `visibility` field back out. A
  bare-string privacy is still accepted on import for older/lenient files.
  **Note (journal) `visibility`** is a plain string in the spec, not the
  object, and stays a string both directions. (Treating the privacy object as
  a bare string was the cause of the `unhashable type: 'dict'` import crash on
  spec-conformant files, e.g. a PluralSpace export routed through OpenPlural.)
- **Front status is free text.** `fronts[].custom_status` becomes
  `FrontPeriod.status` verbatim; there is no controlled vocabulary on the Sheaf
  side, so none is imposed.
- **Fronting accepts both shapes.** Sheaf stores fronting as intervals
  (`FrontPeriod`). On import it reads `front_periods` directly and also derives
  intervals from `front_events` (a point-in-time switch log): each event sets
  who is fronting until the next event, an empty-assignment event is a gap, and
  the last event stays open-ended (same conversion as the PluralKit switch log).
  A file carrying both representations is de-duplicated by interval + member set.
  Sheaf only ever *emits* `front_periods`, so a re-export normalises events to
  intervals (the fronting information is preserved; the event shape is not).
- **Tags are taxonomy.** Sheaf tags become `TaxonomyTerm` with `kind: "tag"`;
  the importer only lifts terms whose `kind` is `tag` back into Sheaf tags, so a
  future taxonomy of another kind round-trips through extensions rather than
  being mistaken for a tag.
- **PluralKit IDs are source refs.** `pluralkit_id` becomes a
  `source_refs[]` entry with `app: "pluralkit"` and back again (see Provenance).
- **Custom-field options pass through as-is.** Sheaf stores `options` as JSONB
  (`dict | None`); it is emitted and re-read unchanged, so both the array-style
  and object-style option shapes survive.
- **Lineage is not persisted.** A Sheaf re-export does not carry forward the
  lineage of a file it imported; see the limitation under
  [Provenance and lineage](#provenance-and-lineage) and issue #7.

### Preserving data Sheaf cannot model (incoming)

Sheaf maps the subset of OpenPlural it models; without preservation, a file from
another app would lose everything Sheaf has no home for. To avoid being a lossy
hop, the importer captures that residual on import and re-merges it into the next
OpenPlural export. This is the **baseline tier** of the preservation contract
Sheaf proposed upstream ([skylartaylor/openplural#11](https://github.com/skylartaylor/openplural/issues/11)):
file-level and whole-section passthrough.

What is preserved (`services/openplural_archive.py`, `extract_residual`):

| Residual | Source |
| --- | --- |
| Foreign `extensions` namespaces | File-level `extensions` keys other than `sheaf` (e.g. `extensions.prism`). |
| `chat` module | Whole object, re-advertised in `capabilities.modules` on export. |
| `relationships` module | Whole object, re-advertised in `capabilities.modules` on export. |
| `front_comments` | Time-anchored comments on fronting; Sheaf fronts have a free-text status but no per-comment model. (`front_events` are NOT preserved here - they are imported as intervals, see the fronting edge case below.) |
| Non-tag `taxonomy_terms` + their assignments | Sheaf models only `kind: "tag"`; roles and other kinds are preserved. |

Storage: the residual is JSON, zlib-compressed, then encrypted at rest (it can
carry message bodies and other content Sheaf treats as sensitive), and parked on
`System.openplural_archive`. It is bounded by `OPENPLURAL_MAX_PRESERVED_MB`
(default 8, measured on the raw JSON); a file over that has its residual dropped
with a warning rather than stored unbounded. The residual rides the native
Article-20 export (decrypted) and is deleted with the account, so it is the
user's data with the usual export and erasure coverage. Re-importing from a
second app merges namespaces rather than clobbering the first.

**Not yet preserved (the "full passthrough" follow-up):** per-record foreign
`extensions` (e.g. `extensions.prism` on one member). Re-attaching those on
export needs stable per-record identity (via `source_refs`), which Sheaf does
not yet track for arbitrary imported records. When an incoming file carries
per-record foreign extensions, the importer emits a one-off warning rather than
silently dropping them.

### Known gaps and open spec issues

Much of what currently round-trips via `extensions.sheaf.*` should move to core
records or dedicated modules if the matching upstream work lands. Sheaf filed
issues #2 through #9 against
[skylartaylor/openplural](https://github.com/skylartaylor/openplural) toward
that (drafts live in `../sheaf-design-docs/openplural-adoption/`):

- **#2 - Add nullable `parent_post_id` to `BoardPost`.** Board posts carry a
  single-level reply pointer (`parent_message_id`) with no core field today; it
  parks under `extensions.sheaf.parent_message_id`.
- **#3 - Preserve author display names on `Note` / `BoardPost`.** Snapshot the
  author name so a record stays legible after the authoring member is deleted;
  Sheaf carries `author_member_names` under `extensions.sheaf` meanwhile.
- **#4 - A `revisions` module for edit history.** Journal/bio/message edit
  history has no core shape, so it parks under `extensions.sheaf.revisions`.
- **#5 - `SourceRef` per-instance disambiguation for self-hosted apps.** Two
  self-hosted Sheaf instances are indistinguishable as a `SourceRef` `app`
  today.
- **#6 - Importers must preserve and append `source_refs`, not replace.** So a
  record's full cross-app pedigree survives each hop. Sheaf appends from day one.
- **#7 - Envelope-level `lineage[]` for file provenance.** Define how the
  file-journey chain accumulates across round-trips; Sheaf emits
  `extensions.sheaf.lineage` now but cannot persist an inherited chain yet (see
  the limitation above).
- **#8 - Specify markdown flavour and in-body image embed syntax.** Pin how
  `description` / `body` markdown and embedded image references are written so
  bodies render identically across apps.
- **#9 - Standardise the `.openplural` zip bundle format.** Where bundled asset
  bytes live in the archive, so the `bundle_path` pointer can move out of the
  Sheaf namespace into the core asset shape.

The data forced into `extensions.sheaf.*` until those land: the polls and
reminders payloads (await the v0.2 modules), System Safety / retention config,
journal-and-bio revision history (#4), and the notification config
(`watch_tokens` plus the member `notify_on_front_*` preferences and
`quick_switch_pin`).

---

## Updating this log

When the mapping logic changes - a field moves from `extensions.sheaf.*` into a
new core record, a new section is mapped, an enum is remapped, an upstream issue
lands - do two things:

1. Bump `OPENPLURAL_IMPL_VERSION` in `sheaf/services/openplural_export.py` (and
   add the new value to the importer's `SUPPORTED_VERSIONS` if the change is not
   backward-compatible for reading older files).
2. Add a new dated `## Sheaf X.Y.Z` section here describing exactly what changed.

The goal is that an export stamped with any past `app_version` /
`exporter_version` remains diagnosable from this file alone: someone holding an
old file should be able to look up the matching section and know precisely how
that build mapped their data.
