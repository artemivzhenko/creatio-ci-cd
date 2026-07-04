import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _get_log_file(logs_dir: Path) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return logs_dir / f"{today}.log"


def _write_log(logs_dir: Path, line: str):
    print(line)
    log_file = _get_log_file(logs_dir)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_pending(logs_dir: Path, operation: str):
    _write_log(logs_dir, f"[!] {operation}")


def log_ok(logs_dir: Path, operation: str):
    _write_log(logs_dir, f"[+] {operation}")


def log_err(logs_dir: Path, operation: str, error: str):
    _write_log(logs_dir, f"[-] {operation} {error}")


# ---------------------------------------------------------------------------
# Command runner
# ---------------------------------------------------------------------------

def run(logs_dir: Path, operation: str, cmd: list[str], cwd: Path = None):
    """Run a shell command, log result, exit on failure."""
    log_pending(logs_dir, operation)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip()
        log_err(logs_dir, operation, error)
        sys.exit(1)
    log_ok(logs_dir, operation)
    return result


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def read_config(dir_path: Path) -> dict:
    config_path = dir_path / "clio-ext.config.json"
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(args):
    dir_path = Path(args.dir).resolve()
    if not dir_path.exists():
        print(f"Directory does not exist: {dir_path}")
        sys.exit(1)

    environment = args.env or ""
    branch = args.branch or ""

    logs_dir = dir_path / "logs"
    if not logs_dir.exists():
        logs_dir.mkdir(parents=True)
        log_ok(logs_dir, "Create directory logs")

    _write_log(logs_dir, f"\n=== init  {datetime.now().isoformat()} ===")

    for name in ("repo", "backup"):
        folder = dir_path / name
        if not folder.exists():
            log_pending(logs_dir, f"Create directory {name}")
            folder.mkdir(parents=True)
            log_ok(logs_dir, f"Create directory {name}")
            run(logs_dir, f"clio createw in {name}", ["clio", "createw"], cwd=folder)

    config_path = dir_path / "clio-ext.config.json"
    if not config_path.exists():
        log_pending(logs_dir, "Create clio-ext.config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"environment": environment, "branch": branch}, f, indent=2)
        log_ok(logs_dir, "Create clio-ext.config.json")
    elif environment or branch:
        log_pending(logs_dir, "Update clio-ext.config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        if environment:
            config["environment"] = environment
        if branch:
            config["branch"] = branch
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        log_ok(logs_dir, "Update clio-ext.config.json")

    log_ok(logs_dir, "Init complete")


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------

def cmd_deploy(args):
    dir_path = Path(args.dir).resolve()
    if not dir_path.exists():
        print(f"Directory does not exist: {dir_path}")
        sys.exit(1)

    logs_dir = dir_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    _write_log(logs_dir, f"\n=== deploy  {datetime.now().isoformat()} ===")

    config = read_config(dir_path)
    environment = config.get("environment", "").strip()
    if not environment:
        print("Error: 'environment' is empty in clio-ext.config.json")
        sys.exit(1)

    backup_dir = dir_path / "backup"
    repo_dir = dir_path / "repo"
    packages_dir = backup_dir / "packages"

    run(logs_dir, f"clio restorew -e {environment}", ["clio", "restorew", "-e", environment], cwd=backup_dir)

    if not packages_dir.exists():
        log_err(logs_dir, "Find packages directory", "packages/ not found after restorew")
        sys.exit(1)

    packages = [p for p in packages_dir.iterdir() if p.is_dir()]
    if not packages:
        log_err(logs_dir, "Find packages", "no package folders found in packages/")
        sys.exit(1)

    for package in packages:
        run(logs_dir, f"clio compress {package.name}", ["clio", "compress", package.name], cwd=packages_dir)

    iso_name = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    archive_dir = backup_dir / iso_name
    log_pending(logs_dir, f"Create archive directory {iso_name}")
    archive_dir.mkdir(parents=True)
    log_ok(logs_dir, f"Create archive directory {iso_name}")

    gz_files = list(packages_dir.glob("*.gz"))
    for gz_file in gz_files:
        log_pending(logs_dir, f"Move {gz_file.name} -> {iso_name}/")
        shutil.move(str(gz_file), archive_dir / gz_file.name)
        log_ok(logs_dir, f"Move {gz_file.name} -> {iso_name}/")

    run(logs_dir, f"clio pushw -e {environment}", ["clio", "pushw", "-e", environment], cwd=repo_dir)

    log_ok(logs_dir, "Deploy complete")


# ---------------------------------------------------------------------------
# find-missing-loc
# ---------------------------------------------------------------------------

def cmd_find_missing_loc(args):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from importlib import import_module
    mod = import_module("clio-ext-lib.find_missing_loc")
    code = mod.run(
        packages_path=args.packages_path,
        locale=args.locale,
    )
    sys.exit(code)


# ---------------------------------------------------------------------------
# push-all
# ---------------------------------------------------------------------------

def cmd_push_all(args):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from importlib import import_module
    mod = import_module("clio-ext-lib.push_all")
    code = mod.run(
        environment=args.env,
        settings_path=args.settings,
        continue_on_error=args.continue_on_error,
        logs_dir=args.logs_dir,
    )
    sys.exit(code)


# ---------------------------------------------------------------------------
# deploy-oauth
# ---------------------------------------------------------------------------

def cmd_deploy_oauth(args):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from importlib import import_module
    mod = import_module("clio-ext-lib.deploy_oauth")

    overrides = {}
    for key in [
        "identity_service_dir", "identity_service_port", "identity_service_https_port",
        "service_name", "app_pool_name", "site_name", "db_connection_string",
        "client_id", "client_name", "client_secret", "cert_password",
        "cert_valid_days", "cert_output_path", "cert_subject_name",
        "cert_file_name", "cert_legacy_name", "creatio_base_url",
        "enable_logging", "creatio_https_port", "creatio_host_header",
        "skip_creatio_https", "normalize_conn_string", "log_path",
        "rebind_ports",
    ]:
        val = getattr(args, key, None)
        if val is not None:
            overrides[key] = val

    code = mod.run(
        creatio_root=args.creatio_root,
        identity_service_zip=args.identity_service_zip,
        what_if=args.what_if,
        force=args.force,
        rollback=args.rollback,
        reset_client=args.reset_client,
        enable_https=args.enable_https,
        config_path=args.config_path or "",
        **overrides,
    )
    sys.exit(code)


# ---------------------------------------------------------------------------
# validate-processes
# ---------------------------------------------------------------------------

def cmd_validate_processes(args):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from importlib import import_module
    mod = import_module("clio-ext-lib.validate_processes")
    code = mod.run(
        packages_path=args.packages_path,
        package=args.package,
    )
    sys.exit(code)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="clio-ext: CI/CD automation for Creatio via clio",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- init ---
    p_init = subparsers.add_parser("init", help="Initialize workspace structure")
    p_init.add_argument("-d", "--dir", required=True, help="Working directory path")
    p_init.add_argument("-e", "--env", default="", help="Environment name")
    p_init.add_argument("-b", "--branch", default="", help="Branch name")
    p_init.set_defaults(func=cmd_init)

    # --- deploy ---
    p_deploy = subparsers.add_parser("deploy", help="Deploy packages (restorew -> compress -> pushw)")
    p_deploy.add_argument("-d", "--dir", required=True, help="Working directory path")
    p_deploy.set_defaults(func=cmd_deploy)

    # --- find-missing-loc ---
    p_loc = subparsers.add_parser("find-missing-loc", help="Find schemas missing localization files")
    p_loc.add_argument("-p", "--packages-path", required=True, help="Path to packages directory")
    p_loc.add_argument("-l", "--locale", default="uk-UA", help="Locale code (default: uk-UA)")
    p_loc.set_defaults(func=cmd_find_missing_loc)

    # --- push-all ---
    p_push = subparsers.add_parser("push-all", help="Push all packages to an environment via clio")
    p_push.add_argument("-e", "--env", required=True, help="Target environment name")
    p_push.add_argument("-s", "--settings", required=True, help="Path to workspaceSettings.json")
    p_push.add_argument("--continue-on-error", action="store_true", help="Continue on package push failure")
    p_push.add_argument("--logs-dir", default=None, help="Custom logs directory")
    p_push.set_defaults(func=cmd_push_all)

    # --- deploy-oauth ---
    p_oauth = subparsers.add_parser("deploy-oauth", help="Deploy OAuth 2.0 Identity Service for Creatio")
    p_oauth.add_argument("--creatio-root", required=True, help="Creatio site root directory")
    p_oauth.add_argument("--identity-service-zip", required=True, help="Path to IdentityService.zip")
    p_oauth.add_argument("--identity-service-dir", default="IdentityService")
    p_oauth.add_argument("--identity-service-port", type=int, default=None)
    p_oauth.add_argument("--identity-service-https-port", type=int, default=None)
    p_oauth.add_argument("--service-name", default=None)
    p_oauth.add_argument("--app-pool-name", default=None)
    p_oauth.add_argument("--site-name", default=None)
    p_oauth.add_argument("--db-connection-string", default=None)
    p_oauth.add_argument("--client-id", default=None)
    p_oauth.add_argument("--client-name", default=None)
    p_oauth.add_argument("--client-secret", default=None)
    p_oauth.add_argument("--cert-password", default=None)
    p_oauth.add_argument("--cert-valid-days", type=int, default=None)
    p_oauth.add_argument("--cert-output-path", default=None)
    p_oauth.add_argument("--cert-subject-name", default=None)
    p_oauth.add_argument("--cert-file-name", default=None)
    p_oauth.add_argument("--cert-legacy-name", default=None)
    p_oauth.add_argument("--creatio-base-url", default=None)
    p_oauth.add_argument("--enable-https", type=bool, default=True)
    p_oauth.add_argument("--enable-logging", type=bool, default=None)
    p_oauth.add_argument("--creatio-https-port", type=int, default=None)
    p_oauth.add_argument("--creatio-host-header", default=None)
    p_oauth.add_argument("--skip-creatio-https", action="store_true", default=None)
    p_oauth.add_argument("--normalize-conn-string", type=bool, default=None)
    p_oauth.add_argument("--log-path", default=None)
    p_oauth.add_argument("--config-path", default=None, help="Path to JSON config file")
    p_oauth.add_argument("--what-if", action="store_true", help="Preview changes without applying")
    p_oauth.add_argument("--force", action="store_true", help="Overwrite existing resources")
    p_oauth.add_argument("--rollback", action="store_true", help="Restore config backups")
    p_oauth.add_argument("--reset-client", action="store_true", help="Reset client in DB")
    p_oauth.add_argument("--rebind-ports", action="store_true", default=None)
    p_oauth.set_defaults(func=cmd_deploy_oauth)

    # --- validate-processes ---
    p_val = subparsers.add_parser(
        "validate-processes",
        help="Find process schemas with unoptimized Read Data elements (reading all columns)",
    )
    p_val.add_argument("-p", "--packages-path", required=True, help="Path to packages directory")
    p_val.add_argument("-k", "--package", default=None, help="Scan only this package (by name)")
    p_val.set_defaults(func=cmd_validate_processes)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
