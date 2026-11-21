# SPDX-License-Identifier: GPL-3.0-or-later
"""Server process: connects to the guard and serves local clients."""

import argparse
import asyncio
import contextlib
import logging
import signal
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from remoteg90ctl.config import UNIX_LISTEN_PREFIX, Config, ConfigError, load_config
from remoteg90ctl.logging_setup import kv, setup_logging
from remoteg90ctl.server import logbuffer
from remoteg90ctl.server.accounts import AccountStore
from remoteg90ctl.server.core import ServerCore
from remoteg90ctl.server.logbook import Logbook
from remoteg90ctl.server.unix_transport import UnixSocketTransport
from remoteg90ctl.server.web import WebTransport

logger = logging.getLogger(__name__)


class StartupError(Exception):
    """The server refuses to start."""


def check_startup(config: Config) -> None:
    """Refusals beyond config validation (which already enforces the network barrier)."""
    for entry in config.server.listen:
        if entry.startswith(UNIX_LISTEN_PREFIX):
            # Accepting it silently would suggest the server listens where it does not.
            raise StartupError(
                f"server.listen: {entry!r}: the web server listens on TCP loopback "
                "addresses only (e.g. '127.0.0.1:8078')"
            )


def open_logbook(state_dir: Path) -> Logbook | None:
    """The station log; if it cannot be opened the server runs without it (no_log)."""
    try:
        return Logbook(state_dir)
    except (OSError, sqlite3.Error) as exc:
        logger.error("logbook_unavailable", extra=kv(path=state_dir, error=exc))
        return None


async def run_server(config: Config, stop: asyncio.Event, core: ServerCore | None = None) -> bool:
    """Serve until ``stop`` is set or a restart is asked for, then release any
    transmission and exit. True: a restart was asked for (server_restart)."""
    check_startup(config)
    core = core if core is not None else ServerCore(config)
    transport = UnixSocketTransport(core, config.server.socket_path)
    await transport.start()
    web = (
        WebTransport(
            core,
            config.server.listen,
            config.server.allowed_hosts,
            accounts=AccountStore(config.server.state_dir),
            require_tailnet_identity=config.server.require_tailnet_identity,
            logbook=open_logbook(config.server.state_dir),
        )
        if config.server.listen
        else None
    )
    if web is not None:
        await web.start()
    core_task = asyncio.create_task(core.run())
    try:
        stop_task = asyncio.create_task(stop.wait())
        restart_task = asyncio.create_task(core.restart_requested.wait())
        done, _ = await asyncio.wait(
            {core_task, stop_task, restart_task}, return_when=asyncio.FIRST_COMPLETED
        )
        stop_task.cancel()
        restart_task.cancel()
        if core_task in done:
            core_task.result()  # re-raise an unexpected failure
        restart = restart_task in done and not stop.is_set()
    finally:
        await core.shutdown()
        if web is not None:
            await web.close()
        await transport.close()
        core_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await core_task
        logger.info("server_stopped")
    return restart


# Exit status asking systemd to start the server again (Restart=on-failure
# restarts on any non-zero status); EX_TEMPFAIL from sysexits.h.
EXIT_RESTART = 75


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="remoteg90ctl-server",
        description="remoteG90ctl server (CAT, lease relay, I/Q waterfall)",
    )
    parser.add_argument("--config", type=Path, required=True, help="path to the TOML config")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        check_startup(config)
    except (ConfigError, StartupError) as exc:
        sys.stderr.write(f"remoteg90ctl-server: {exc}\n")
        return 2
    setup_logging(config.logging.level)
    logbuffer.install()  # recent lines for the page's "System log" window

    async def run() -> bool:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        return await run_server(config, stop)

    try:
        restart = asyncio.run(run())
    except Exception as exc:
        logger.exception("server_failed", extra=kv(error=exc))
        return 1
    if restart:
        logger.warning("server_restarting", extra=kv(exit_code=EXIT_RESTART))
        return EXIT_RESTART
    return 0


if __name__ == "__main__":
    sys.exit(main())
