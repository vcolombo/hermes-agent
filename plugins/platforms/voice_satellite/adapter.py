"""Voice satellite platform adapter (Wyoming protocol)."""

import importlib.util
import sys
from pathlib import Path


def _import_sibling(name: str):
    """Load a module that lives next to this file.

    plugins/platforms/ has no __init__.py on purpose (plugins are not
    importable as dotted packages), so siblings load by file path — the
    same mechanism tests/gateway/_plugin_adapter_loader.py uses.
    """
    mod_key = f"hermes_voice_satellite_{name}"
    if mod_key in sys.modules:
        return sys.modules[mod_key]
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(mod_key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = module
    spec.loader.exec_module(module)
    return module
