"""Environment loading helpers for CLI entry points."""

from dotenv import load_dotenv


def load_project_env() -> None:
    """Load local override env first, then the default .env file."""
    load_dotenv(".env.local")
    load_dotenv()
