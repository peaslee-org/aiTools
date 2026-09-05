"""The remove_background migration chains from the compiled_transcripts head and imports cleanly."""
import importlib.util
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "app" / "db" / "migrations" / "versions"


def _load(name: str):
    path = next(VERSIONS.glob(f"{name}_*.py"))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_remove_background_migration_chains_from_compiled_transcripts():
    mod = _load("w3x4y5z6a7b8")
    assert mod.revision == "w3x4y5z6a7b8"
    assert mod.down_revision == "v2w3x4y5z6a7"
    assert callable(mod.upgrade) and callable(mod.downgrade)
