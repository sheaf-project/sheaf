from fastapi import APIRouter

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

v1_router = APIRouter(prefix="/v1")

v1_router.include_router(auth.router)
v1_router.include_router(systems.router)
v1_router.include_router(members.router)
v1_router.include_router(fronts.router)
v1_router.include_router(groups.router)
v1_router.include_router(tags.router)
v1_router.include_router(custom_fields.router)
v1_router.include_router(export.router)
v1_router.include_router(files.router)
v1_router.include_router(sp_import.router)
v1_router.include_router(admin.router)
# File serve catch-all MUST be last — {path:path} would shadow other routes
v1_router.include_router(files.serve_router)
