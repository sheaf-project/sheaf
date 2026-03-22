from fastapi import APIRouter, Depends

from sheaf.api.v1 import (
    admin,
    auth,
    custom_fields,
    export,
    files,
    fronts,
    groups,
    members,
    sp_import,
    systems,
    tags,
)
from sheaf.auth.dependencies import require_scope

v1_router = APIRouter(prefix="/v1")

# Auth and admin: no scope enforcement (auth has its own rules; admin uses get_admin_user)
v1_router.include_router(auth.router)
v1_router.include_router(admin.router)

# Resource routers: router-level read scope dep + per-endpoint write scope dep
v1_router.include_router(
    systems.router,
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
v1_router.include_router(sp_import.router)
# File serve catch-all MUST be last — {path:path} would shadow other routes
v1_router.include_router(files.serve_router)
