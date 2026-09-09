# The three steps that need root

Everything else in the deployment runs as `talkwisp` and needs no privilege.
These three do. Run them from your own sudo session; paste the output of the
`ufw status` line back, because that is the one whose answer nobody knows yet.

Order matters only in one place: **Caddy cannot obtain a certificate until 80
and 443 are open**, so the firewall goes first.

---

## 1. Firewall

Report what it says before changing anything — an RDP rule was found open on
this project once, and the interesting question is what else is there.

```bash
sudo ufw status verbose
```

Then, if it is not already this:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp     comment 'ssh'
sudo ufw allow 80/tcp     comment 'http, ACME challenge'
sudo ufw allow 443/tcp    comment 'https'
sudo ufw enable
sudo ufw status numbered
```

**Do not open 5432 or 8200.** Postgres already binds `127.0.0.1` and uvicorn is
configured to; opening either would make the origin reachable around Caddy and
Cloudflare both.

`ufw` is not the only layer. GCP has its own VPC firewall in front of the VM, and
a rule there is invisible from inside the box:

```bash
gcloud compute firewall-rules list --project <the talkwisp@gmail.com project> \
  --format="table(name,sourceRanges.list(),allowed[].map().firewall_rule().list())"
```

Anything allowing 3389, 5432, 8200 or `0.0.0.0/0` on a wide port range should go.

---

## 2. The secrets file

Root-owned, `0600`. Not `.bashrc`, not the unit files — those are world-readable
under `/etc/systemd/system`.

```bash
sudo install -d -m 0755 /etc/talkwisp
sudo touch /etc/talkwisp/env
sudo chmod 0600 /etc/talkwisp/env
sudo chown root:root /etc/talkwisp/env
sudo nano /etc/talkwisp/env
```

Contents — one `KEY=value` per line, no `export`, no quotes:

```
DATABASE_URL=postgresql://talkwisp_app:<app password>@localhost:5432/talkwisp
ADMIN_DATABASE_URL=postgresql://postgres:<superuser password>@localhost:5432/talkwisp
PUBLIC_BASE_URL=https://talkwisp.uz
GEMINI_API_KEYS=<key>[,<key2>]
GEMINI_MODEL=gemini-3.1-flash-lite
LLM_PROVIDER=gemini
TELEGRAM_BOT_TOKEN=<@avisenamed_bot token>
TELEGRAM_PLATFORM_BOT_TOKEN=<@talkwisp_bot token>
TELEGRAM_LOGIN_ENABLED=true
TELEGRAM_OWNER_ID=<your numeric telegram id>
RESEND_API_KEY=<from resend>
RESEND_FROM=info@talkwisp.uz
```

Note the port is **5432** here, not the laptop's 5433 — the box runs Postgres
natively rather than in a container.

`TELEGRAM_BOT_TOKEN` is the AGENT bot and `TELEGRAM_PLATFORM_BOT_TOKEN` is the
one that signs web logins. They are different bots on purpose and conflating
them is the mistake this split exists to prevent.

---

## 3. Caddy and the two units

```bash
# Caddy, from its own apt repository
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# config
sudo cp /home/talkwisp/talkwisp/deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile     # before reloading
sudo systemctl reload caddy

# Caddy runs as its own user and must be able to read the built files
sudo chmod o+x /home/talkwisp /home/talkwisp/talkwisp

# the two services
sudo cp /home/talkwisp/talkwisp/deploy/talkwisp-api.service /etc/systemd/system/
sudo cp /home/talkwisp/talkwisp/deploy/talkwisp-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now talkwisp-api
sudo systemctl enable --now talkwisp-bot
systemctl --no-pager status talkwisp-api talkwisp-bot
```

**The API will refuse to start until the database is set up**, and that refusal
is deliberate: `assert_app_role()` will not run on a superuser connection,
because a superuser bypasses every row-level policy silently. If you see

```
DATABASE_URL connects as 'postgres', which is a superuser. Refusing to start.
```

that is the guard working, not a bug. `DATABASE_URL` must be `talkwisp_app`.

---

## Also needed, and only you can do it

```bash
sudo -u postgres psql -c "\du"
sudo -u postgres psql -c "alter role postgres with password '<pick one>';"
sudo -u postgres psql -d talkwisp -c "select version from schema_migrations order by version;"
```

Both credentials currently in the repo's `.env` fail authentication, and
Postgres returns the same error whether a role exists or not — so until this
runs, nobody can say whether `talkwisp_app` exists or whether the tenancy
migration was ever applied here.
