import os
from dataclasses import dataclass


@dataclass
class LarkConfig:
    app_id: str
    app_secret: str


def load_lark_config() -> LarkConfig:
    """
    Decouple credentials from business code:
    - Read from environment variables
    - Fail fast if missing
    """
    app_id = os.getenv("LARK_APP_ID", "").strip()
    app_secret = os.getenv("LARK_APP_SECRET", "").strip()

    if not app_id or not app_secret:
        raise RuntimeError(
            "Missing Lark credentials. Please set LARK_APP_ID and LARK_APP_SECRET in environment variables."
        )

    return LarkConfig(app_id=app_id, app_secret=app_secret)
