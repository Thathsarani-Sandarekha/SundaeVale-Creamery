"""Import target for uvicorn. Run from repository root with backend prefix."""
from .settings import load_project_env
from .api import create_app

load_project_env()
app = create_app()
