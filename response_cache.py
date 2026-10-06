"""Bounded, in-memory success-only TTL cache; owned by one application session."""
from copy import deepcopy
import json
import time


class ResponseCache:
    def __init__(self, ttl_seconds=3600, max_entries=128, clock=time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._entries = {}

    def clear(self):
        self._entries.clear()

    def get_or_load(self, query, variables, loader):
        key = json.dumps([query, variables or {}], sort_keys=True)
        now = self.clock()
        self._entries = {k: v for k, v in self._entries.items() if v[0] > now}
        if key in self._entries:
            return deepcopy(self._entries[key][1])
        value = loader()  # Exceptions, including auth/schema/rate failures, are never cached.
        if self.ttl_seconds > 0:
            if len(self._entries) >= self.max_entries:
                del self._entries[next(iter(self._entries))]
            self._entries[key] = (self.clock() + self.ttl_seconds, deepcopy(value))
        return deepcopy(value)
