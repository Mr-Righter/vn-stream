"""Shared visual tokens used by Ant Design, bootstrap assets and installation metadata."""

import json
from importlib.resources import files

THEME = json.loads(files(__package__).joinpath("theme.json").read_text(encoding="utf-8"))
