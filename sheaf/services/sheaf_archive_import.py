"""Sheaf export-with-images archive (.zip) import.

The async export job can produce a zip carrying `export.json` (the
exact `/v1/export` shape), a README, and `images/<key>` blobs for every
file the account had uploaded. This importer closes the loop the plain
JSON import can't: instead of stripping internal image references
(avatars, markdown embeds, journal/revision image_keys), it re-uploads
the referenced blobs as the importing user's `UploadedFile` rows and
rewrites the references to the new keys.

Layering: the JSON walk is `sheaf_import.run_import`, unchanged - this
module only adds the zip handling and the image restore around it:

1. Parse the zip defensively (bad-zip, per-entry decompressed-size
   caps, `safe_json_loads` element cap, version check) off the event
   loop.
2. Run the tier member-cap precheck (`count_new_members_for_import`)
   BEFORE any storage write, so a cap failure never strands blobs for
   the runner's rollback to orphan.
3. Upload every image key the JSON actually references (collected from
   exactly the fields the importer consumes) through the shared
   `store_imported_image` pipeline - sniff, Pillow off-loop, tier
   storage quota, fresh key under the importing user.
4. Delegate to `sheaf_import.run_import` with the old->new key map.
   On any failure, best-effort delete the blobs written in step 3
   (the UploadedFile rows roll back with the transaction).
5. Discard uploads no written row ended up referencing - e.g. the
   avatar of a member the dedup pass skipped - so a re-import doesn't
   leak storage quota.
"""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.files import _MD_IMAGE_URL_RE, _to_internal_key
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.services.import_dedup import ImportConflictStrategy
from sheaf.services.import_media import (
    ImportImageError,
    StoredImportImage,
    store_imported_image,
    user_can_upload_images,
)
from sheaf.services.import_parsing import ImportPayloadError, safe_json_loads
from sheaf.services.member_limits import enforce_import_member_cap
from sheaf.services.sheaf_import import (
    SheafImportResult,
    count_new_members_for_import,
)
from sheaf.services.sheaf_import import (
    preview as sheaf_preview,
)
from sheaf.services.sheaf_import import (
    run_import as sheaf_run_import,
)
from sheaf.storage import get_storage

logger = logging.getLogger("sheaf.import.sheaf_archive")


# Decompressed-size caps, matching the PluralSpace zip importer's
# rationale: DEFLATE reaches roughly 1000:1, so the 100MB compressed
# upload cap alone does not bound memory. Python's zipfile refuses a
# stream that overruns its declared size, so the declared sizes
# checked here are what reads actually enforce.
_MAX_JSON_DECOMPRESSED = 256 * 1024 * 1024
_MAX_IMAGE_DECOMPRESSED = 100 * 1024 * 1024


@dataclass
class ParsedArchive:
    """A parsed export-with-images zip.

    The zip handle stays open as long as this struct exists so image
    bytes can be fetched lazily by storage key.

    `asset_prefix` is the in-zip directory blobs live under. The native
    Sheaf archive uses ``images/``; the OpenPlural bundle reuses this
    same struct with ``assets/`` (see ``openplural_import``).
    """

    data: dict
    zf: zipfile.ZipFile
    image_keys: set[str] = field(default_factory=set)
    asset_prefix: str = "images/"

    def read_image(self, key: str) -> bytes | None:
        """Bytes for `<asset_prefix><key>`, or None when absent / over-cap."""
        path = f"{self.asset_prefix}{key}"
        if key not in self.image_keys:
            return None
        try:
            if self.zf.getinfo(path).file_size > _MAX_IMAGE_DECOMPRESSED:
                return None
            with self.zf.open(path) as fh:
                return fh.read()
        except KeyError:
            return None


def parse_archive(blob: bytes) -> ParsedArchive:
    """Open the zip and validate it as a Sheaf export-with-images archive.

    Raises ImportPayloadError with a user-facing message on anything
    that's not recognisable as one.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ImportPayloadError("file is not a valid zip archive") from exc

    names = set(zf.namelist())
    if "export.json" not in names:
        raise ImportPayloadError(
            "Sheaf archive must contain export.json (is this a Sheaf "
            "export-with-images zip?)"
        )

    if zf.getinfo("export.json").file_size > _MAX_JSON_DECOMPRESSED:
        raise ImportPayloadError(
            "export.json decompresses to more than "
            f"{_MAX_JSON_DECOMPRESSED // (1024 * 1024)}MB; refusing to parse"
        )

    try:
        data_raw = zf.read("export.json")
    except KeyError as exc:  # pragma: no cover - membership checked above
        raise ImportPayloadError(f"could not read entry: {exc}") from exc

    # safe_json_loads caps the parsed element count (json-bomb guard,
    # same as every other importer's parse path) and raises
    # ImportPayloadError itself on bad JSON.
    data = safe_json_loads(data_raw)

    if not isinstance(data, dict) or data.get("version") not in {"1", "2"}:
        raise ImportPayloadError(
            "export.json is not a valid Sheaf export (missing or "
            "unsupported version field - expected 1 or 2)"
        )

    image_keys = {
        n.removeprefix("images/")
        for n in names
        if n.startswith("images/") and not n.endswith("/")
    }
    return ParsedArchive(data=data, zf=zf, image_keys=image_keys)


# A worst-case parse decompresses and JSON-loads up to
# _MAX_JSON_DECOMPRESSED bytes in one go - far too heavy for the event
# loop the import runner shares with live requests. Same pattern (and
# rationale) as the PluralSpace zip importer.
_parse_semaphore: asyncio.Semaphore | None = None


def _get_parse_semaphore() -> asyncio.Semaphore:
    global _parse_semaphore
    if _parse_semaphore is None:
        _parse_semaphore = asyncio.Semaphore(2)
    return _parse_semaphore


async def parse_archive_async(blob: bytes) -> ParsedArchive:
    async with _get_parse_semaphore():
        return await asyncio.to_thread(parse_archive, blob)


def preview(parsed: ParsedArchive):
    """The native preview summary plus the archive's image count.

    Returns (SheafPreviewSummary, image_count). Writes nothing.
    """
    return sheaf_preview(parsed.data), len(parsed.image_keys)


# --- Image reference collection ---------------------------------------------

# The fields run_import resolves image references in. Collected here so
# only blobs the import will actually try to use get uploaded; an
# images/ entry nothing references is ignored outright.
_MD_FIELDS_SYSTEM = ("description", "note")
_MD_FIELDS_MEMBER = ("description", "note")


def collect_image_references(data: dict) -> dict[str, list[str]]:
    """Map every referenced internal storage key to the records using it.

    Walks exactly the fields `sheaf_import.run_import` rewrites:
    system/member avatar_url, markdown bodies (system, members, groups,
    journals, revisions, messages, reminders), and the journal/revision
    image_keys caches. Anything else in the zip's images/ directory is
    not referenced and never uploaded.

    The per-key descriptors ("member <export-id> avatar", ...) feed the
    missing-image warning events so the import report can say WHICH
    records lost a reference. Deliberately export-side ids, never names:
    job events are stored as plaintext JSONB while names live in
    encrypted columns, and an import report must not downgrade that.
    """
    refs: dict[str, list[str]] = {}

    def _add(key: str | None, site: str) -> None:
        if not key:
            return
        # Dedupe per key: one record can reference the same image more
        # than once (body markdown + the image_keys cache, or repeated
        # embeds) and should still read as one site.
        sites = refs.setdefault(key, [])
        if site not in sites:
            sites.append(site)

    def _from_url(value: object, site: str) -> None:
        if isinstance(value, str) and value:
            _add(_to_internal_key(value), site)

    def _from_md(text: object, site: str) -> None:
        if not isinstance(text, str) or not text:
            return
        for m in _MD_IMAGE_URL_RE.finditer(text):
            _add(_to_internal_key(m.group(2)), site)

    def _from_key_list(values: object, site: str) -> None:
        if not isinstance(values, list):
            return
        for v in values:
            _from_url(v, site)

    sys_data = data.get("system")
    if isinstance(sys_data, dict):
        _from_url(sys_data.get("avatar_url"), "system avatar")
        for fld in _MD_FIELDS_SYSTEM:
            _from_md(sys_data.get(fld), f"system {fld}")

    for m_data in data.get("members", []):
        if not isinstance(m_data, dict):
            continue
        mid = m_data.get("id", "?")
        _from_url(m_data.get("avatar_url"), f"member {mid} avatar")
        _from_url(m_data.get("banner_url"), f"member {mid} banner")
        for fld in _MD_FIELDS_MEMBER:
            _from_md(m_data.get(fld), f"member {mid} {fld}")

    for g_data in data.get("groups", []):
        if isinstance(g_data, dict):
            _from_md(
                g_data.get("description"),
                f"group {g_data.get('id', '?')} description",
            )

    for section, label in (
        ("journals", "journal"),
        ("messages", "message"),
        ("reminders", "reminder"),
    ):
        for row in data.get(section, []):
            if isinstance(row, dict):
                _from_md(row.get("body"), f"{label} {row.get('id', '?')}")

    for row in data.get("revisions", []):
        if not isinstance(row, dict):
            continue
        site = (
            f"{row.get('target_type', 'revision')} history "
            f"(revision {row.get('id', '?')})"
        )
        _from_md(row.get("body"), site)
        _from_key_list(row.get("image_keys"), site)

    for row in data.get("journals", []):
        if isinstance(row, dict):
            _from_key_list(
                row.get("image_keys"), f"journal {row.get('id', '?')}"
            )

    return refs


# --- Import ------------------------------------------------------------------


@dataclass
class SheafArchiveImportResult:
    """The native import result plus the image-restore accounting."""

    base: SheafImportResult
    images_imported: int = 0
    images_discarded: int = 0
    warnings: list[str] = field(default_factory=list)
    # One entry per referenced key absent from the archive (or over the
    # per-image size limit): (storage key, "site; site; ..." summary of
    # the records whose reference was removed). The runner emits one
    # warning event per entry with the key as record_ref, so the import
    # report names exactly what lost its image.
    missing_images: list[tuple[str, str]] = field(default_factory=list)


async def run_import(
    parsed: ParsedArchive,
    system: System,
    user: User,
    db: AsyncSession,
    *,
    images: bool = True,
    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.SKIP,
    **section_options,
) -> SheafArchiveImportResult:
    """Restore an export-with-images archive into the user's system.

    `section_options` are `sheaf_import.run_import`'s section toggles
    (system_profile, member_ids, fronts, ...), passed through verbatim.
    """
    result = SheafArchiveImportResult(base=SheafImportResult())
    warnings = result.warnings
    data = parsed.data

    # Member-cap precheck FIRST: enforce_import_member_cap raises the
    # same user-facing error run_import would, but before any blob has
    # been written to storage.
    member_ids = section_options.get("member_ids")
    new_members = await count_new_members_for_import(
        data,
        system,
        db,
        member_ids=member_ids,
        conflict_strategy=conflict_strategy,
    )
    await enforce_import_member_cap(db, system, new_members)

    # Upload the referenced blobs and build the old->new key map.
    key_map: dict[str, str] = {}
    uploaded: dict[str, StoredImportImage] = {}
    if images and parsed.image_keys:
        references = collect_image_references(data)
        if references and not user_can_upload_images(user):
            warnings.append(
                "Skipped image restore: image uploads are not enabled for "
                "this account. Image references were removed as in a "
                "plain JSON import."
            )
            references = {}

        quota_stopped = False
        # Count cap: the quota bounds bytes, this bounds normalize_image
        # passes (see the setting's rationale in config.py).
        restore_cap = settings.max_import_restored_images
        for key in sorted(references):
            if len(uploaded) >= restore_cap:
                warnings.append(
                    f"Image restore stopped after {restore_cap} files "
                    "(per-import limit, MAX_IMPORT_RESTORED_IMAGES). "
                    "Remaining image references were removed."
                )
                break
            raw = parsed.read_image(key)
            if raw is None:
                sites = references[key]
                shown = "; ".join(sites[:5])
                if len(sites) > 5:
                    shown += f"; and {len(sites) - 5} more"
                result.missing_images.append((key, shown))
                continue
            purpose = "bio" if key.startswith("bios/") else "avatar"
            try:
                stored = await store_imported_image(
                    raw, db=db, user=user, purpose=purpose
                )
            except ImportImageError as exc:
                if exc.reason == "quota_full":
                    warnings.append(
                        "Image restore stopped: storage quota reached. "
                        "Remaining image references were removed."
                    )
                    quota_stopped = True
                    break
                cause = (
                    "unsupported format"
                    if exc.reason == "bad_format"
                    else "rejected by the image normaliser"
                )
                warnings.append(f"Image {key!r} could not be restored ({cause}).")
                continue
            key_map[key] = stored.key
            uploaded[key] = stored
        if quota_stopped:
            pass  # warning already appended; unmapped refs strip below

    # The JSON walk. On ANY failure, scrub the blobs we just wrote -
    # the runner rolls the DB back, but storage writes don't roll back
    # by themselves.
    used_keys: set[str] = set()
    try:
        result.base = await sheaf_run_import(
            data,
            system,
            db,
            conflict_strategy=conflict_strategy,
            image_key_map=key_map,
            used_image_keys=used_keys,
            **section_options,
        )
    except Exception:
        storage = get_storage()
        for stored in uploaded.values():
            try:
                await storage.delete(stored.key)
            except Exception:  # pragma: no cover - best-effort scrub
                logger.warning(
                    "could not scrub blob %s after failed archive import",
                    stored.key,
                )
        raise

    # Discard uploads nothing referenced in the end (e.g. the avatar of
    # a member the dedup pass skipped) so re-imports don't leak quota.
    storage = get_storage()
    for old_key, stored in uploaded.items():
        if old_key in used_keys:
            result.images_imported += 1
            continue
        result.images_discarded += 1
        try:
            await storage.delete(stored.key)
        except Exception:  # pragma: no cover - best-effort scrub
            logger.warning("could not delete unused imported blob %s", stored.key)
        # The UploadedFile row is still pending in this session (the
        # import commits once, at finalize), so expunge it rather than
        # issuing a DELETE.
        if stored.row in db.new:
            db.expunge(stored.row)
        else:  # pragma: no cover - row already flushed
            await db.delete(stored.row)

    result.warnings = warnings + result.base.warnings
    return result
