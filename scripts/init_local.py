"""Prepare an isolated demo workspace without copying production data."""
import json
from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/demo-lab"
    destination = root / "data/demo-lab"
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(fixture, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print("Created the synthetic demo workspace in data/demo-lab.")
    else:
        print("Kept the existing local workspace in data/demo-lab.")
    config = root / "config/server.json"
    if not config.exists():
        settings = json.loads((root / "config/server.example.json").read_text())
        settings["lab_root"] = str(destination.resolve())
        settings["static_root"] = str((root / "frontend/dist").resolve())
        config.write_text(json.dumps(settings, indent=2) + "\n")
        print("Created the ignored local config/server.json; device queries are disabled.")
    else:
        print("Kept the existing local config/server.json.")
    print("Create your own administrator with: PYTHONPATH=backend python3 -m pump_console.auth add-user --username admin --role admin")


if __name__ == "__main__":
    main()
