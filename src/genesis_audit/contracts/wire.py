"""Bounded JSON Lines exchange on a private local socket."""

import json
from socket import socket
from typing import BinaryIO

MAX_MESSAGE_BYTES = 1024 * 1024


def send(connection: socket, message: dict) -> None:
    payload = json.dumps(message, ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("IPC message is too large")
    connection.sendall(payload + b"\n")


def receive(reader: BinaryIO) -> dict:
    line = reader.readline(MAX_MESSAGE_BYTES + 2)
    if not line:
        raise EOFError("IPC session ended")
    if len(line) > MAX_MESSAGE_BYTES + 1 or not line.endswith(b"\n"):
        raise ValueError("invalid IPC frame")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("IPC frame must contain an object")
    return message
