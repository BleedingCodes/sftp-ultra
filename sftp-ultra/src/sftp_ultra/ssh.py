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
        ssh = self._new_ssh()
        sftp = ssh.open_sftp()
        try:
            yield sftp
        finally:
            try:
                sftp.close()
            finally:
                ssh.close()


def prompt_credentials(config: Config) -> Credentials:
    return Credentials(
        password=getpass.getpass(
            f"SSH password for {config.username}@{config.target}: "
        )
    )
