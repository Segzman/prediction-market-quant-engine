# PMQE VM deployment (paper mode)

A small, self-healing kit for running the engine on an Ubuntu 24.04 x86_64 VM.
It runs paper trading only: the bot always starts with `--shadow-live` and
`DRY_RUN=True`, and every order stays inside the dry-run order layer.

Live trading is out of scope for this kit. If `DRY_RUN` is set to anything
other than true, the guard refuses to start the bot unless the operator sets
`ALLOW_LIVE=1` explicitly. Even with that flag, the service command still uses
`--shadow-live`; this deployment never places live orders.

## What it installs

- `~/pmqe`: git checkout of the public repository, updated with a fast-forward
  on every bootstrap run.
- `~/pmqe/.venv`: virtualenv built from `requirements.lock`.
- `~/pmqe-state`: all mutable state, kept outside the repository.
  - `ledgers/`: append-only JSONL ledgers.
  - `logs/`: bot and dashboard file logs.
  - `runtime_status.json`: heartbeat written by the bot.
  - `jev_calls.jsonl`: optional AI judgment call log.
  - `.env`: operator environment file, including secrets.
  - `data/`: cached market history.
- `pmqe-bot.service`: the paper bot, `Restart=always`, `RestartSec=10`,
  `KillMode=process`, logs to the journal and `~/pmqe-state/logs/bot.log`.
- `pmqe-web.service`: the dashboard on `0.0.0.0:8765` (or `PORT` from `.env`),
  read-only by default.
- `pmqe-heal.service` plus `pmqe-heal.timer`: a check every five minutes.
- `pmqe-logsync.service` plus `pmqe-logsync.timer`: a private paper log backup
  every ten minutes.
- `pmqe-publish.service` plus `pmqe-publish.timer`: every 15 minutes, `publish_status.sh`
  runs `research/live_summary.py` and pushes only `status/` to the `live-status` branch of
  the public repository, using the deploy key `~/.ssh/pmqe_public_ed25519` (Host alias
  `pmqe-public-gh`). Bootstrap prints the public key on first run; add it as a deploy key
  with write access to `Segzman/prediction-market-quant-engine`. Git errors never fail the bot;
  the last publish time is shown by `check.sh`.
- `/etc/logrotate.d/pmqe`: daily rotation for the file logs, 14 copies kept.

The state directory is guarded with mode 700. The `.env` file is created from
`deploy/vm/pmqe.env.example` only when it does not exist, so re-running the
bootstrap never overwrites operator secrets.

## Install or reinstall

The external scheduler can run this one line after a reprovision:

```bash
curl -fsSL https://raw.githubusercontent.com/Segzman/prediction-market-quant-engine/main/deploy/vm/bootstrap.sh | bash
```

The script is idempotent. It installs `git`, `python3`, `python3-venv`, `curl`,
`lsof`, `openssh-client`, and `logrotate` only when missing, clones the
repository when absent, fast-forwards it when present, refreshes the virtualenv
when `requirements.lock` changes, and installs or updates the systemd units and
log rotation. It is safe to run again at any time.

After the first run, add operator secrets to `~/pmqe-state/.env`:

```bash
edit ~/pmqe-state/.env
```

For example set `TYPESAFE_API_KEY=` to a real key only if you want the JEV
experiment. Secrets are never committed. Keep `DRY_RUN=True`.

To choose a different location or branch:

```bash
PMQE_HOME="$HOME/pmqe" PMQE_STATE_DIR="$HOME/pmqe-state" PMQE_BRANCH=main \
  bash deploy/vm/bootstrap.sh
```

## Reprovision story

A reprovision wipes the disk, including the checkout, virtualenv, and state.
Run the one-line command above from the external scheduler. The bootstrap:

1. Installs the base packages.
2. Clones the repository into `~/pmqe`.
3. Rebuilds the virtualenv from the lock file.
4. Recreates `~/pmqe-state` and writes a fresh `.env` from the template.
5. Generates a logs deploy key and prints the public key.
6. Clones `~/pmqe-logs` and restores the latest paper backup when the state is
   empty.
7. Installs and enables the systemd units and log rotation.
8. Starts the bot and dashboard.

Paper mode needs no wallet key, so the bot can come up immediately after a wipe.
Re-add any operator secret after the first boot. If the VM keeps the state
directory across a rebuild, the existing `.env` and ledgers are reused.

A wiped disk also creates a new logs deploy key. Add it to the private logs
repository, then run the bootstrap again so the backup can be cloned and
restored. Until then the bot keeps running and the logs remain local.

## Dashboard over Tailscale

Find the VM Tailscale address:

```bash
tailscale ip -4
```

Open `http://<tailscale-ip>:8765` from another device on the tailnet. The
service listens on all interfaces so the Tailscale address works. If the host
firewall is enabled, allow the port on the tailnet interface only:

```bash
sudo ufw allow in on tailscale0 to any port 8765 proto tcp
```

The dashboard is read-only by default. It shows status, feeds, tracks, and
reports, and start/stop controls are hidden. To enable controls for a trusted
operator, set `PMQE_WEB_ALLOW_CONTROLS=1` in `~/pmqe-state/.env` and run:

```bash
sudo systemctl restart pmqe-web.service
```

The HTTP server also rejects `POST /api/start` and `POST /api/stop` while
read-only.

A small machine-readable probe is available:

```bash
curl -fsS http://127.0.0.1:8765/healthz
```

## Health check

Run the remote health summary with:

```bash
bash ~/pmqe/deploy/vm/check.sh
```

It prints:

- systemd state for the bot, dashboard, heal timer, and logsync timer;
- the last runtime status timestamp and age;
- BTC feed health, quorum, and Polymarket book state;
- paper P&L and per-track bankroll;
- recent warnings and errors from the journal and log file;
- the last successful log push, its commit, and any backup error;
- the `/healthz` response.

Exit code 0 means OK, 1 means degraded, and 2 means failed.

## Logs

```bash
sudo journalctl -u pmqe-bot.service -f
sudo journalctl -u pmqe-web.service -f
sudo journalctl -u pmqe-heal.service -n 50 --no-pager
sudo journalctl -u pmqe-logsync.service -n 50 --no-pager
tail -f ~/pmqe-state/logs/bot.log
```

The bot writes to both the journal and the file log. The dashboard writes to
`~/pmqe-state/logs/web.log` and the journal.

## Private log backup

Paper logs are pushed to the private repository
`git@github.com:Segzman/pmqe-paper-logs.git` through the host alias
`pmqe-logs-gh`. The bootstrap generates `~/.ssh/pmqe_logs_ed25519` and prints
its public key:

```bash
cat ~/.ssh/pmqe_logs_ed25519.pub
```

Add that key to the logs repository as a deploy key with write access:

```text
https://github.com/Segzman/pmqe-paper-logs/settings/keys
```

The backup stores only `ledgers/*.jsonl`, `jev_calls.jsonl`, a
`runtime_status.json` snapshot, and rotated `logs/bot.log.*` files under
`paper/<hostname>/<YYYY-MM-DD>/`. It never copies `.env`, SSH keys, or other
secrets. The sync removes secret-looking files and redacts known API key and
wallet variable names before committing.

Each run pulls, copies, commits, and pushes. A push failure is logged and the
next timer tick retries. The last success and error are written to
`~/pmqe-state/logsync_last_success` and `~/pmqe-state/logsync_last_error`.

Run a sync or restore by hand:

```bash
sudo systemctl start pmqe-logsync.service
bash ~/pmqe/deploy/vm/logsync.sh --restore
```

Restore only fills ledger and JEV files that are missing or empty. It does not
overwrite current state or restore a stale runtime status.

If the proxy needs a tunnel for SSH, the bootstrap writes a `ProxyCommand` into
`~/.ssh/pmqe_logs_config` using `corkscrew`, `nc`, or `ncat` when available.
Install one of those tools and re-run the bootstrap when `HTTPS_PROXY` is set.

## Stop and start

Stop everything, including the heal timer, so nothing restarts the services:

```bash
sudo systemctl disable --now pmqe-bot.service pmqe-web.service pmqe-heal.timer pmqe-logsync.timer
```

Start it again:

```bash
sudo systemctl enable --now pmqe-bot.service pmqe-web.service pmqe-heal.timer pmqe-logsync.timer
```

Restart only the bot:

```bash
sudo systemctl restart pmqe-bot.service
```

## How self-healing works

`pmqe-heal.timer` fires `pmqe-heal.service` every five minutes. The heal script:

1. Checks for the virtualenv, bootstrap script, and all systemd units.
2. If anything is missing, it re-runs `bootstrap.sh` from the checkout. If the
   checkout is also gone, it downloads the bootstrap script from `main` and
   runs that.
3. If the bot unit is disabled, it leaves it stopped.
4. If the bot unit is enabled but inactive, it starts it.
5. If `runtime_status.json` is missing or older than 120 seconds, it restarts
   the bot.

`pmqe-bot.service` itself uses `Restart=always` with `RestartSec=10`, so a
crashed process is restarted independently of the timer.

## Proxy support

Outbound traffic can use an HTTP or HTTPS proxy. Uncomment and edit the proxy
lines in `~/pmqe-state/.env`:

```bash
HTTPS_PROXY=http://proxy.example:3128
HTTP_PROXY=http://proxy.example:3128
NO_PROXY=localhost,127.0.0.1,::1
```

Then restart the services:

```bash
sudo systemctl restart pmqe-bot.service pmqe-web.service
```

The bootstrap, git, pip, and the bot all honor these variables. To use a proxy
during the very first install, export the same variables in the shell that
runs the one-line command.

## Files

- `bootstrap.sh`: install and update everything.
- `guard.sh`: refuse to start outside paper mode.
- `run-bot.sh`: start the bot with file and journal logging.
- `web.sh`: start the dashboard read-only unless controls are enabled.
- `heal.sh`: the five-minute repair check.
- `check.sh`: operator and remote-agent health summary.
- `logsync.sh`: private paper log backup and restore.
- `publish_status.sh`: public paper status publish to the `live-status` branch.
- `pmqe.env.example`: template copied to `~/pmqe-state/.env`.
- `logrotate/pmqe`: log rotation template.
- `systemd/`: unit and timer templates.
