"""Builtin ONScripter adapter."""

from vn_stream.runtimes.native import resolve_native_runtime

from .assets import PROVIDER
from .compatibility import runtime_compatibility
from .inspection import CAPABILITIES, inspect_project
from .launch import prepare_launch


class OnsAdapter:
    engine_id = "ons"
    directory_name = "onscripter"
    display_name = "ONScripter"
    display_name_key = "engine.name.ons"
    capabilities = CAPABILITIES
    asset_provider = PROVIDER
    runtime_compatibility = staticmethod(runtime_compatibility)
    inspect_project = staticmethod(inspect_project)
    resolve_runtime = staticmethod(resolve_native_runtime)
    prepare_launch = staticmethod(prepare_launch)
