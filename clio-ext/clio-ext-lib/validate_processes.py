import json
import os

# Platform UId for the ReadDataUserTask schema — constant across Creatio versions
READ_DATA_TASK_UID = "cb455b6f-78ff-4b1e-b241-c2bbc0b37e9f"


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _is_unoptimized_read_element(element: dict) -> bool:
    """True if element is a ReadData task that reads all columns (no column filter set)."""
    if element.get("BL7") != READ_DATA_TASK_UID:
        return False
    for param in element.get("BP2", []):
        if param.get("A2") == "EntityColumnMetaPathes":
            gs2 = param.get("L8", {}).get("GS2", "")
            return not bool(gs2)
    # EntityColumnMetaPathes absent — defaults to all columns
    return True


def _analyze_schema(schema_dir: str) -> list:
    """
    Returns list of unoptimized element names in this schema, or None if it's
    not a process schema.
    """
    descriptor_path = os.path.join(schema_dir, "descriptor.json")
    metadata_path = os.path.join(schema_dir, "metadata.json")

    if not os.path.exists(descriptor_path):
        return None

    try:
        descriptor = _read_json(descriptor_path)
    except (json.JSONDecodeError, OSError):
        return None

    if descriptor.get("Descriptor", {}).get("ManagerName") != "ProcessSchemaManager":
        return None

    if not os.path.exists(metadata_path):
        return []

    try:
        metadata = _read_json(metadata_path)
    except (json.JSONDecodeError, OSError):
        return []

    schema_name = descriptor["Descriptor"].get("Name", os.path.basename(schema_dir))
    caption = descriptor["Descriptor"].get("Caption", "")
    elements = metadata.get("MetaData", {}).get("Schema", {}).get("BK4", [])

    issues = []
    for elem in elements:
        if _is_unoptimized_read_element(elem):
            issues.append({
                "schema": schema_name,
                "caption": caption,
                "element": elem.get("A2", "<unnamed>"),
            })
    return issues


def run(packages_path: str, package: str = None) -> int:
    if not os.path.isdir(packages_path):
        print(f"Error: packages path not found: {packages_path}")
        return 1

    if package:
        pkg_names = [package]
    else:
        pkg_names = sorted(os.listdir(packages_path))

    all_issues: dict[str, list] = {}

    for pkg_name in pkg_names:
        pkg_dir = os.path.join(packages_path, pkg_name)
        schemas_dir = os.path.join(pkg_dir, "Schemas")
        if not os.path.isdir(schemas_dir):
            continue

        pkg_issues = []
        for schema_name in sorted(os.listdir(schemas_dir)):
            schema_dir = os.path.join(schemas_dir, schema_name)
            if not os.path.isdir(schema_dir):
                continue
            result = _analyze_schema(schema_dir)
            if result:
                pkg_issues.extend(result)

        if pkg_issues:
            all_issues[pkg_name] = pkg_issues

    total = sum(len(v) for v in all_issues.values())

    if not total:
        print("No unoptimized Read Data elements found.")
        return 0

    print(f"Found {total} unoptimized Read Data element(s) (reading all columns):\n")
    for pkg_name, issues in all_issues.items():
        print(f"[{pkg_name}]")
        for issue in issues:
            caption = f" ({issue['caption']})" if issue["caption"] else ""
            print(f"  {issue['schema']}{caption}")
            print(f"    element: {issue['element']}")
    print(f"\nTotal: {total}")
    return 1
