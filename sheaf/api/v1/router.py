from fastapi import APIRouter, Depends

from sheaf.api.v1 import (
    account,
    admin,
    analytics,
    announcements,
    auth,
    client_settings,
    custom_fields,
    export,
    files,
    fronts,
    groups,
    journals,
    members,
    messages,
    notification_channels,
    notifications_public,
    pk_import,
    polls,
    reminders,
    retention,
    sheaf_import,
    sp_import,
    system_safety,
    systems,
    tags,
    version,
    watch_tokens,
    webhooks,
)
from sheaf.auth.dependencies import require_scope

v1_router = APIRouter(prefix="/v1")

# Public (no auth): build provenance for verifiability tooling.
v1_router.include_router(version.router)

# Auth, admin, announcements: no scope enforcement
v1_router.include_router(auth.router)
v1_router.include_router(admin.router)
# Account-level (Article 15 etc.) — session/JWT auth only, body-gated
# step-up. Lives outside the scope-gated section since API key access
# is refused inline by the endpoint.
v1_router.include_router(account.router)
v1_router.include_router(announcements.admin_router)
v1_router.include_router(announcements.public_router)
v1_router.include_router(
    client_settings.router,
    dependencies=[Depends(require_scope("settings:read"))],
)

# Resource routers: router-level read scope dep + per-endpoint write scope dep
v1_router.include_router(
    systems.router,
    dependencies=[Depends(require_scope("system:read"))],
)
v1_router.include_router(
    system_safety.router,
    dependencies=[Depends(require_scope("system:read"))],
)
v1_router.include_router(
    members.router,
    dependencies=[Depends(require_scope("members:read"))],
)
v1_router.include_router(
    fronts.router,
    dependencies=[Depends(require_scope("fronts:read"))],
)
v1_router.include_router(
    analytics.router,
    dependencies=[Depends(require_scope("fronts:read"))],
)
v1_router.include_router(
    groups.router,
    dependencies=[Depends(require_scope("groups:read"))],
)
v1_router.include_router(
    tags.router,
    dependencies=[Depends(require_scope("tags:read"))],
)
v1_router.include_router(
    custom_fields.router,
    dependencies=[Depends(require_scope("fields:read"))],
)
v1_router.include_router(
    journals.router,
    dependencies=[Depends(require_scope("journals:read"))],
)
v1_router.include_router(
    retention.router,
    dependencies=[Depends(require_scope("system:read"))],
)
v1_router.include_router(
    export.router,
    dependencies=[Depends(require_scope("export:read"))],
)

# Files: upload gated by members:write (used for avatars/bios); serve is public
v1_router.include_router(files.router)
v1_router.include_router(
    sp_import.router,
    dependencies=[Depends(require_scope("import:write"))],
)
v1_router.include_router(
    pk_import.router,
    dependencies=[Depends(require_scope("import:write"))],
)
v1_router.include_router(
    sheaf_import.router,
    dependencies=[Depends(require_scope("import:write"))],
)
v1_router.include_router(webhooks.router)

# Notifications: owner-side (auth+scope), recipient-side (public)
v1_router.include_router(
    watch_tokens.router,
    dependencies=[Depends(require_scope("notifications:read"))],
)
v1_router.include_router(
    notification_channels.router,
    dependencies=[Depends(require_scope("notifications:read"))],
)
v1_router.include_router(notifications_public.router)
v1_router.include_router(
    reminders.router,
    dependencies=[Depends(require_scope("notifications:read"))],
)
v1_router.include_router(
    polls.router,
    dependencies=[Depends(require_scope("polls:read"))],
)

# Messages share the members:* scope set rather than minting new scopes —
# the audience and authorization domain are the same as member content.
v1_router.include_router(
    messages.router,
    dependencies=[Depends(require_scope("members:read"))],
)

# File serve catch-all MUST be last — {path:path} would shadow other routes
v1_router.include_router(files.serve_router)
