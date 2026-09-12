"""One process that keeps a Telegram poller running for every business that has one.

    uv run python supervise.py

WHAT IT REPLACES. bot.py binds its token and business as module globals at
startup, so one process serves exactly one business, permanently. Starting one
meant `systemctl enable --now talkwisp-bot@Name`, which needs root -- and the web
app deliberately cannot have root. So a customer pasted a token into Settings and
nothing happened until somebody noticed and ran a command over SSH. This is what
makes "we'll switch your bot on" true without a human in it.

IT IS A RECONCILE LOOP, NOT AN EVENT HANDLER. Every tick it asks two questions --
which businesses should be polled, and which children do I have -- and fixes the
difference. There is no code path for "a new customer signed up" distinct from
"a process died" or "I was just restarted and have no children at all". All
three are the same difference between two lists, which is why none of them needs
to have been anticipated.

WHY NOT systemctl. Shelling out to it needs root, which is the thing being
avoided. Children are spawned directly; deploy/talkwisp-bot@.service is retired.

THE CEILING IS POSTGRES CONNECTIONS, NOT MEMORY, which is the opposite of what
you would guess from watching `free`. Measured on this box: each bot is ~35 MB
resident against 1.4 GB available -- about forty bots' worth -- but
max_connections is 40 with 3 reserved, the API holds up to 5, and each bot held
up to 5 before this change. That allowed roughly SIX bots under simultaneous
load, and exhausting the pool does not kill the newest bot: it returns "sorry,
too many clients" to everything, including the web app. One tenant's traffic
would take the product down for every tenant.

bot.py now caps its own pool at 2, which is all a single-threaded update loop
can use. MAX_CHILDREN below is derived from that rather than guessed.
"""

import collections
import os
import pathlib
import signal
import subprocess
import sys
import time

from dotenv import load_dotenv

from app.db import assert_app_role, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

HERE = pathlib.Path(__file__).parent
PYTHON = HERE / ".venv" / "bin" / "python"
LOG_DIR = HERE / "logs"

TICK_SECONDS = 15

# (37 usable connections - 5 the API may hold) / 2 per bot, rounded down and
# left some headroom for migrate.py and a psql session. Refused loudly rather
# than silently, because the failure this prevents is global.
MAX_CHILDREN = 14

# Crash backoff. A revoked token fails at getMe immediately, and without this
# that is a tight restart loop hammering Telegram and the log. Doubling, capped,
# and reset once a child has stayed up long enough to be considered healthy.
BACKOFF_START = 5
BACKOFF_MAX = 300
HEALTHY_SECONDS = 120

# How many reconciles in a row may fail before the supervisor gives up and exits
# non-zero.
#
# WHY GIVE UP AT ALL. Catching and retrying is right for a database blip and
# WRONG AS A SIGNAL: it means a permissions problem looks like health. When
# logs/ did not exist, every reconcile failed with PermissionError, no bot ever
# started, and `systemctl is-active` said `active` -- the one state where
# nothing is polling and nothing says so.
#
# Exiting hands the judgement to systemd, which is better at it than a loop:
# Restart=always brings us back for a transient fault, and StartLimitBurst=5 in
# 60s puts the unit into `failed` for a persistent one. That guard only started
# working yesterday -- it had been in [Service], where systemd ignores it.
#
# Eight ticks is two minutes at TICK_SECONDS=15: long enough that a postgres
# restart rides through, short enough that a broken box is visible before anyone
# would have noticed by other means.
MAX_CONSECUTIVE_FAILURES = 8


class Child:
    __slots__ = ("process", "name", "started", "failures", "next_try", "log")

    def __init__(self, name: str) -> None:
        self.process: subprocess.Popen | None = None
        self.name = name
        self.started = 0.0
        self.failures = 0
        self.next_try = 0.0
        self.log = None


def desired() -> dict[str, str]:
    """{business_id: name} for everything that should be polled.

    ONE QUESTION, ANSWERED BY THE DATABASE. app_businesses_to_poll() is
    SECURITY DEFINER and filters on `approved` itself -- see migrations/0013 for
    why that belongs in the WHERE clause and not in this file. It returns no
    tokens; a child reads its own once it is bound.
    """
    with pool.connection() as conn:
        return {str(bid): name
                for bid, name in conn.execute(
                    "select id, name from app_businesses_to_poll()").fetchall()}


def spawn(business_id: str, name: str, child: Child) -> None:
    """Start one bot.py, by ID.

    NEVER BY NAME. business.name is not unique, signup lets a stranger pick
    their own, and resolving one here could start a poller for the wrong tenant
    with that tenant's token.
    """
    LOG_DIR.mkdir(exist_ok=True)
    # Per tenant, because twenty bots on one stream is unreadable and the first
    # thing anyone wants when a bot misbehaves is that bot's log. Appended, so a
    # restart does not erase the reason for the restart.
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
    child.log = open(LOG_DIR / f"bot-{safe}-{business_id[:8]}.log", "a",
                     encoding="utf-8", buffering=1)
    child.log.write(f"\n--- supervisor starting {name!r} at "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    child.process = subprocess.Popen(
        [str(PYTHON), "-u", str(HERE / "bot.py"), "--business-id", business_id],
        cwd=HERE, stdout=child.log, stderr=subprocess.STDOUT,
        # Its own process group, so a Ctrl-C in a terminal reaches the
        # supervisor and lets IT decide how children stop, rather than the
        # terminal signalling everyone at once and racing our own teardown.
        start_new_session=True)
    child.started = time.monotonic()
    print(f"started {name!r} ({business_id[:8]}) pid={child.process.pid}",
          flush=True)


def stop(child: Child, why: str) -> None:
    """SIGTERM, then wait, then SIGKILL.

    bot.py finishes the update it is on and exits between iterations, so the
    usual path is a clean handover rather than a killed generation. The wait is
    generous for that reason: a model call in flight is seconds, and cutting it
    short recreates exactly the duplicate-answer problem the handler removes.
    """
    if child.process is None:
        return
    print(f"stopping {child.name!r}: {why}", flush=True)
    try:
        child.process.terminate()
        child.process.wait(timeout=45)
    except subprocess.TimeoutExpired:
        print(f"  {child.name!r} ignored SIGTERM, killing", flush=True)
        child.process.kill()
        child.process.wait(timeout=10)
    except Exception as exc:  # noqa: BLE001
        print(f"  stopping {child.name!r} failed: {exc!r}", flush=True)
    finally:
        if child.log:
            child.log.close()
            child.log = None
        child.process = None


def reconcile(children: dict[str, Child]) -> None:
    want = desired()
    now = time.monotonic()

    # --- gone, or no longer eligible ---------------------------------------
    for business_id in list(children):
        if business_id not in want:
            stop(children[business_id],
                 "its token was removed or its approval was withdrawn")
            del children[business_id]

    # --- reap, so terminated children do not become zombies ----------------
    for business_id, child in list(children.items()):
        if child.process is None:
            continue
        code = child.process.poll()
        if code is None:
            if now - child.started > HEALTHY_SECONDS and child.failures:
                # Stayed up. Forget the earlier crashes so a bot that failed at
                # 3am does not carry a five-minute backoff for the rest of the
                # week.
                child.failures = 0
            continue
        child.process.wait()  # collect the exit status; this is the reaping
        if child.log:
            child.log.close()
            child.log = None
        child.process = None
        child.failures += 1
        delay = min(BACKOFF_START * (2 ** (child.failures - 1)), BACKOFF_MAX)
        child.next_try = now + delay
        print(f"{child.name!r} exited with {code}; retry in {delay}s "
              f"(failure {child.failures})", flush=True)

    # --- start what is missing ---------------------------------------------
    running = sum(1 for c in children.values() if c.process is not None)
    for business_id, name in want.items():
        child = children.setdefault(business_id, Child(name))
        child.name = name
        if child.process is not None or now < child.next_try:
            continue
        if running >= MAX_CHILDREN:
            # Loud and repeated, not once. A ceiling that announced itself only
            # the first time would be invisible by the time anyone looked, and
            # this is the state where a paying customer's bot is not running.
            print(f"AT CAPACITY: {running} bots running, cap is {MAX_CHILDREN}. "
                  f"{name!r} is NOT being started. The limit is Postgres "
                  f"connections, not memory -- raise max_connections and this "
                  f"cap together, or the failure is 'too many clients' for "
                  f"every tenant including the web app.", flush=True)
            continue
        spawn(business_id, name, child)
        running += 1


def main() -> None:
    # POOL FIRST. assert_app_role() asks the database who it is connected as,
    # so it needs a connection -- calling it first raised PoolClosed and the
    # supervisor never started. app/main.py's lifespan has the same two lines in
    # the opposite order, which is the order that works.
    pool.open()
    assert_app_role()
    children: dict[str, Child] = {}
    stopping = collections.deque(maxlen=1)

    def handle(signum, frame):  # noqa: ARG001
        stopping.append(True)
        print("shutting down; stopping children first.", flush=True)

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    print(f"supervisor up. tick={TICK_SECONDS}s cap={MAX_CHILDREN}", flush=True)
    consecutive = 0
    try:
        while not stopping:
            try:
                reconcile(children)
                consecutive = 0
            except Exception as exc:  # noqa: BLE001
                # A database blip must not take the supervisor down and every
                # bot with it. Children keep running while this retries; the
                # reconcile is idempotent, so the next tick catches up.
                consecutive += 1
                print(f"reconcile failed ({consecutive}/"
                      f"{MAX_CONSECUTIVE_FAILURES}), retrying next tick: "
                      f"{exc!r}", flush=True)
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    # Loud, and then out. Staying up here would be the failure
                    # mode this exists to remove: a unit reporting `active`
                    # while no bot has ever started.
                    print(f"GIVING UP after {consecutive} consecutive failed "
                          f"reconciles. Exiting non-zero so this shows as "
                          f"failed rather than active-but-doing-nothing. Last "
                          f"error: {exc!r}", flush=True)
                    raise SystemExit(1)
            for _ in range(TICK_SECONDS):
                if stopping:
                    break
                time.sleep(1)
    finally:
        # CHILDREN FIRST. systemd's KillMode=mixed sends SIGTERM to this process
        # only, so anything still running when we exit is orphaned -- reparented
        # to init, still polling Telegram, and invisible to the next supervisor,
        # which would start a SECOND poller for the same token. Two consumers on
        # one token means Telegram hands each update to whichever asks first and
        # the customer sees answers arrive unpredictably.
        for child in list(children.values()):
            stop(child, "supervisor shutting down")
        pool.close()
        print("supervisor stopped.", flush=True)


if __name__ == "__main__":
    main()
