"""Project configuration; mock mode is safe by default."""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent


def use_mock_data():
    load_dotenv(PROJECT_DIR / ".env", override=False)
    value = os.getenv("USE_MOCK_DATA", "true").strip().lower()
    if value not in ("true", "false"):
        raise ValueError("USE_MOCK_DATA must be true or false.")
    return value == "true"
