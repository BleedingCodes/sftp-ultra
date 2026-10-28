from __future__ import annotations

import getpass
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import paramiko

from .model import Config

# F3 (AUDIT-FINDINGS): tool-specific known-hosts file for TOFU pinning under
# --trust-unknown-host. Deliberately NOT ~/.ssh/known_hosts, so the tool never
# rewrites entries that the user's other SSH sessions verify against.
TOOL_KNOWN_HOSTS = Path.home() / ".local" / "state" / "sftp-ultra" / "known_hosts"


@dataclass
class Credentials:
    password: str


class SFTPConnectionFactory:
    def __init__(self, config: Config, credentials: Credentials) -> None:
        self.config = config
        self.credentials = credentials
        self._thread_local = threading.local()

    @staticmethod
    def _is_alive(ssh: paramiko.SSHClient | None) -> bool:
        if ssh is None:
            return False
        transport = ssh.get_transport()
        return transport is not None and transport.is_active()

    def _new_ssh(self) -> paramiko.SSHClient:
        ssh = paramiko.SSHClient()
        ssh.load_system_host_keys()

        if self.config.trust_unknown_host:
            # F3: load_host_keys(filename) sets the known-hosts filename, and
            # only then does paramiko's AutoAddPolicy persist accepted keys
            # (save_host_keys). With plain load_system_host_keys() the
            # filename stays unset, keys were accepted in memory only, and
            # every run re-trusted the unknown host — no trust-on-first-use.
            # Known hosts with a *changed* key still fail with
            # BadHostKeyException, so TOFU applies: first run pins, later
            # runs verify against what was pinned (and what the user has in
            # the system file, which we still verify against first).
            known_hosts = TOOL_KNOWN_HOSTS
            known_hosts.parent.mkdir(parents=True, exist_ok=True)
            if not known_hosts.exists():
                # paramiko's load_host_keys(filename) opens the file for
                # reading; an empty file is a valid (empty) pin set.
                known_hosts.touch()
            ssh.load_host_keys(str(known_hosts))
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())

        ssh.connect(
            hostname=self.config.target,
            port=self.config.port,
            username=self.config.username,
            password=self.credentials.password,
            timeout=self.config.timeout_seconds,
            banner_timeout=self.config.timeout_seconds,
            auth_timeout=self.config.timeout_seconds,
            # F4: enforce the documented behavior (README:45) — only the
            # typed password is submitted to the server. The SSH agent and
            # ~/.ssh default keys are deliberately not consulted, so a wrong
            # typed password can never produce a successful connection and
            # the server's auth trail matches the README's promise.
            allow_agent=False,
            look_for_keys=False,
        )
        return ssh

    @contextmanager
    def open(self) -> Iterator[paramiko.SFTPClient]:
        # Reuse one live SSH/SFTP connection per worker thread instead of
        # doing a fresh TCP + SSH handshake for every single file (and every
        # retry). ThreadPoolExecutor threads live for the whole run, so the
        # cached connection persists across transfer_one() calls on the
        # same thread and is only rebuilt if it has actually dropped.
        ssh = getattr(self._thread_local, "ssh", None)
        sftp = getattr(self._thread_local, "sftp", None)

        if not self._is_alive(ssh):
            if ssh is not None:
                try:
                    ssh.close()
                except Exception:
                    pass
            ssh = self._new_ssh()
            try:
                sftp = ssh.open_sftp()
            except Exception:
                # N1 (AUDIT-REVIEW): a fresh SSHClient whose open_sftp() call
                # failed is neither cached nor closed by the poisoned-try
                # below (that only wraps the yield) — it leaked a live TCP
                # socket + paramiko transport thread per retry attempt.
                try:
                    ssh.close()
                except Exception:
                    pass
                raise
            channel = sftp.get_channel()
            if channel is not None:
                # F2: configure the SFTP data-phase timeout. --timeout
                # previously covered only TCP connect / SSH banner / auth
                # (paramiko connect semantics); the channel's default
                # ``Channel.timeout = None`` let a stalled read wedge the
                # worker -- and thus the whole unattended run -- forever.
                # With a timeout set, a stalled read raises socket.timeout,
                # which the retry loop converts into a reconnect attempt.
                channel.settimeout(float(self.config.timeout_seconds))
            self._thread_local.ssh = ssh
            self._thread_local.sftp = sftp

        try:
            yield sftp
        except Exception:
            # The connection may be poisoned (broken pipe, dropped
            # transport, etc.) - drop the cache so the *next* call to
            # open() on this thread reconnects instead of reusing a dead
            # socket.
            try:
                ssh.close()
            except Exception:
                pass
            self._thread_local.ssh = None
            self._thread_local.sftp = None
            raise

    def close_all_for_this_thread(self) -> None:
        ssh = getattr(self._thread_local, "ssh", None)
        if ssh is not None:
            try:
                ssh.close()
            finally:
                self._thread_local.ssh = None
                self._thread_local.sftp = None


def prompt_credentials(config: Config) -> Credentials:
    # F11: unattended runs need a non-interactive credential source; without
    # one, getpass' no-tty fallback rung with the echo-on reading the typed
    # password (GetPassWarning "input may be echoed"). Order:
    # 1. SFTP_ULTRA_PASSWORD (documented unattended/CI source; unlogged).
    # 2. getpass on a real tty (reading the user's typed keystrokes).
    # 3. Fail cleanly: no tty + no env credential => configuration error
    #    (exit 2 via the ValueError path) instead of echoing the password.
    env_password = os.environ.get("SFTP_ULTRA_PASSWORD")
    if env_password:
        return Credentials(password=env_password)

    if not sys.stdin.isatty():
        raise ValueError(
            "No TTY on stdin and SFTP_ULTRA_PASSWORD is not set. "
            "Refusing the echo-risk no-tty fallback; for unattended runs set "
            "SFTP_ULTRA_PASSWORD."
        )

    return Credentials(
        password=getpass.getpass(
            f"SSH password for {config.username}@{config.target}: "
        )
    )
