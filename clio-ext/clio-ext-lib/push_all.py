import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run(
    environment: str,
    settings_path: str,
    continue_on_error: bool = False,
    logs_dir: str | None = None,
) -> int:
    """Push all packages listed in workspaceSettings.json to an environment via clio."""
    settings_file = Path(settings_path)
    if not settings_file.is_file():
        print(f"Settings file not found: {settings_file}")
        return 1

    with open(settings_file, "r", encoding="utf-8") as f:
        settings = json.load(f)

    packages = settings.get("Packages", [])
    if not packages:
        print("No packages found in settings file.")
        return 1

    log_dir = Path(logs_dir) if logs_dir else settings_file.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"

    def log(message: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}"
        print(message)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    total = len(packages)
    success = 0
    failed: list[str] = []

    mode = "continue on error" if continue_on_error else "stop on error"
    log(f"Pushing {total} packages to environment: {environment} [{mode}]")
    log("-" * 60)

    for i, pkg in enumerate(packages, 1):
        print(f"[{i}/{total}] {pkg} ... ", end="", flush=True)
        result = subprocess.run(
            ["clio", "push-pkg", pkg, "-e", environment],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("OK")
            log(f"[{i}/{total}] {pkg} ... OK")
            success += 1
        else:
            print("FAILED")
            error = (result.stderr or result.stdout or "").strip()
            log(f"[{i}/{total}] {pkg} ... FAILED")
            if error:
                print(f"  {error}")
                log(f"  {error}")
            failed.append(pkg)

            if not continue_on_error:
                log("-" * 60)
                log(f"Error on package '{pkg}'. Stopping.")
                log(f"Done. Success: {success} / {total}")
                return 1

    log("-" * 60)
    log(f"Done. Success: {success} / {total}")

    if failed:
        log(f"Failed packages ({len(failed)}):")
        for pkg in failed:
            log(f"  - {pkg}")
        return 1

    return 0
