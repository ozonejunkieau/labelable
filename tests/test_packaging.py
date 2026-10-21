"""The core install (no extras) must render without server or bridge dependencies."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from labelable._extras import _EXTRA_MODULES, missing_extra

ROOT = Path(__file__).resolve().parent.parent

# Blocks every module the extras provide, as if only the core were installed
_BLOCK_EXTRAS = f"""
import sys

BLOCKED = {sorted(_EXTRA_MODULES["server"])!r}

class BlockExtras:
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None

sys.meta_path.insert(0, BlockExtras())
"""


def _run_without_extras(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_EXTRAS + code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )


def test_core_renders_every_format_without_extras():
    result = _run_without_extras("""
from labelable.models.template import TemplateConfig
from labelable.templates import ImageTemplateEngine, JinjaTemplateEngine

template = TemplateConfig.model_validate({
    "name": "t",
    "engine": "image",
    "dimensions": {"width_mm": 30, "height_mm": 15},
    "fields": [{"name": "id"}],
    "elements": [
        {"type": "rect", "bounds": {"x_mm": 0, "y_mm": 0, "width_mm": 30, "height_mm": 5}},
        {"type": "text", "field": "id", "color": "white",
         "bounds": {"x_mm": 0, "y_mm": 0, "width_mm": 30, "height_mm": 5}},
        {"type": "qrcode", "field": "id", "x_mm": 8, "y_mm": 10, "size_mm": 6},
        {"type": "code128", "field": "id", "x_mm": 22, "y_mm": 10, "height_mm": 4},
    ],
})
engine = ImageTemplateEngine()
for fmt in ("zpl", "epl2", "ptouch", "ptouch_raw", "png"):
    assert engine.render(template, {"id": "A1"}, output_format=fmt), fmt
assert engine.render_image(template, {"id": "A1"}).mode == "1"
JinjaTemplateEngine()
import labelable.cli.render
leaked = sorted(m for m in sys.modules if m.partition(".")[0] in BLOCKED)
assert not leaked, leaked
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("module", "extra"),
    [("labelable.__main__", "server"), ("labelable.cli.bridge", "bridge"), ("labelable.cli.ptouch", "bridge")],
)
def test_entry_points_explain_missing_extra(module, extra):
    result = _run_without_extras(f"import {module}")
    assert result.returncode == 1
    assert f"labelable[{extra}]" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_extra_reraises_unrelated_import_errors():
    error = ModuleNotFoundError("No module named 'labelable.oops'", name="labelable.oops")
    with pytest.raises(ModuleNotFoundError):
        missing_extra("server", error)


def test_extra_module_map_matches_pyproject():
    """_EXTRA_MODULES drives the hint, so it must cover what each extra installs."""
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    import_names = {
        "pyserial": "serial",
        "pyusb": "usb",
        "python-multipart": "multipart",
        "uvicorn[standard]": "uvicorn",
    }

    def modules(extra: str) -> set[str]:
        found: set[str] = set()
        for requirement in extras[extra]:
            if requirement.startswith("labelable["):
                found |= modules(requirement.removeprefix("labelable[").partition("]")[0])
                continue
            name = requirement.split(">")[0].split("=")[0].strip()
            found.add(import_names.get(name, name.replace("-", "_")))
        return found

    for extra in ("bridge", "server"):
        assert modules(extra) == _EXTRA_MODULES[extra], extra
