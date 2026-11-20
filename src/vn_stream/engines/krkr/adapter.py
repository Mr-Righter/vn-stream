"""Builtin krkr adapter."""

from vn_stream.runtimes.native import resolve_native_runtime

from .assets import PROVIDER
from .inspection import CAPABILITIES, inspect_project
from .launch import prepare_launch


class KrkrAdapter:
    engine_id = "krkr"
    directory_name = "kirikiri"
    required_resources = {
        "krkr2": ("Resources/locale/en_us.xml", "Resources/vn-stream-alsoft.conf"),
        "krkr-rs": ("LICENSE", "LICENSE-IJG.txt", "README.txt"),
        "krkr-next": (
            "lib/libengine_api.so",
            "LICENSE",
            "LICENSE-components.txt",
            "THIRD_PARTY-NOTICES.txt",
            "README.txt",
        ),
    }
    display_name = "Kirikiri"
    display_name_key = "engine.name.krkr"
    capabilities = CAPABILITIES
    asset_provider = PROVIDER
    inspect_project = staticmethod(inspect_project)
    resolve_runtime = staticmethod(resolve_native_runtime)
    prepare_launch = staticmethod(prepare_launch)
