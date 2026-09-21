"""Load the integration's dependency-free modules without importing Home Assistant.

``custom_components/arbor/__init__.py`` pulls in Home Assistant, which is not
needed to exercise the parser. This loads the individual modules under a synthetic
package so their relative imports still resolve.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG_DIR = _ROOT / "custom_components" / "arbor"
_PKG_NAME = "arbor_under_test"

if _PKG_NAME not in sys.modules:
    package = types.ModuleType(_PKG_NAME)
    package.__path__ = [str(_PKG_DIR)]
    sys.modules[_PKG_NAME] = package


def load(name: str) -> types.ModuleType:
    """Import one module from the integration by file name."""
    full_name = f"{_PKG_NAME}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, _PKG_DIR / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise ImportError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


const = load("const")
errors = load("errors")
models = load("models")
http_util = load("http_util")
parser = load("parser")
