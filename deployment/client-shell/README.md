# CERBERUS + clawroyale.ai client shell

This package installs the CERBERUS orchestration layer and the
clawroyale.ai plugin as one versioned runtime. It contains no customer
credentials, wallet keys, saved sessions, memory databases, or machine-specific
paths.

## Choose the service you bought

### Self-hosted license

You own the deployment and the credentials. Supported targets are:

- Windows 11 with WSL2 Ubuntu
- Ubuntu Linux with systemd
- Your own Render account

The WSL2 and Linux paths use immutable releases and systemd services. If a new
release fails its protected health checks, deployment restores the prior code
and Python-environment pointers. Existing service definitions are backed up
before this package changes them.

### Managed hosting

AnarchI operates the same versioned artifact on the AnarchI WSL2 cloud-server
spine. Each customer receives a separate systemd instance, protected credential
file, runtime configuration, state directory, memory directory, log directory,
loopback port, current and staging pointers, and rollback record.

Managed hosting does not add credentials to this download. Credentials are
provided separately and installed into the protected tenant file by an
authorized operator. Caddy may expose an API-only route for the future Windows
desktop client, and Cloudflare Access or Tunnel may add another security layer.
That client must still send the CERBERUS bearer token; opening the hostname in a
normal browser returns `401`. The CERBERUS process remains bound to loopback.

The Anar Core Kernel invariant is absolute: an account may access only data
scoped to that account. Managed installation enforces it with a dedicated Linux
identity, strict configuration keys, isolated files and directories, a unique
port, hostname, HTTP token and PIN, deterministic tenant Mongo names when Mongo
is safely supportable, disabled private-network trust, and a default-deny gateway. Only
`/healthz` is unauthenticated. Every other route requires the account bearer
token, including stats, stream data, chat, and tick operations.

Managed hosting is currently locked to each tenant's isolated SQLite path.
Mongo/Atlas is rejected until the installer can prove the database credential
is restricted to exactly one tenant; naming a database is not sufficient
isolation. Customer-owned single-account targets may configure their own memory
backend at their own boundary.

This package contains deployment mechanics only. It does not contain a billing
system or license-enforcement backend.

## Before you begin

For Windows, enable WSL2, install Ubuntu, and enable systemd in Ubuntu. For
native Linux, use a current Ubuntu release with systemd and Python 3.12 or
newer.

Keep your credentials out of the extracted download. The configuration command
copies them from a file you control into a protected Linux location. Never send
that completed file back to AnarchI support.

## Windows 11 + WSL2 quick start

Open PowerShell in the extracted package folder.

1. Check the machine:

   ```powershell
   .\cerberus-client.ps1 doctor
   ```

2. Install the sealed release:

   ```powershell
   .\cerberus-client.ps1 install
   ```

3. Make private account and agent configuration copies outside the download:

   ```powershell
   New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\AnarchI\CERBERUS"
   Copy-Item .\config\runtime-self-hosted.template "$env:LOCALAPPDATA\AnarchI\CERBERUS\runtime.env"
   Copy-Item .\config\credentials.template "$env:LOCALAPPDATA\AnarchI\CERBERUS\credentials.env"
   Copy-Item .\config\agent-runtime.template "$env:LOCALAPPDATA\AnarchI\CERBERUS\default-agent.env"
   Copy-Item .\config\agent-credentials.template "$env:LOCALAPPDATA\AnarchI\CERBERUS\default-agent-credentials.env"
   ```

4. Open those four copies in a text editor. Runtime settings control behavior.
   Credentials are only the keys and account values you own. Leave an optional
   credential blank when its integration is disabled.

5. Apply the files without printing their values:

   ```powershell
   .\cerberus-client.ps1 configure `
     -RuntimeFile "$env:LOCALAPPDATA\AnarchI\CERBERUS\runtime.env" `
     -CredentialsFile "$env:LOCALAPPDATA\AnarchI\CERBERUS\credentials.env"
   ```

   Then apply the default agent:

   ```powershell
   .\cerberus-client.ps1 configure `
     -Agent default `
     -AgentRuntimeFile "$env:LOCALAPPDATA\AnarchI\CERBERUS\default-agent.env" `
     -AgentCredentialsFile "$env:LOCALAPPDATA\AnarchI\CERBERUS\default-agent-credentials.env"
   ```

6. Deploy and check status:

   ```powershell
   .\cerberus-client.ps1 deploy
   .\cerberus-client.ps1 status
   ```

The service listens on `127.0.0.1` by default. Do not change it to a public
address unless you have deliberately added authentication and a protected
reverse proxy.

Register more plugin workers with repeated agent options:

```powershell
.\cerberus-client.ps1 install -Agent scout,guardian,closer
.\cerberus-client.ps1 deploy -Agent scout,guardian,closer
.\cerberus-client.ps1 status -Agent scout,guardian,closer
```

Each agent receives its own runtime identity, configuration, credentials,
memory, and worker process. Agents within one account share that account's
protected core; they never share another account's data.

## Ubuntu Linux quick start

From the extracted package:

```bash
sudo bash bin/cerberus-client doctor
sudo bash bin/cerberus-client install
sudo bash bin/cerberus-client configure \
  --runtime /path/to/your/runtime.env \
  --credentials /path/to/your/credentials.env
sudo bash bin/cerberus-client deploy
sudo bash bin/cerberus-client status
```

The source configuration files are never copied into the immutable release.
The installed credential file is mode `0600` and readable only by that
account's dedicated service identity (plus the host root administrator).

## Your own Render account

Prepare a clean Render source folder from the sealed artifact:

```powershell
.\cerberus-client.ps1 prepare-render -Destination C:\path\to\new\cerberus-render-source
```

On Linux:

```bash
bash bin/cerberus-client prepare-render --destination /path/to/new/cerberus-render-source
```

The command overlays the plugin onto CERBERUS and adds a credential-free
`render.yaml`. Put that clean folder in a repository you control, create a
Blueprint in your Render account, and enter every `sync: false` value in the
Render dashboard. Do not commit a local credential file. The template keeps
every non-health route bearer-protected even though Render assigns the web
service a public endpoint.

## Managed-tenant operator flow

This section is for authorized AnarchI operators. It uses the same artifact as
the customer download.

```bash
sudo bash bin/cerberus-client install \
  --scope managed \
  --tenant customer-slug \
  --port 10101

sudo bash bin/cerberus-client configure \
  --scope managed \
  --tenant customer-slug \
  --runtime /secure/intake/customer-slug.runtime \
  --credentials /secure/intake/customer-slug.credentials

sudo bash bin/cerberus-client deploy \
  --scope managed \
  --tenant customer-slug

sudo bash bin/cerberus-client status \
  --scope managed \
  --tenant customer-slug
```

Tenant identifiers may contain lowercase letters, numbers, and hyphens only.
Ports must be unique and must remain bound to `127.0.0.1`. The optional Caddy
template under `deploy/managed` is an API-only route for the bearer-authenticated
desktop client, not a browser dashboard. It contains exactly one tenant
loopback upstream. Cloudflare tokens and tunnel credentials are configured
outside this artifact.

## Updating or rolling back

Installing a newer package creates a new release and moves the staging pointer.
`deploy` promotes it. If the health check fails, the command restores the
previous code and Python-environment pointers and restarts the prior release.

The release identity is the package content hash, not a mutable folder name.
Existing releases are never edited in place.

Systemd unit files are host configuration, not part of the code-pointer
rollback. Before replacing an existing unit, `install` preserves the prior file
in a timestamped, root-only folder under
`/var/lib/cerberus-client/unit-backups`. An operator must deliberately restore
one of those files if a host-level unit change itself needs to be reversed.

## Credential rules

- Only use credentials issued to you or owned by your organization.
- Never put credentials into the downloaded folder or a Git repository.
- Keep unused integrations disabled and their credentials blank.
- Use separate credentials for separate managed tenants.
- Generate `CERBERUS_HTTP_TOKEN` as 43–128 URL-safe random characters. This
  command creates a suitable token:

  ```text
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

- Generate a separate 8–64 character URL-safe `CERBERUS_PIN`:

  ```text
  python -c "import secrets; print(secrets.token_urlsafe(12))"
  ```

- `CLAW_ROYALE_API_KEY` is issued by the customer's own clawroyale.ai account.
  Do not reuse an AnarchI operator credential or another customer's key.
- `CERBERUS_OWNER_EOA_ADDRESS` identifies the human or company owner. It is a
  public `0x`-prefixed EVM address, never a private key.
- `CERBERUS_AGENT_EOA_ADDRESS` and
  `CERBERUS_AGENT_EOA_PRIVATE_KEY` belong to a dedicated automation wallet for
  that one plugin agent. The private key must be a `0x`-prefixed 32-byte EVM
  key and is stored only in the protected per-agent credential file. Never
  enter or transmit a seed phrase.
- Free play is the safe default. Choosing `offchain` or `onchain` in
  `CLAW_ROYALE_GAME_MODE` is an explicit economic opt-in, requires a dedicated
  agent signing key, and never enables automatic paid upgrades.
- Do not quote credential values.
- Rotate a credential in the provider first, then apply the updated protected
  file with `configure`, and restart with `deploy`.
- The status and doctor commands report presence and permissions, never values.

## What the package refuses to ship

The builder stops if the distributable contains:

- any `.env` file;
- private-key files or private-key blocks;
- a recognizable API key, token, password, or private-key value;
- SQLite databases or memory/state directories;
- Git metadata;
- a Windows user path, WSL user path, or Linux home path tied to one machine.

Only files matched by the component allowlists can enter a release. The package
also includes hashes for every shipped file.

## Command reference

```text
doctor          Check prerequisites, isolation, configuration, and service health.
install         Install the sealed release and systemd definitions.
configure       Apply runtime and credential files without printing their values.
deploy          Promote the staged immutable release; roll back on failed health.
status          Show service, release, and health state without credential values.
prepare-render  Create a clean source folder for a customer-owned Render account.
```

Windows uses `cerberus-client.ps1`. WSL2 and Linux use
`bash bin/cerberus-client`. Run either command with `help` for options.
