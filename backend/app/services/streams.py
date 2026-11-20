"""Incremental readers for large JSON and JSONL feeds."""

from __future__ import annotations

import io
from collections.abc import Iterable, Iterator
from typing import Any

import ijson


class ByteChunkReader(io.RawIOBase):
    """Present an iterable of byte chunks as a readable binary stream."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._leftover = b""

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        while not self._leftover:
            try:
                self._leftover = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(buffer), len(self._leftover))
        buffer[:size] = self._leftover[:size]
        self._leftover = self._leftover[size:]
        return size


def iter_json_items(chunks: Iterable[bytes], prefix: str) -> Iterator[Any]:
    reader = io.BufferedReader(ByteChunkReader(chunks))
    yield from ijson.items(reader, prefix)
