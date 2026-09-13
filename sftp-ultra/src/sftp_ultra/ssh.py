from __future__ import annotations

import getpass
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import paramiko

from .model import Config


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
            sftp = ssh.open_sftp()
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
    return Credentials(
        password=getpass.getpass(
            f"SSH password for {config.username}@{config.target}: "
        )
    )
