# Local WSL runtime

This supervisor runs CERBERUS as a Linux `systemd` service while keeping its
HTTP endpoint on WSL loopback. The Windows login task only boots the enabled
service; gameplay remains deterministic and Ollama remains disabled by default.

Prerequisites:

- WSL 2 with an Ubuntu distribution and `systemd`
- `/opt/cerberus-venv` containing the repository's Python dependencies
- a repository-root `.env` file, which is ignored by Git

The service stores runtime state under `/var/lib/cerberus/memory`, restarts on
failure, and starts whenever the distribution boots. Windows must keep the WSL
VM alive for unattended operation; set a long `vmIdleTimeout` under `[wsl2]` in
`%USERPROFILE%\.wslconfig` and use a login task to boot the distribution.

The checked-in service is a template. Replace `__REPO_ROOT_WSL__` with the
repository's WSL path before installing it as
`/etc/systemd/system/cerberus.service`.
