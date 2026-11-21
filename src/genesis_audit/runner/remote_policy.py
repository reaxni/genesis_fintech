"""Trusted half of the isolated policy transport."""

from pathlib import Path
from socket import AF_UNIX, SOCK_STREAM, socket
from time import monotonic

from genesis_audit.contracts.wire import receive, send


class PolicyListener:
    def __init__(self, path: Path):
        self.path = path
        self.server = socket(AF_UNIX, SOCK_STREAM)
        try:
            self.server.bind(str(path))
            path.chmod(0o666)
            self.server.listen(1)
        except OSError:
            self.server.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.server.close()
        self.path.unlink(missing_ok=True)

    def episode(self, policy: str):
        return _RemoteEpisode(self.server, policy)


class _RemoteEpisode:
    def __init__(self, server: socket, policy: str):
        self.server = server
        self.policy = policy
        self.connection = None
        self.reader = None

    def __enter__(self):
        return self.choose

    def __exit__(self, *_args):
        if self.reader is not None:
            self.reader.close()
        if self.connection is not None:
            self.connection.close()

    def choose(self, observation: dict, deadline: float) -> dict:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        if self.connection is None:
            self.server.settimeout(remaining)
            self.connection, _ = self.server.accept()
            self.reader = self.connection.makefile("rb")
        self.connection.settimeout(max(0.001, deadline - monotonic()))
        send(self.connection, {"policy": self.policy, "observation": observation})
        self.connection.settimeout(max(0.001, deadline - monotonic()))
        return receive(self.reader)
