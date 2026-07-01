# creatio-ci-cd

Toolset for Creatio on-premise: CI/CD automation, deployment, testing, and monitoring.
All tools in one repository.

## Requirements

- Python 3.10+
- [clio](https://github.com/Advance-Technologies-Foundation/clio) installed and available in PATH
- PowerShell 5.1+ (required for OAuth deployment only)

## Repository structure

```
creatio-ci-cd/
    clio-ext/       CI/CD automation CLI built on top of clio
    testing/        API tests and test collections
    monitoring/     health checks and monitoring scripts  [planned]
    docs/           additional documentation              [planned]
```

---

## clio-ext

Python CLI that extends clio with automation commands for package deployment,
localization validation, and OAuth 2.0 Identity Service setup.

Entry point: `clio-ext/clio-ext.py`

### Commands

**`init`** - initialize working directory structure

```bash
python clio-ext/clio-ext.py init -d <dir> [-e <environment>] [-b <branch>]
```

Creates `repo/`, `backup/`, `logs/` folders, runs `clio createw` in each,
and writes `clio-ext.config.json`.

---

**`deploy`** - deploy packages to environment

```bash
python clio-ext/clio-ext.py deploy -d <dir>
```

Sequence: `clio restorew` -> `clio compress` each package -> archive `.gz` files
with ISO timestamp -> `clio pushw`.

Reads environment name from `clio-ext.config.json` in the working directory:

```json
{
  "environment": "dev1",
  "branch": "main"
}
```

---

**`find-missing-loc`** - find schemas missing localization files

```bash
python clio-ext/clio-ext.py find-missing-loc -p <packages_path> [-l <locale>]
```

Scans the packages directory and reports any schema that is missing
`resource.<locale>.xml`. Default locale: `uk-UA`.

Output example:

```
Missing 'resource.uk-UA.xml' in 2 schema(s):

[UsrPackage1]
  UsrSchema1
[UsrPackage2]
  UsrSchema2

Total: 2
```

Exit code `0` = all present, `1` = missing files found.

---

**`push-all`** - push all workspace packages sequentially

```bash
python clio-ext/clio-ext.py push-all -e <env> -s <settings> [--continue-on-error]
```

Reads the package list from `workspaceSettings.json` and runs `clio push-pkg`
for each package. Stops on first error by default; use `--continue-on-error`
to push all regardless of failures.

| Argument | Required | Description |
|---|---|---|
| `-e`, `--env` | yes | Creatio environment name |
| `-s`, `--settings` | yes | Path to `workspaceSettings.json` |
| `--continue-on-error` | no | Do not stop on package failure |
| `--logs-dir` | no | Custom log directory |

---

**`deploy-oauth`** - deploy OAuth 2.0 Identity Service

Python orchestrator for setting up the Identity Service on IIS + PostgreSQL.
IIS and certificate operations run via PowerShell subprocess.

**Requires Administrator.**

```bash
python clio-ext/clio-ext.py deploy-oauth ^
  --creatio-root "C:\inetpub\wwwroot\Creatio" ^
  --identity-service-zip "C:\distr\IdentityService.zip"
```

Key options:

| Argument | Default | Description |
|---|---|---|
| `--creatio-root` | - | Creatio site root (required) |
| `--identity-service-zip` | - | Path to IdentityService.zip (required) |
| `--identity-service-port` | `40000` | HTTP port |
| `--identity-service-https-port` | `40001` | HTTPS port |
| `--service-name` | `IDService` | IIS site and AppPool base name |
| `--db-connection-string` | auto | Read from `ConnectionStrings.config` if not set |
| `--client-id` / `--client-secret` | auto | Generated randomly if not set |
| `--cert-password` | auto | Generated randomly if not set |
| `--cert-valid-days` | `1095` | Certificate validity in days |
| `--creatio-base-url` | auto | Detected from IIS binding |
| `--enable-https` | `true` | Add HTTPS binding after deploy |
| `--config-path` | - | Load options from a JSON file |

Utility flags:

| Flag | Description |
|---|---|
| `--what-if` | Preview all steps without making changes |
| `--force` | Overwrite existing certificate and config |
| `--rollback` | Restore config backups, stop Identity Service |
| `--reset-client` | Delete client from DB and restart the service |

Deployment steps:

| Step | Action |
|---|---|
| 0 | Check prerequisites: .NET 8, IIS module, ports, psql |
| 1 | Generate self-signed PFX certificate (RSA 2048 / SHA-256) |
| 2 | Extract zip, patch `appsettings.json` and `web.config` |
| 3 | Create IIS Application Pool and Website |
| 4 | Start service, poll OIDC endpoint until ready |
| 5 | Write OAuth settings to Creatio DB, enable feature flag |
| 6 | Add HTTPS bindings, patch Creatio Web.config files |
| 7 | Final report and checklist |

After deployment `secrets.out.txt` is created with ClientId, ClientSecret,
and certificate password. Save to a password manager and delete the file.

Config file example (`oauth.config.json`):

```json
{
  "service_name": "IDService",
  "identity_service_port": 40000,
  "identity_service_https_port": 40001,
  "enable_https": true,
  "enable_logging": true
}
```

---

## testing

API test collection for Creatio REST endpoints.

```
testing/
    CreatioTesing.postman_collection.json
```

Import into Postman and set the following collection variables:

| Variable | Description |
|---|---|
| `url` | Creatio base URL, e.g. `http://localhost` |
| `login` | Creatio username |
| `password` | Creatio password |

Included requests:

- **Login** - authenticates and saves `BPMCSRF` and `.ASPXAUTH` cookies to collection variables
- **OAuth dev** - gets a token via `client_credentials` grant (Identity Service)
- **Base POST Request** - template for calling custom configuration services

---

## Logging

| Tool | Log location |
|---|---|
| `clio-ext init`, `deploy` | `clio-ext/logs/YYYY-MM-DD.log` |
| `clio-ext push-all` | `clio-ext/logs/log_YYYY-MM-DD_HH-MM-SS.log` |
| `clio-ext deploy-oauth` | `--log-path` argument (default: `deploy-oauth.log`) |

`logs/` and `backup/` directories are listed in `.gitignore`.
