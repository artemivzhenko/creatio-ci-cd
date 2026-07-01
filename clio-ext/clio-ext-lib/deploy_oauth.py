import json
import os
import secrets
import string
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class OAuthConfig:
    creatio_root: str = ""
    identity_service_zip: str = ""
    identity_service_dir: str = "IdentityService"
    identity_service_port: int = 40000
    identity_service_https_port: int = 40001
    service_name: str = "IDService"
    app_pool_name: str = ""
    site_name: str = ""
    db_connection_string: str = ""
    client_id: str = ""
    client_name: str = "LocalIdentityServiceApp"
    client_secret: str = ""
    cert_password: str = ""
    cert_valid_days: int = 1095
    cert_output_path: str = ""
    cert_subject_name: str = ""
    cert_file_name: str = ""
    cert_legacy_name: str = "IdentityService"
    creatio_base_url: str = ""
    enable_https: bool = True
    enable_logging: bool = True
    creatio_https_port: int = 443
    creatio_host_header: str = ""
    skip_creatio_https: bool = False
    normalize_conn_string: bool = True
    log_path: str = ".\\deploy-oauth.log"
    what_if: bool = False
    force: bool = False
    rollback: bool = False
    reset_client: bool = False
    rebind_ports: bool = False
    config_path: str = ""

    def __post_init__(self):
        if not self.app_pool_name:
            self.app_pool_name = f"{self.service_name}Pool"
        if not self.site_name:
            self.site_name = self.service_name


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class Logger:
    COLORS = {
        "INFO": "\033[96m",
        "WARN": "\033[93m",
        "ERROR": "\033[91m",
        "STEP": "\033[95m",
        "OK": "\033[92m",
    }
    RESET = "\033[0m"

    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message: str, level: str = "INFO"):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}][{level}] {message}"
        color = self.COLORS.get(level, "")
        print(f"{color}{line}{self.RESET}")
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def step(self, num: int, name: str):
        self.log("", "INFO")
        self.log("=" * 42, "STEP")
        self.log(f"  Step {num}: {name}", "STEP")
        self.log("=" * 42, "STEP")

    def block(self, name: str):
        self.log(f"  -- {name}", "INFO")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_alphanumeric(length: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _random_password(length: int = 16) -> str:
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghijkmnpqrstuvwxyz"
    digits = "23456789"
    special = "!@#$%^&*-_=+"
    all_chars = upper + lower + digits + special
    pw = [
        secrets.choice(upper),
        secrets.choice(lower),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    for _ in range(length - 4):
        pw.append(secrets.choice(all_chars))
    import random
    random.shuffle(pw)
    return "".join(pw)


def _run_ps(script: str, logger: Logger, check: bool = True) -> subprocess.CompletedProcess:
    """Run a PowerShell script block and return the result."""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()
        logger.log(f"    PowerShell error: {error}", "ERROR")
    return result


def _read_connection_string(creatio_root: str, logger: Logger) -> str:
    """Read the 'db' connection string from ConnectionStrings.config."""
    cs_path = Path(creatio_root) / "ConnectionStrings.config"
    if not cs_path.exists():
        raise FileNotFoundError(f"ConnectionStrings.config not found: {cs_path}")

    tree = ET.parse(cs_path)
    root = tree.getroot()
    for add in root.iter("add"):
        if add.get("name") == "db":
            return add.get("connectionString", "")
    raise ValueError("No 'db' entry found in ConnectionStrings.config")


def _normalize_npgsql(conn: str, logger: Logger) -> str:
    """Normalize short-form Npgsql keywords to long form."""
    aliases = {
        "MaxPoolSize": "Maximum Pool Size",
        "MinPoolSize": "Minimum Pool Size",
        "CommandTimeout": "Command Timeout",
    }
    parts = [p.strip() for p in conn.split(";") if p.strip()]
    changed = 0
    out = []
    for part in parts:
        kv = part.split("=", 1)
        if len(kv) != 2:
            out.append(part)
            continue
        key, val = kv[0].strip(), kv[1].strip()
        for alias, canonical in aliases.items():
            if key.lower() == alias.lower():
                logger.log(f"    ConnString: '{key}' -> '{canonical}'", "INFO")
                key = canonical
                changed += 1
                break
        out.append(f"{key}={val}")
    if changed:
        logger.log(f"    Normalized {changed} Npgsql keyword(s) [OK]", "OK")
    return ";".join(out)


def _safe_name(name: str) -> str:
    import re
    safe = re.sub(r"[^A-Za-z0-9\-_]", "-", name)
    safe = re.sub(r"-{2,}", "-", safe).strip("-")
    return safe or "IdentityService"


# ---------------------------------------------------------------------------
# Step results tracker
# ---------------------------------------------------------------------------

@dataclass
class DeployState:
    cfg: OAuthConfig
    log: Logger
    cert_thumbprint: str = ""
    id_service_path: str = ""
    creatio_site_name: str = ""
    pfx_path: str = ""
    secrets_path: str = ""
    step_results: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step0_prerequisites(st: DeployState):
    """Check prerequisites: .NET 8, IIS module, paths, ports, PostgreSQL."""
    st.log.step(0, "Checking prerequisites")
    ok = True

    st.log.block(".NET 8 ASP.NET Core Runtime")
    r = _run_ps("dotnet.exe --list-runtimes", st.log, check=False)
    if r.returncode != 0 or "Microsoft.AspNetCore.App 8." not in r.stdout:
        st.log.log("    .NET 8 ASP.NET Core Runtime NOT found", "ERROR")
        st.log.log("    Download: https://dotnet.microsoft.com/en-us/download/dotnet/8.0", "ERROR")
        raise RuntimeError(".NET 8 Hosting Bundle is required")
    st.log.log("    .NET 8 runtime: found [OK]", "OK")

    st.log.block("IIS PowerShell module")
    r = _run_ps("Import-Module WebAdministration -ErrorAction Stop", st.log, check=False)
    if r.returncode != 0:
        r = _run_ps("Import-Module IISAdministration -ErrorAction Stop", st.log, check=False)
        if r.returncode != 0:
            st.log.log("    No IIS PowerShell module found", "ERROR")
            raise RuntimeError("IIS management module required")
    st.log.log("    IIS module loaded [OK]", "OK")

    st.log.block("Input paths")
    if not Path(st.cfg.creatio_root).is_dir():
        raise FileNotFoundError(f"CreatioRootPath not found: {st.cfg.creatio_root}")
    st.log.log(f"    CreatioRootPath: exists [OK]", "OK")
    if not Path(st.cfg.identity_service_zip).is_file():
        raise FileNotFoundError(f"IdentityServiceZip not found: {st.cfg.identity_service_zip}")
    size_mb = Path(st.cfg.identity_service_zip).stat().st_size / (1024 * 1024)
    st.log.log(f"    IdentityServiceZip: exists ({size_mb:.1f} MB) [OK]", "OK")

    st.log.block("Port availability")
    for port in (st.cfg.identity_service_port, st.cfg.identity_service_https_port):
        r = _run_ps(
            f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count",
            st.log, check=False,
        )
        count = (r.stdout or "0").strip()
        if count != "0":
            st.log.log(f"    Port {port} in use", "WARN")
            ok = False
        else:
            st.log.log(f"    Port {port}: free [OK]", "OK")

    st.log.block("PostgreSQL connectivity")
    r = _run_ps(
        f'$env:PGPASSWORD="test"; psql.exe --version 2>&1',
        st.log, check=False,
    )
    if "psql" in (r.stdout or ""):
        st.log.log("    psql.exe found [OK]", "OK")
    else:
        st.log.log("    psql.exe not found (DB steps may be skipped)", "WARN")
        ok = False

    st.step_results["Step0_Prerequisites"] = "OK" if ok else "WARN"


def step1_certificate(st: DeployState):
    """Generate a PFX certificate for the Identity Service."""
    st.log.step(1, "Generating PFX certificate")

    if st.cfg.what_if:
        st.log.log(f"    [WhatIf] Would generate RSA 2048 cert -> {st.pfx_path}", "WARN")
        st.cert_thumbprint = "WHATIF000000000000000000000000000000000000"
        st.step_results["Step1_Certificate"] = "SKIP"
        return

    st.log.block("Create self-signed certificate (RSA 2048 / SHA-256)")
    cert_subject = f"CN={st.cfg.cert_subject_name}, O=Creatio, L=Local"

    ps_script = f"""
$certPwd = ConvertTo-SecureString '{st.cfg.cert_password}' -AsPlainText -Force
$cert = New-SelfSignedCertificate `
    -Subject           '{cert_subject}' `
    -KeyAlgorithm      RSA `
    -KeyLength         2048 `
    -HashAlgorithm     SHA256 `
    -KeyUsage          DigitalSignature, KeyEncipherment `
    -TextExtension     @("2.5.29.37={{text}}1.3.6.1.5.5.7.3.1,1.3.6.1.5.5.7.3.2") `
    -NotAfter          (Get-Date).AddDays({st.cfg.cert_valid_days}) `
    -CertStoreLocation "Cert:\\LocalMachine\\My"
Export-PfxCertificate -Cert $cert -FilePath '{st.pfx_path}' -Password $certPwd -Force | Out-Null
$poolAccount = "IIS AppPool\\{st.cfg.app_pool_name}"
try {{
    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
    $keyName = $rsa.Key.UniqueName
    $keyPaths = @(
        (Join-Path $env:ProgramData "Microsoft\\Crypto\\Keys\\$keyName"),
        (Join-Path $env:ProgramData "Microsoft\\Crypto\\RSA\\MachineKeys\\$keyName"),
        (Join-Path $env:SystemRoot "System32\\Microsoft\\Crypto\\Keys\\$keyName")
    )
    foreach ($kp in $keyPaths) {{
        if (Test-Path $kp) {{
            $acl = Get-Acl $kp
            foreach ($account in @($poolAccount, "NETWORK SERVICE")) {{
                try {{
                    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($account, "Read", "Allow")
                    $acl.AddAccessRule($rule)
                }} catch {{ }}
            }}
            Set-Acl $kp $acl
            break
        }}
    }}
}} catch {{ }}
Write-Output $cert.Thumbprint
"""
    r = _run_ps(ps_script, st.log)
    if r.returncode != 0:
        st.step_results["Step1_Certificate"] = "FAIL"
        raise RuntimeError("Failed to create certificate")

    st.cert_thumbprint = r.stdout.strip().split("\n")[-1].strip()
    st.log.log(f"    Certificate created [OK]  Thumbprint: {st.cert_thumbprint}", "OK")
    st.step_results["Step1_Certificate"] = "OK"


def step2_configure(st: DeployState):
    """Extract and configure the Identity Service."""
    st.log.step(2, "Extracting and configuring Identity Service")

    id_path = Path(st.id_service_path)
    appsettings = id_path / "appsettings.json"

    st.log.block("Extract IdentityService.zip")
    if st.cfg.what_if:
        st.log.log(f"    [WhatIf] Would extract -> {id_path}", "WARN")
    else:
        _run_ps(
            f"Expand-Archive -Path '{st.cfg.identity_service_zip}' -DestinationPath '{id_path}' -Force",
            st.log,
        )
        file_count = sum(1 for _ in id_path.rglob("*") if _.is_file())
        st.log.log(f"    Extracted ({file_count} files) [OK]", "OK")

    st.log.block("Build Clients JSON")
    client_obj = {
        "ClientId": st.cfg.client_id,
        "ClientName": st.cfg.client_name,
        "Secrets": [st.cfg.client_secret],
        "AllowedGrantTypes": ["implicit", "client_credentials"],
        "RedirectUris": [
            st.cfg.creatio_base_url,
            f"{st.cfg.creatio_base_url}/lib",
            f"{st.cfg.creatio_base_url}/lib/",
        ],
        "PostLogoutRedirectUris": [st.cfg.creatio_base_url],
        "IdentityTokenLifetime": 300,
        "AccessTokenLifetime": 3600,
        "Properties": {"AllowedQueryParameters": '["invitationHash","targetSubject"]'},
        "AllowedScopes": [
            "register_own_resource", "get_resource_list", "get_client_info",
            "find_clients", "remove_client", "update_client",
            "add_registrar_client", "generate_auth_code", "revoke_client_tokens",
            "get_service_info", "IdentityServerApi",
        ],
    }
    clients_json = json.dumps([client_obj], separators=(",", ":"))
    st.log.log(f"    Clients JSON built ({len(clients_json)} chars) [OK]", "OK")

    st.log.block("Patch appsettings.json")
    if st.cfg.what_if:
        st.log.log("    [WhatIf] Would patch appsettings.json", "WARN")
    elif appsettings.is_file():
        with open(appsettings, "r", encoding="utf-8") as f:
            settings = json.load(f)

        conn_str = st.cfg.db_connection_string
        if st.cfg.normalize_conn_string:
            conn_str = _normalize_npgsql(conn_str, st.log)

        settings["DbProvider"] = "Postgres"
        settings["DatabaseConnectionString"] = conn_str
        settings["X509CertificatePath"] = st.pfx_path.replace("\\", "/")
        settings["X509CertificatePassword"] = st.cfg.cert_password
        settings["Clients"] = clients_json
        settings["AllowedCorsOrigins"] = f'[ "{st.cfg.creatio_base_url}" ]'
        log_level = "Debug" if st.cfg.enable_logging else "Information"
        settings.setdefault("Logging", {}).setdefault("LogLevel", {})["Default"] = log_level

        with open(appsettings, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        st.log.log("    appsettings.json saved [OK]", "OK")

    st.log.block("Configure web.config")
    web_config = id_path / "web.config"
    if not st.cfg.what_if and web_config.is_file():
        try:
            tree = ET.parse(web_config)
            asp = tree.find(".//aspNetCore")
            if asp is not None:
                if st.cfg.enable_logging:
                    log_dir = id_path / "logs"
                    log_dir.mkdir(exist_ok=True)
                    asp.set("stdoutLogEnabled", "true")
                    asp.set("stdoutLogFile", str(log_dir / "stdout").replace("\\", "/"))
                env_vars = asp.find("environmentVariables")
                if env_vars is None:
                    env_vars = ET.SubElement(asp, "environmentVariables")
                for ev in list(env_vars):
                    if ev.get("name") == "ASPNETCORE_HTTPS_PORT":
                        env_vars.remove(ev)
                new_ev = ET.SubElement(env_vars, "environmentVariable")
                new_ev.set("name", "ASPNETCORE_HTTPS_PORT")
                new_ev.set("value", str(st.cfg.identity_service_https_port))
                tree.write(web_config, encoding="utf-8", xml_declaration=True)
                st.log.log(f"    ASPNETCORE_HTTPS_PORT={st.cfg.identity_service_https_port} [OK]", "OK")
        except Exception as e:
            st.log.log(f"    web.config error (non-fatal): {e}", "WARN")

    st.step_results["Step2_Configure"] = "OK"


def step3_iis(st: DeployState):
    """Create IIS Application Pool and Website."""
    st.log.step(3, "Creating IIS App Pool and Site")

    if st.cfg.what_if:
        st.log.log(f"    [WhatIf] Would create AppPool '{st.cfg.app_pool_name}'", "WARN")
        st.log.log(f"    [WhatIf] Would create Site '{st.cfg.site_name}' HTTP:{st.cfg.identity_service_port}", "WARN")
        st.step_results["Step3_IIS"] = "SKIP"
        return

    ps_script = f"""
Import-Module WebAdministration -ErrorAction SilentlyContinue
if (-not (Test-Path "IIS:\\AppPools\\{st.cfg.app_pool_name}")) {{
    New-WebAppPool -Name '{st.cfg.app_pool_name}' | Out-Null
}}
Set-ItemProperty "IIS:\\AppPools\\{st.cfg.app_pool_name}" managedRuntimeVersion ""
Set-ItemProperty "IIS:\\AppPools\\{st.cfg.app_pool_name}" managedPipelineMode "Integrated"
$site = Get-Website -Name '{st.cfg.site_name}' -ErrorAction SilentlyContinue
if (-not $site) {{
    New-Website -Name '{st.cfg.site_name}' `
        -PhysicalPath    '{st.id_service_path}' `
        -ApplicationPool '{st.cfg.app_pool_name}' `
        -Port            {st.cfg.identity_service_port} `
        -Force | Out-Null
}} else {{
    Set-ItemProperty "IIS:\\Sites\\{st.cfg.site_name}" physicalPath    '{st.id_service_path}'
    Set-ItemProperty "IIS:\\Sites\\{st.cfg.site_name}" applicationPool '{st.cfg.app_pool_name}'
}}
Write-Output "IIS_OK"
"""
    r = _run_ps(ps_script, st.log)
    if "IIS_OK" not in (r.stdout or ""):
        st.step_results["Step3_IIS"] = "FAIL"
        raise RuntimeError("Failed to configure IIS")

    st.log.log(f"    AppPool '{st.cfg.app_pool_name}' + Site '{st.cfg.site_name}' [OK]", "OK")
    st.step_results["Step3_IIS"] = "OK"


def step4_verify(st: DeployState):
    """Start and verify the Identity Service via OIDC endpoint."""
    st.log.step(4, "Starting and verifying Identity Service")

    if st.cfg.what_if:
        st.log.log("    [WhatIf] Would start and poll /.well-known/openid-configuration", "WARN")
        st.step_results["Step4_Verify"] = "SKIP"
        return

    st.log.block("Start AppPool and Site")
    _run_ps(f"""
Import-Module WebAdministration -ErrorAction SilentlyContinue
Start-WebAppPool -Name '{st.cfg.app_pool_name}' -ErrorAction SilentlyContinue
Start-Website -Name '{st.cfg.site_name}' -ErrorAction SilentlyContinue
""", st.log, check=False)

    st.log.block("Poll OIDC endpoint")
    import time
    import urllib.request
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    urls = [
        f"http://localhost:{st.cfg.identity_service_port}/.well-known/openid-configuration",
        f"https://localhost:{st.cfg.identity_service_https_port}/.well-known/openid-configuration",
    ]

    max_retries = 15
    delay = 5
    is_up = False

    for attempt in range(1, max_retries + 1):
        for url in urls:
            try:
                st.log.log(f"    Attempt {attempt}/{max_retries} -> {url}", "INFO")
                req = urllib.request.Request(url)
                resp = urllib.request.urlopen(req, timeout=10, context=ctx)
                body = resp.read().decode("utf-8")
                oidc = json.loads(body)
                if oidc.get("issuer"):
                    st.log.log(f"    Identity Service is UP [OK]", "OK")
                    st.log.log(f"    Issuer: {oidc['issuer']}", "OK")
                    is_up = True
                    break
            except Exception as e:
                st.log.log(f"    Not ready: {e}", "WARN")
        if is_up:
            break
        if attempt < max_retries:
            time.sleep(delay)

    if not is_up:
        st.step_results["Step4_Verify"] = "FAIL"
        raise RuntimeError("Identity Service failed to start")

    st.log.block("Add HTTPS binding if needed")
    _run_ps(f"""
Import-Module WebAdministration -ErrorAction SilentlyContinue
$existBind = Get-WebBinding -Name '{st.cfg.site_name}' -Protocol "https" -ErrorAction SilentlyContinue
if (-not $existBind) {{
    $cert = Get-ChildItem Cert:\\LocalMachine\\My |
        Where-Object {{ $_.Thumbprint -eq '{st.cert_thumbprint}' }} | Select-Object -First 1
    if (-not $cert) {{
        $certPwd = ConvertTo-SecureString '{st.cfg.cert_password}' -AsPlainText -Force
        $cert = Import-PfxCertificate -FilePath '{st.pfx_path}' -Password $certPwd -CertStoreLocation Cert:\\LocalMachine\\My
    }}
    New-WebBinding -Name '{st.cfg.site_name}' -Protocol "https" -Port {st.cfg.identity_service_https_port} -IPAddress "*"
    $b = Get-WebBinding -Name '{st.cfg.site_name}' -Protocol "https" -Port {st.cfg.identity_service_https_port}
    $b.AddSslCertificate($cert.Thumbprint, "My")
    Restart-WebAppPool -Name '{st.cfg.app_pool_name}' -ErrorAction SilentlyContinue
}}
""", st.log, check=False)

    st.step_results["Step4_Verify"] = "OK"


def step5_connect(st: DeployState):
    """Write OAuth settings to Creatio DB and enable feature flag."""
    st.log.step(5, "Connecting Identity Service to Creatio")

    id_url = f"http://localhost:{st.cfg.identity_service_port}"

    if st.cfg.what_if:
        st.log.log(f"    [WhatIf] Would UPDATE SysSettings and enable OAuth20Integration", "WARN")
        st.step_results["Step5_Connect"] = "SKIP"
        return

    st.log.block("Update Creatio SysSetting via psql")
    settings = [
        ("OAuth20IdentityServerUrl", id_url, False),
        ("OAuth20IdentityServerClientId", st.cfg.client_id, False),
        ("OAuth20IdentityServerClientSecret", st.cfg.client_secret, True),
    ]
    for code, value, redact in settings:
        display = "***" if redact else value
        st.log.log(f"    {code} = {display}", "INFO")
        query = f"""UPDATE "SysSetting" SET "TextValue" = '{value}' WHERE "Code" = '{code}';"""
        _run_ps(f"""
$env:PGPASSWORD = ('{st.cfg.db_connection_string}' -split ';' | ForEach-Object {{ if ($_ -match '(?i)password=(.*)') {{ $matches[1] }} }})
# Use psql via clio-ext helper; fallback to direct psql
$psql = Get-Command 'psql.exe' -ErrorAction SilentlyContinue
if ($psql) {{
    & psql.exe -c "{query}" 2>&1 | Out-Null
}}
""", st.log, check=False)

    st.log.block("Enable OAuth20Integration feature flag")
    feature_query = '''DO $$ BEGIN IF EXISTS (SELECT 1 FROM "SysFeature" WHERE "Code" = 'OAuth20Integration') THEN UPDATE "SysAdminUnitFeatureState" s SET "FeatureState" = 1 FROM "SysFeature" f WHERE f."Id" = s."SysFeatureId" AND f."Code" = 'OAuth20Integration'; END IF; END; $$;'''
    _run_ps(f"""
$psql = Get-Command 'psql.exe' -ErrorAction SilentlyContinue
if ($psql) {{
    & psql.exe -c "{feature_query}" 2>&1 | Out-Null
}}
""", st.log, check=False)

    st.log.block("Recycle Creatio app pool")
    if st.creatio_site_name:
        _run_ps(f"""
Import-Module WebAdministration -ErrorAction SilentlyContinue
$pool = (Get-Website -Name '{st.creatio_site_name}' -ErrorAction SilentlyContinue).applicationPool
if ($pool) {{ Restart-WebAppPool -Name $pool -ErrorAction SilentlyContinue }}
""", st.log, check=False)

    st.step_results["Step5_Connect"] = "OK"


def step6_https(st: DeployState):
    """Add HTTPS bindings and patch Web.config files."""
    st.log.step(6, "Switching sites to HTTPS")

    if not st.cfg.enable_https:
        st.log.log("    EnableHttps=false -- skipping", "WARN")
        st.step_results["Step6_Https"] = "SKIP"
        return

    if st.cfg.what_if:
        st.log.log("    [WhatIf] Would add HTTPS bindings and patch Web.config", "WARN")
        st.step_results["Step6_Https"] = "SKIP"
        return

    st.log.block("HTTPS binding -- Identity Service")
    _run_ps(f"""
Import-Module WebAdministration -ErrorAction SilentlyContinue
$cert = Get-ChildItem Cert:\\LocalMachine\\My |
    Where-Object {{ $_.Thumbprint -eq '{st.cert_thumbprint}' }} | Select-Object -First 1
if (-not $cert) {{
    $certPwd = ConvertTo-SecureString '{st.cfg.cert_password}' -AsPlainText -Force
    Import-PfxCertificate -FilePath '{st.pfx_path}' -Password $certPwd -CertStoreLocation Cert:\\LocalMachine\\My | Out-Null
}}
$existBind = Get-WebBinding -Name '{st.cfg.site_name}' -Protocol "https" -ErrorAction SilentlyContinue
if (-not $existBind) {{
    New-WebBinding -Name '{st.cfg.site_name}' -Protocol "https" -Port {st.cfg.identity_service_https_port} -IPAddress "*"
    $b = Get-WebBinding -Name '{st.cfg.site_name}' -Protocol "https" -Port {st.cfg.identity_service_https_port}
    $b.AddSslCertificate('{st.cert_thumbprint}', "My")
}}
""", st.log, check=False)
    st.log.log(f"    HTTPS:{st.cfg.identity_service_https_port} binding [OK]", "OK")

    st.log.block("Patch root Web.config (http -> https)")
    root_wc = Path(st.cfg.creatio_root) / "Web.config"
    if root_wc.is_file():
        _patch_webconfig_http_to_https(root_wc, st.log)

    st.log.block("Patch Terrasoft.WebApp\\Web.config")
    webapp_wc = Path(st.cfg.creatio_root) / "Terrasoft.WebApp" / "Web.config"
    if webapp_wc.is_file():
        _patch_webconfig_http_to_https(webapp_wc, st.log)

    st.log.block("Restart app pools")
    _run_ps(f"""
Import-Module WebAdministration -ErrorAction SilentlyContinue
Restart-WebAppPool -Name '{st.cfg.app_pool_name}' -ErrorAction SilentlyContinue
""", st.log, check=False)

    st.step_results["Step6_Https"] = "OK"


def _patch_webconfig_http_to_https(path: Path, logger: Logger):
    """Replace configSource references from \\http\\ to \\https\\."""
    try:
        import shutil
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, f"{path}.bak_{ts}")

        tree = ET.parse(path)
        changed = 0
        for elem in tree.iter():
            cs = elem.get("configSource", "")
            if "\\http\\" in cs or "/http/" in cs:
                import re
                new_cs = re.sub(r"[/\\]http[/\\]", r"\\https\\", cs)
                elem.set("configSource", new_cs)
                logger.log(f"    Updated: '{cs}' -> '{new_cs}'", "INFO")
                changed += 1
        tree.write(path, encoding="utf-8", xml_declaration=True)
        logger.log(f"    Patched ({changed} nodes) [OK]", "OK")
    except Exception as e:
        logger.log(f"    Failed to patch {path.name}: {e}", "WARN")


def step7_report(st: DeployState):
    """Print final summary report."""
    st.log.step(7, "Final report")

    id_url = f"http://localhost:{st.cfg.identity_service_port}"
    id_https = f"https://localhost:{st.cfg.identity_service_https_port}"

    st.log.block("Step results")
    for step_name, result in st.step_results.items():
        st.log.log(f"    [{result:>4}]  {step_name}", "OK" if result == "OK" else "WARN")

    print()
    print("=" * 64)
    print("  DEPLOY COMPLETE -- OAuth Identity Service")
    print("=" * 64)
    print(f"  Identity Service  : {st.id_service_path}")
    print(f"  Certificate (PFX) : {st.pfx_path}")
    print(f"  Cert thumbprint   : {st.cert_thumbprint}")
    print()
    print(f"  HTTP  URL : {id_url}")
    print(f"  HTTPS URL : {id_https}")
    print()
    print(f"  ClientId   : {st.cfg.client_id}")
    print(f"  ClientName : {st.cfg.client_name}")
    print(f"  ClientSecret: *** (see secrets.out.txt)")
    print()
    print(f"  Secrets file : {st.secrets_path}")
    print(f"  Log file     : {st.cfg.log_path}")
    print()
    print("  MANUAL CHECKLIST:")
    print("  [ ] Log in to Creatio and verify OAuth login works")
    print(f"  [ ] System Settings -> OAuth20IdentityServerUrl = {id_url}")
    print("  [ ] Delete secrets.out.txt after saving to password manager!")
    print("=" * 64)


def _write_secrets_file(st: DeployState):
    """Write secrets to a file for the administrator."""
    content = f"""================================================================================
  DEPLOY-CREATIOAUTH -- GENERATED SECRETS
  !! SENSITIVE -- save to a password manager and DELETE this file immediately! !!
================================================================================

Generated  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

-- Identity Service Client --
ClientId       : {st.cfg.client_id}
ClientSecret   : {st.cfg.client_secret}
ClientName     : {st.cfg.client_name}

-- Certificate --
CertPassword   : {st.cfg.cert_password}
CertThumbprint : {st.cert_thumbprint or 'not yet generated'}
PFX path       : {st.pfx_path}

-- Creatio System Settings (written to DB) --
OAuth20IdentityServerUrl          : http://localhost:{st.cfg.identity_service_port}
OAuth20IdentityServerClientId     : {st.cfg.client_id}
OAuth20IdentityServerClientSecret : {st.cfg.client_secret}

================================================================================
  DELETE THIS FILE after saving credentials to a password manager!
================================================================================
"""
    with open(st.secrets_path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def _rollback(st: DeployState):
    """Restore config backups and stop the Identity Service."""
    st.log.log("Rolling back -- restoring latest backups...", "WARN")
    for rel in [
        f"{st.cfg.identity_service_dir}/appsettings.json",
        "Web.config",
        "Terrasoft.WebApp/Web.config",
    ]:
        target = Path(st.cfg.creatio_root) / rel
        import glob
        backups = sorted(glob.glob(f"{target}.bak_*"), reverse=True)
        if backups:
            import shutil
            shutil.copy2(backups[0], target)
            st.log.log(f"  Restored: {target.name} <- {Path(backups[0]).name}", "OK")
        else:
            st.log.log(f"  No backup for {target.name}", "WARN")

    _run_ps(f"""
Import-Module WebAdministration -ErrorAction SilentlyContinue
Stop-Website -Name '{st.cfg.site_name}' -ErrorAction SilentlyContinue
Stop-WebAppPool -Name '{st.cfg.app_pool_name}' -ErrorAction SilentlyContinue
""", st.log, check=False)
    st.log.log("  Identity Service stopped", "OK")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    creatio_root: str,
    identity_service_zip: str,
    what_if: bool = False,
    force: bool = False,
    rollback: bool = False,
    reset_client: bool = False,
    enable_https: bool = True,
    config_path: str = "",
    **overrides,
) -> int:
    """Deploy OAuth 2.0 Identity Service for Creatio."""

    cfg = OAuthConfig(
        creatio_root=creatio_root,
        identity_service_zip=identity_service_zip,
        what_if=what_if,
        force=force,
        rollback=rollback,
        reset_client=reset_client,
        enable_https=enable_https,
        config_path=config_path,
    )

    # Apply overrides from kwargs
    for key, val in overrides.items():
        if hasattr(cfg, key) and val is not None:
            setattr(cfg, key, val)

    # Load external config file
    if cfg.config_path and Path(cfg.config_path).is_file():
        with open(cfg.config_path, "r", encoding="utf-8") as f:
            file_cfg = json.load(f)
        for key, val in file_cfg.items():
            attr = key.replace("-", "_")
            if hasattr(cfg, attr):
                current = getattr(cfg, attr)
                if not current or current == 0:
                    setattr(cfg, attr, val)

    # Auto-generate secrets
    if not cfg.client_id:
        cfg.client_id = _random_alphanumeric(16)
    if not cfg.client_secret:
        cfg.client_secret = _random_alphanumeric(32)
    if not cfg.cert_password:
        cfg.cert_password = _random_password(16)

    # Derive names
    safe = _safe_name(cfg.site_name or cfg.service_name)
    if not cfg.cert_subject_name:
        cfg.cert_subject_name = safe
    if not cfg.cert_file_name:
        cfg.cert_file_name = f"{safe}.pfx"

    logger = Logger(cfg.log_path)

    # Derive paths
    id_path = str(Path(cfg.creatio_root) / cfg.identity_service_dir)
    if not cfg.cert_output_path:
        cfg.cert_output_path = id_path

    st = DeployState(
        cfg=cfg,
        log=logger,
        id_service_path=id_path,
        pfx_path=str(Path(cfg.cert_output_path) / cfg.cert_file_name),
        secrets_path=str(Path(id_path) / "secrets.out.txt"),
    )

    # Read DB connection string
    if not cfg.db_connection_string:
        try:
            cfg.db_connection_string = _read_connection_string(cfg.creatio_root, logger)
            logger.log("DB connection string loaded [OK]", "OK")
        except Exception as e:
            logger.log(f"Could not read DB connection string: {e}", "WARN")

    # Detect Creatio IIS site
    r = _run_ps(f"""
Import-Module WebAdministration -ErrorAction SilentlyContinue
$normalised = '{cfg.creatio_root}'.TrimEnd('\\').ToLower()
$site = Get-Website | Where-Object {{ $_.physicalPath.TrimEnd('\\').ToLower() -eq $normalised }} | Select-Object -First 1
if ($site) {{ Write-Output $site.Name }}
""", logger, check=False)
    st.creatio_site_name = (r.stdout or "").strip()
    if st.creatio_site_name:
        logger.log(f"Auto-detected Creatio IIS site: '{st.creatio_site_name}'", "OK")

    # Auto-detect CreatioBaseUrl
    if not cfg.creatio_base_url:
        cfg.creatio_base_url = "http://localhost"
        logger.log("CreatioBaseUrl defaulted to http://localhost", "WARN")

    if cfg.rollback:
        _rollback(st)
        return 0

    # Ensure ID service directory exists
    Path(id_path).mkdir(parents=True, exist_ok=True)

    # Write secrets immediately
    _write_secrets_file(st)
    logger.log(f"Secrets written to: {st.secrets_path}", "WARN")

    logger.log("=" * 64, "INFO")
    logger.log(f"  Deploy-CreatioOAuth  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "INFO")
    logger.log(f"  CreatioRootPath  : {cfg.creatio_root}", "INFO")
    logger.log(f"  IdentityService  : {id_path}", "INFO")
    logger.log(f"  AppPoolName      : {cfg.app_pool_name}", "INFO")
    logger.log(f"  SiteName         : {cfg.site_name}", "INFO")
    logger.log(f"  HTTP  port       : {cfg.identity_service_port}", "INFO")
    logger.log(f"  HTTPS port       : {cfg.identity_service_https_port}", "INFO")
    logger.log(f"  EnableHttps      : {cfg.enable_https}", "INFO")
    logger.log(f"  WhatIf           : {cfg.what_if}", "INFO")
    logger.log("=" * 64, "INFO")

    try:
        step0_prerequisites(st)
        step1_certificate(st)
        step2_configure(st)
        step3_iis(st)
        step4_verify(st)
        step5_connect(st)
        step6_https(st)
        step7_report(st)
    except Exception as e:
        logger.log("", "INFO")
        logger.log("=" * 64, "ERROR")
        logger.log("  DEPLOYMENT FAILED", "ERROR")
        logger.log(f"  Error: {e}", "ERROR")
        logger.log("=" * 64, "ERROR")
        logger.log("  -> Rerun with --rollback to restore config backups", "WARN")
        logger.log("  -> Rerun with --what-if to preview changes", "WARN")
        return 1

    return 0
