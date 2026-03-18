import logging
import sys
from enum import StrEnum
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings

logger = logging.getLogger("sheaf")


class SheafMode(StrEnum):
    SELFHOSTED = "selfhosted"
    SAAS = "saas"


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://sheaf:changeme@db:5432/sheaf"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Encryption
    sheaf_encryption_key: str | None = None
    sheaf_data_dir: Path = Path("data")

    # Auth
    jwt_secret_key: str = "changeme-in-production"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 30
    jwt_algorithm: str = "HS256"
    session_expire_hours: int = 24

    # Mode
    sheaf_mode: SheafMode = SheafMode.SELFHOSTED

    # aaS settings
    free_tier_front_retention_days: int = 30

    # Server
    sheaf_port: int = 8000
    sheaf_host: str = "0.0.0.0"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def get_encryption_key(self) -> bytes:
        """Get or auto-generate the Fernet encryption key."""
        if self.sheaf_encryption_key:
            return self.sheaf_encryption_key.encode()

        key_path = self.sheaf_data_dir / "encryption.key"

        if key_path.exists():
            return key_path.read_bytes().strip()

        # Auto-generate and persist
        self.sheaf_data_dir.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        key_path.chmod(0o600)

        logger.warning("=" * 72)
        logger.warning(
            "AUTO-GENERATED ENCRYPTION KEY — BACK THIS UP OR YOU "
            "LOSE ALL ENCRYPTED DATA FOREVER"
        )
        logger.warning("Key file: %s", key_path.resolve())
        logger.warning("Key value: %s", key.decode())
        logger.warning(
            "Set SHEAF_ENCRYPTION_KEY in .env to use your own key "
            "and suppress this warning."
        )
        logger.warning("=" * 72)

        return key


settings = Settings()


def _validate_settings() -> None:
    """Check for insecure defaults and warn loudly."""
    problems = []
    if settings.jwt_secret_key == "changeme-in-production":
        problems.append("JWT_SECRET_KEY is set to the default value")
    if "changeme" in settings.database_url:
        problems.append("DATABASE_URL contains default password")

    if settings.sheaf_mode == SheafMode.SAAS and problems:
        logger.critical("REFUSING TO START IN SAAS MODE WITH INSECURE DEFAULTS:")
        for p in problems:
            logger.critical("  - %s", p)
        sys.exit(1)
    elif problems:
        for p in problems:
            logger.warning("INSECURE DEFAULT: %s", p)
