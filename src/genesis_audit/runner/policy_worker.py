"""Installed as a standalone entry point in the policy container."""

from copy import deepcopy
from pathlib import Path
from random import Random
from socket import AF_UNIX, SOCK_STREAM, socket
from time import sleep
import sys

from genesis_audit.contracts.wire import receive, send
from genesis_audit.policies import choose_action


def work(path: Path) -> None:
    while True:
        with socket(AF_UNIX, SOCK_STREAM) as connection:
            try:
                connection.connect(str(path))
            except (FileNotFoundError, ConnectionRefusedError):
                sleep(0.05)
                continue
            with connection.makefile("rb") as reader:
                rng = Random(0)
                history = []
                try:
                    while True:
                        message = receive(reader)
                        observation = message["observation"]
                        action = choose_action(
                            message["policy"], deepcopy(observation), deepcopy(history), rng=rng
                        )
                        send(connection, action)
                        history.append({"observation": observation, "action": action})
                except (EOFError, BrokenPipeError, ConnectionResetError):
                    pass


if __name__ == "__main__":
    work(Path(sys.argv[1]))
