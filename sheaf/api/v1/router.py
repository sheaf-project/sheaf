from fastapi import APIRouter, Depends

from sheaf.api.v1 import (
    admin,
    announcements,
    auth,
    client_settings,
    custom_fields,
    export,
    files,
    fronts,
    groups,
    members,
    sheaf_import,
    sp_import,
    system_safety,
    systems,
    tags,
    webhooks,
)
from sheaf.auth.dependencies import require_scope

v1_router = APIRouter(prefix="/v1")

# Auth, admin, announcements: no scope enforcement
v1_router.include_router(auth.router)
v1_router.include_router(admin.router)
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
    sheaf_import.router,
    dependencies=[Depends(require_scope("import:write"))],
)
v1_router.include_router(webhooks.router)
# File serve catch-all MUST be last — {path:path} would shadow other routes
v1_router.include_router(files.serve_router)
