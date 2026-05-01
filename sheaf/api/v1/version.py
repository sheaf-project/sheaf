from fastapi import APIRouter

from sheaf import __version__
from sheaf.config import settings

router = APIRouter(tags=["version"])


@router.get("/version")
async def get_version() -> dict[str, str | None]:
    return {
        "version": __version__,
        "git_commit": settings.sheaf_git_commit or None,
        "git_tag": settings.sheaf_git_tag or None,
        "build_time": settings.sheaf_build_time or None,
        "mode": settings.sheaf_mode.value,
        # VAPID public key is genuinely public — recipients need it to
        # subscribe to web push from the unauthenticated redeem page.
        "vapid_public_key": settings.vapid_public_key or None,
    }
