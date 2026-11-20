"""Builtin Ren'Py adapter."""

from .assets import PROVIDER
from .inspection import CAPABILITIES, inspect_project
from .launch import prepare_launch, resolve_runtime


class RenPyAdapter:
    engine_id = "renpy"
    directory_name = "renpy"
    display_name = "Ren'Py"
    capabilities = CAPABILITIES
    asset_provider = PROVIDER
    inspect_project = staticmethod(inspect_project)
    resolve_runtime = staticmethod(resolve_runtime)
    prepare_launch = staticmethod(prepare_launch)
