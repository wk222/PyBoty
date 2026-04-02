"""Tests for core.tool_cache."""

from __future__ import annotations

import threading
import time

from core.assets.tools.tool_cache import ToolCache, cached_tool_call


class TestToolCache:
    def setup_method(self):
        self.cache = ToolCache(default_ttl=10.0)

    def test_miss_then_hit(self):
        hit, val = self.cache.get("tool_a", "hash1")
        assert not hit
        self.cache.set("tool_a", "hash1", "result_1")
        hit, val = self.cache.get("tool_a", "hash1")
        assert hit
        assert val == "result_1"

    def test_different_tools_different_keys(self):
        self.cache.set("tool_a", "h1", "r1")
        self.cache.set("tool_b", "h1", "r2")
        _, v1 = self.cache.get("tool_a", "h1")
        _, v2 = self.cache.get("tool_b", "h1")
        assert v1 == "r1"
        assert v2 == "r2"

    def test_ttl_expiry(self):
        self.cache.set("tool_a", "h1", "old", ttl=0.01)
        time.sleep(0.05)
        hit, _ = self.cache.get("tool_a", "h1")
        assert not hit

    def test_no_ttl(self):
        self.cache.set("tool_a", "h1", "persistent", ttl=None)
        hit, val = self.cache.get("tool_a", "h1")
        assert hit
        assert val == "persistent"

    def test_invalidate_all(self):
        self.cache.set("t1", "h1", "a")
        self.cache.set("t2", "h2", "b")
        removed = self.cache.invalidate()
        assert removed == 2
        assert self.cache.stats()["size"] == 0

    def test_invalidate_specific_tool(self):
        self.cache.set("t1", "h1", "a")
        self.cache.set("t1", "h2", "b")
        self.cache.set("t2", "h3", "c")
        removed = self.cache.invalidate("t1")
        assert removed == 2
        assert self.cache.stats()["size"] == 1

    def test_stats(self):
        self.cache.set("t", "h", "v")
        self.cache.get("t", "h")  # hit
        self.cache.get("t", "missing")  # miss
        s = self.cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["size"] == 1
        assert s["hit_rate"] == 0.5

    def test_stats_empty(self):
        s = self.cache.stats()
        assert s["hit_rate"] == 0.0

    def test_evict_expired(self):
        self.cache.set("t", "h1", "a", ttl=0.01)
        self.cache.set("t", "h2", "b", ttl=None)
        time.sleep(0.05)
        evicted = self.cache._evict_expired()
        assert evicted == 1
        assert self.cache.stats()["size"] == 1


class TestHashArgs:
    def test_deterministic(self):
        h1 = ToolCache.hash_args({"a": 1, "b": "hello"})
        h2 = ToolCache.hash_args({"b": "hello", "a": 1})
        assert h1 == h2

    def test_different_args_different_hash(self):
        h1 = ToolCache.hash_args({"x": 1})
        h2 = ToolCache.hash_args({"x": 2})
        assert h1 != h2


class TestCachedToolCall:
    def test_caches_result(self):
        cache = ToolCache()
        call_count = 0

        def my_tool(x=0):
            nonlocal call_count
            call_count += 1
            return x * 2

        r1 = cached_tool_call(cache, "my_tool", {"x": 5}, my_tool)
        r2 = cached_tool_call(cache, "my_tool", {"x": 5}, my_tool)
        assert r1 == 10
        assert r2 == 10
        assert call_count == 1

    def test_not_cacheable(self):
        cache = ToolCache()
        call_count = 0

        def my_tool(x=0):
            nonlocal call_count
            call_count += 1
            return x

        cached_tool_call(cache, "t", {"x": 1}, my_tool, cacheable=False)
        cached_tool_call(cache, "t", {"x": 1}, my_tool, cacheable=False)
        assert call_count == 2

    def test_different_args_not_cached(self):
        cache = ToolCache()
        call_count = 0

        def my_tool(x=0):
            nonlocal call_count
            call_count += 1
            return x

        cached_tool_call(cache, "t", {"x": 1}, my_tool)
        cached_tool_call(cache, "t", {"x": 2}, my_tool)
        assert call_count == 2


class TestConcurrency:
    def test_concurrent_reads_writes(self):
        cache = ToolCache(default_ttl=60.0)
        errors = []

        def writer():
            try:
                for i in range(50):
                    cache.set("t", f"h{i}", f"v{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    cache.get("t", f"h{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
