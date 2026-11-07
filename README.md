# Home server dashboard

A private Flask dashboard for an Ubuntu server or Ubuntu running in WSL. It displays system metrics and service state, stores a constrained policy, and can optionally apply that policy through a separately installed root helper.

The application does not configure Windows networking, install WSL, set up a reverse proxy, or change the server automatically. Review the deployment steps and confirm the target machine before running system-changing commands.

## What is included

- Login with a Werkzeug password hash, signed HTTP-only session cookie, CSRF protection, a five-attempt/five-minute login throttle, and a client-address allowlist.
- Live CPU, memory, disk, uptime, and `cron` / `unattended-upgrades` status.
- JSON policy validation and atomic, owner-only policy-file writes.
- An optional fixed-command apply hook. The browser cannot supply shell commands or helper arguments.
- A systemd unit and sudoers rule for an unprivileged service account.
- Tests for authentication, request validation, policy persistence, and status reporting.

Applying a policy can stop or start services, change systemd resource limits, and replace `/etc/cron.d/homedash`. The initial policy is empty and makes no system changes. Review every proposed policy and the helper before enabling Apply.

## Requirements

- Ubuntu 22.04 or newer, or another Ubuntu release with Python 3.10+, systemd, and cron.
- For WSL: a WSL 2 Ubuntu distribution with systemd enabled. Windows does not need to run the Flask application; run the Linux commands inside the Ubuntu distribution.
- A trusted local network and a TLS reverse proxy for browser access from other devices. The app itself listens only on `127.0.0.1:8080` in the production unit.

Check WSL from **PowerShell**:

```powershell
wsl --list --verbose
```

In the Ubuntu shell, check systemd:

```sh
systemctl is-system-running
```

If systemd is not enabled in WSL, consult the current Microsoft WSL documentation before changing `/etc/wsl.conf`. Preserve any existing settings; applying a WSL configuration change requires shutting down and restarting the distribution.

## Development

Run these commands from the repository root in an Ubuntu shell (or another shell with Python 3.10+):

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p instance tasks
python create_config.py ./instance/config.json
```

The setup prompts for a username, password, and allowed client CIDR. For local development, enter `127.0.0.1/32`; use `::1/128` if testing over IPv6 loopback. The config generator refuses to overwrite an existing config file. Keep the generated config private.

Start the Flask development server (not for production):

```sh
HOMEDASH_COOKIE_SECURE=0 \
HOMEDASH_CONFIG=./instance/config.json \
HOMEDASH_POLICY=./instance/policy.json \
HOMEDASH_TASKS=./tasks \
python -m flask --app 'app:create_app()' run
```

Open `http://127.0.0.1:5000`. The development server binds to loopback by default. To stop it, press **Ctrl+C** in that terminal.

Run the test suite from the repository root:

```sh
python -m unittest discover -s tests -v
```

## Production installation

These are manual Ubuntu commands. Replace `/path/to/checkout` with the actual repository path inside the Ubuntu distribution. Do not copy private notes or the `.git` directory into `/opt/homedash`.

1. Install prerequisites and create the service account:

   ```sh
   sudo apt update
   sudo apt install python3 python3-venv cron
   sudo groupadd --system homedash
   sudo useradd --system --gid homedash --home-dir /var/lib/homedash \
     --create-home --shell /usr/sbin/nologin homedash
   ```

   If the account or group already exists, inspect it rather than repeating or replacing it.

2. From the repository root, install only the application files. The app and task scripts are root-owned; the service account must not be able to modify them.

   ```sh
   sudo install -d -o root -g root -m 755 /opt/homedash
   sudo cp app.py create_config.py requirements.txt /opt/homedash/
   sudo cp -a templates static scripts deploy /opt/homedash/
   sudo chown -R root:root /opt/homedash
   sudo install -d -o root -g root -m 755 /opt/homedash/tasks
   sudo python3 -m venv /opt/homedash/venv
   sudo /opt/homedash/venv/bin/pip install -r /opt/homedash/requirements.txt
   ```

   Add only reviewed `.sh` scripts to `/opt/homedash/tasks`. They run as root when scheduled. Each must be a regular file, owned by root, and not writable by group or others.

3. Generate the login config and create private state storage:

   ```sh
   sudo install -d -o root -g homedash -m 750 /etc/homedash
   sudo install -d -o root -g root -m 755 /etc/systemd/system.control
   sudo /opt/homedash/venv/bin/python /opt/homedash/create_config.py /etc/homedash/config.json
   sudo chown root:homedash /etc/homedash/config.json
   sudo chmod 640 /etc/homedash/config.json
   sudo install -d -o homedash -g homedash -m 700 /var/lib/homedash
   sudo install -o homedash -g homedash -m 600 \
     /opt/homedash/deploy/policy.json /var/lib/homedash/policy.json
   ```

   For this loopback-only service, configure `127.0.0.1/32` as the allowed network when prompted. The reverse proxy is the client the app sees. Therefore, the proxy must independently restrict browser access to the intended LAN or VPN; do not expose an unrestricted proxy route.

4. Review and install the privileged helper and sudoers rule:

   ```sh
   sudo install -o root -g root -m 750 /opt/homedash/scripts/homedash-apply \
     /usr/local/sbin/homedash-apply
   sudo install -o root -g root -m 440 /opt/homedash/deploy/homedash.sudoers \
     /etc/sudoers.d/homedash
   sudo visudo -cf /etc/sudoers.d/homedash
   ```

   Do not grant the service account broader sudo permissions. The helper is an intentional trust boundary: inspect its validation and every task script before enabling it.

5. Install and start the systemd service:

   ```sh
   sudo install -o root -g root -m 644 /opt/homedash/deploy/homedash.service \
     /etc/systemd/system/homedash.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now homedash
   sudo systemctl status homedash
   ```

   The service binds to loopback port `8080`. Put a TLS reverse proxy in front of it and restrict that proxy to trusted clients. This repository does not include proxy or certificate configuration. Do not expose port 8080 directly, enable router port forwarding, or publish the dashboard to the public internet.

6. Verify locally in Ubuntu:

   ```sh
   curl --fail http://127.0.0.1:8080/login
   sudo journalctl -u homedash -n 50 --no-pager
   ```

   The `curl` response should be the sign-in page. Sign in through the trusted HTTPS proxy to verify browser access, then confirm the status values. Applying a policy is optional and should be tested only after reviewing its effects.

To stop the production service, run `sudo systemctl disable --now homedash`. This stops the dashboard; it does not uninstall files or undo policies already applied.

## Network and proxy boundary

The production service intentionally binds to `127.0.0.1`, and the allowlist checks the TCP peer address (`request.remote_addr`). When a same-machine reverse proxy connects, that address is loopback—not the browser’s LAN address. Configure the app allowlist for loopback and enforce the real browser source network at the proxy/firewall. The application does not trust forwarded client-IP headers.

In WSL, a loopback-bound Linux service is available within that distribution and may be reachable from Windows depending on WSL networking mode. Access from another LAN device requires additional, deliberate Windows/WSL networking and firewall configuration. This project does not configure that path; do not solve it by binding the Flask app to every interface.

## Policy format

The initial `deploy/policy.json` is intentionally empty:

```json
{
  "services": {},
  "limits": {},
  "tasks": []
}
```

Only `cron` and `unattended-upgrades` are supported. Service states are `running` or `stopped`. Limits require both `MemoryMax` (for example `100M`) and `CPUQuota` (for example `20%`). A task requires a lowercase name, a five-field cron schedule, and the basename of a reviewed `.sh` script in `/opt/homedash/tasks`. The privileged helper revalidates the policy and script ownership before applying it.

## Operational limits

- Keep exactly one Gunicorn worker: the login throttle is in-memory and shared only within one process. The supplied systemd unit uses one worker and four threads.
- The unit permits the root helper to write only `/etc/cron.d` and `/etc/systemd/system.control`; these are needed for cron installation and persistent systemd resource limits. The application itself still runs as the unprivileged `homedash` user.
- Failed helper runs return an error, but systemd service operations may have succeeded before a later operation fails. Check the service state and system journal before retrying.
- Policy and config files are replaced atomically. Keep backups before manually changing production state.
- The dashboard is intended for a trusted private network, not as a general-purpose server administration panel.
