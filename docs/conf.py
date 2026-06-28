import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from django_zodb_backend import __version__

project = "django-zodb-backend"
author = "django-zodb contributors"
copyright = f"{datetime.now().year}, {author}"
release = __version__
version = release

extensions = [
    "sphinx_copybutton",
]

templates_path = []
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
language = "en"
source_suffix = ".rst"
master_doc = "index"
pygments_style = "sphinx"

html_theme = "furo"
html_title = f"{project} {release}"
html_static_path = ["_static"]
html_theme_options = {
    "source_repository": "https://github.com/django-zodb/django-zodb-backend/",
    "source_branch": "main",
    "source_directory": "docs/",
    "navigation_with_keys": True,
}
html_css_files = []

copybutton_prompt_text = r"(>>> |\.\.\. |\$ )"
copybutton_prompt_is_regexp = True
