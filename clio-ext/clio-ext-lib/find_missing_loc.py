import sys
from pathlib import Path


def run(packages_path: str, locale: str = "uk-UA") -> int:
    """Find schemas missing a localization resource file for the given locale."""
    packages_dir = Path(packages_path)
    if not packages_dir.is_dir():
        print(f"Packages path not found: {packages_dir}")
        return 1

    file_name = f"resource.{locale}.xml"
    missing: list[dict] = []

    for pkg in sorted(packages_dir.iterdir()):
        if not pkg.is_dir():
            continue
        resources_dir = pkg / "Resources"
        if not resources_dir.is_dir():
            continue
        for schema in sorted(resources_dir.iterdir()):
            if not schema.is_dir():
                continue
            target = schema / file_name
            if not target.exists():
                missing.append({
                    "package": pkg.name,
                    "schema": schema.name,
                    "path": str(schema),
                })

    if not missing:
        print(f"All schemas have '{file_name}'.")
        return 0

    print(f"Missing '{file_name}' in {len(missing)} schema(s):\n")

    grouped: dict[str, list[str]] = {}
    for m in missing:
        grouped.setdefault(m["package"], []).append(m["schema"])

    for package, schemas in grouped.items():
        print(f"[{package}]")
        for s in schemas:
            print(f"  {s}")

    print(f"\nTotal: {len(missing)}")
    return 1
