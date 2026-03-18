from fastapi import APIRouter

from sheaf.api.v1 import auth, custom_fields, export, fronts, groups, members, systems, tags

v1_router = APIRouter(prefix="/v1")

v1_router.include_router(auth.router)
v1_router.include_router(systems.router)
v1_router.include_router(members.router)
v1_router.include_router(fronts.router)
v1_router.include_router(groups.router)
v1_router.include_router(tags.router)
v1_router.include_router(custom_fields.router)
v1_router.include_router(export.router)
