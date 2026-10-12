"""Small LRU cache for generated lines."""

from __future__ import annotations

from collections import OrderedDict
from typing import Hashable


class LRU:
    def __init__(self, size: int):
        self.size = max(0, size)
        self._d: OrderedDict[Hashable, object] = OrderedDict()

    def get(self, key: Hashable) -> object | None:
        if key not in self._d:
            return None
        self._d.move_to_end(key)
        return self._d[key]

    def put(self, key: Hashable, value: object) -> None:
        if self.size == 0:
            return
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)
