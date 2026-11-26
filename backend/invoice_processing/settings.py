"""Local development configuration for the invoice application.

Shell/Conda environment variables override files. The root .env is preferred
over the legacy .env.invoice so GROQ_API_KEY already present in .env is used.
"""
from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_project_env(base_dir: Path | None = None) -> None:
    root = Path(base_dir) if base_dir is not None else PROJECT_ROOT
    for candidate in (root / ".env", root / ".env.invoice", root / "backend" / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
