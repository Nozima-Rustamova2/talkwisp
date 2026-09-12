# The three steps that need root

Everything else in the deployment runs as `talkwisp` and needs no privilege.
These three do. Run them from your own sudo session; paste the output of the
`ufw status` line back, because that is the one whose answer nobody knows yet.

Order matters in one place: **the origin certificate must exist before Caddy
starts**, so section 2b comes before section 3. Caddy does not use Let's
Encrypt here, so nothing waits on the firewall for a challenge.

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
sudo ufw allow 80/tcp     comment 'http -> https redirect only, no ACME'
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

## 1b. Two identities, and why

The services run as **`talkwisp-svc`** -- a system account with no login shell
that has never been in `google-sudoers`. You log in as `talkwisp`, which does
have passwordless root. That split is the point: an RCE in the API must not be
root on the box holding the database and every credential.

Do not "simplify" the units back to `User=talkwisp`. And note the group
membership cannot be edited away instead -- `google-guest-agent` rebuilds it
from SSH-key metadata. See docs/design-decisions.md.

What `talkwisp-svc` needs, and nothing more:

```bash
sudo useradd --system --shell /usr/sbin/nologin --no-create-home      --user-group talkwisp-svc
sudo chown talkwisp:talkwisp-svc .env && sudo chmod 0640 .env
touch gaps.jsonl messages.jsonl feedback.jsonl
sudo chown talkwisp:talkwisp-svc *.jsonl && sudo chmod 0660 *.jsonl
```

**Create all three JSONL files even if empty.** They are opened in append mode,
which creates them -- and creating a file needs write on the directory, which
this account deliberately does not have. `console._log` does not catch, so a
missing `feedback.jsonl` is a 500 on the first Right/Wrong click and nowhere
else.

## 1c. The supervisor's log directory

`supervise.py` writes one log per tenant into `logs/`, and `talkwisp-svc`
deliberately has no write access to the repo directory -- so the directory must
exist and be group-writable before the service starts, exactly like the JSONL
files above:

```bash
mkdir -p logs
sudo chown talkwisp:talkwisp-svc logs
sudo chmod 2770 logs          # setgid: files the service creates keep the group
```

Without it every reconcile fails with `PermissionError(13)` and no bot starts.
The loop reports it and keeps retrying rather than crashing, so the symptom is a
supervisor that is `active` while nothing polls -- worth recognising.

## 2. The secrets file

**There is only one, and it is the repo's `.env`.** An earlier draft of this
file had you create a second at `/etc/talkwisp/env` for systemd. That was a
mistake: `migrate.py`, `seed.py` and every check script read the repo `.env`
through `load_dotenv()`, so a second copy means two files holding the same
secrets, and they drift. A drifted secret fails at runtime, not at edit time.

The security difference is close to nil. systemd injects the values into the
process environment either way, the process runs as `talkwisp`, and `talkwisp`
can read them from `/proc/<pid>/environ` whoever owns the file. What protects it
is the mode, which it already has:

```bash
ls -l /home/talkwisp/talkwisp/.env      # -rw------- talkwisp talkwisp
```

It is already populated. What is still missing:

```
TELEGRAM_PLATFORM_BOT_TOKEN=<@talkwisp_bot token>   # the LOGIN signer
TELEGRAM_LOGIN_ENABLED=true
```

`TELEGRAM_BOT_TOKEN` is the AGENT bot (`@avisenamed_bot`) and
`TELEGRAM_PLATFORM_BOT_TOKEN` is the one that signs web logins. Different bots
on purpose; conflating them is the mistake the split exists to prevent.

And the row that still says `CHANGEME`:

```bash
cd /home/talkwisp/talkwisp
psql "$(grep '^ADMIN_DATABASE_URL=' .env | cut -d= -f2-)"   -c "update business set bot_token = '<@avisenamed_bot token>' where name = 'Default business';"
```

---

## 2b. The Cloudflare Origin Certificate

Caddy does **not** use Let's Encrypt here, and the Caddyfile explains at length
why. Create the certificate first or Caddy will not start.

Cloudflare -> **SSL/TLS -> Origin Server -> Create Certificate**. Accept the
defaults (RSA, 15 years), covering `talkwisp.uz` and `*.talkwisp.uz`. Copy both
blocks out -- the private key is shown **once**.

```bash
sudo install -d -m 0755 /etc/caddy
sudo nano /etc/caddy/origin.pem      # paste the certificate
sudo nano /etc/caddy/origin.key      # paste the private key
sudo chown root:caddy /etc/caddy/origin.pem /etc/caddy/origin.key
sudo chmod 0640 /etc/caddy/origin.pem /etc/caddy/origin.key
```

Confirm Cloudflare's SSL/TLS mode is **Full (strict)**. That is what makes the
origin certificate load-bearing rather than decorative.

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

## Already done — do not redo these

The database is set up and verified. Recorded so nobody repeats it:

- 8 migrations applied; `talkwisp_app` created, non-superuser, no BYPASSRLS
- RLS enabled **and forced** on all 6 tenant tables, `retrievable_fact` is
  `security_invoker`, and an untenanted read raises rather than returning rows
- Seeded: 138 confirmed facts, 2 waiting, 2 sources, 8 chunks, 106 aliases
- Resend verified with a real send through the real app

## After it is up — verify from a different network

Not from the box and not from the machine that deployed it: a proxy
misconfiguration is invisible from inside.

```
https://talkwisp.uz/app/            loads the sign-in screen
https://talkwisp.uz/review          401, not data
https://talkwisp.uz/openapi.json    404 — the docs routes do not exist
curl -sI https://talkwisp.uz/auth/callback?token=x | grep -i set-cookie
```

The last one is the point: the `Secure` flag is derived from
`PUBLIC_BASE_URL`'s scheme, so it should have flipped on by itself. Confirm it
did rather than assuming — a cookie without `Secure` on an HTTPS site is a
cookie that will travel over plain HTTP the first time something downgrades.
