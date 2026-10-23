import sys
import types
from pathlib import Path

_COMPONENTS = Path(__file__).resolve().parents[1] / "custom_components"


def _package(name: str, path: Path) -> None:
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module


_package("custom_components", _COMPONENTS)
_package("custom_components.eufy_clean", _COMPONENTS / "eufy_clean")
_package("custom_components.eufy_clean.api", _COMPONENTS / "eufy_clean" / "api")
