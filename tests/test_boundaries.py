import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

import httpx

from local_codex.database import open_database
from local_codex.model_validation import validate_model_info
from local_codex.ollama import buffered_response, ResponseLimitError
from local_codex.protocol import normalize_tools


class DatabaseTests(unittest.TestCase):
    def test_legacy_backup_preserves_rows_and_future_schema_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            legacy = sqlite3.connect(path)
            legacy.execute("CREATE TABLE example(value TEXT)")
            legacy.execute("INSERT INTO example VALUES ('keep')")
            legacy.commit()
            legacy.close()
            adopted = open_database(path)
            self.assertEqual(1, adopted.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual("keep", adopted.execute("SELECT value FROM example").fetchone()[0])
            adopted.close()
            backup = sqlite3.connect(path.with_name(path.name + ".schema-0.bak"))
            self.assertEqual("keep", backup.execute("SELECT value FROM example").fetchone()[0])
            backup.close()
            current = open_database(path)
            current.execute("PRAGMA user_version=999")
            current.close()
            with self.assertRaises(RuntimeError):
                open_database(path)

    def test_model_and_tool_boundaries(self):
        valid = {"capabilities": ["completion", "tools", "vision", "thinking"]}
        validate_model_info("local", "vision", valid, "high")
        for info in ({**valid, "remote_host": "https://cloud.example"}, {"capabilities": ["completion"]}):
            with self.assertRaises(ValueError):
                validate_model_info("local", "vision", info, "high")
        with self.assertRaises(ValueError):
            normalize_tools([{"type": "custom", "name": "apply_patch"}])
        with self.assertRaises(ValueError):
            normalize_tools([{"type": "function", "name": "x"}] * 2)


class FragmentStream(httpx.AsyncByteStream):
    def __init__(self, chunks, wait=None):
        self.chunks = chunks
        self.wait = wait
        self.closed = False
        self.entered = asyncio.Event()

    async def __aiter__(self):
        self.entered.set()
        for chunk in self.chunks:
            yield chunk
        if self.wait:
            await self.wait.wait()

    async def aclose(self):
        self.closed = True


class OllamaTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragments_and_response_limit_close_upstream(self):
        for limit in (4, 2):
            stream = FragmentStream([b"ab", b"cd"])
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))) as client:
                observer = Mock()
                if limit == 4:
                    result = await buffered_response(client, "http://127.0.0.1", {}, limit, observer)
                    self.assertEqual(b"abcd", result.content)
                    self.assertEqual(2, observer.call_count)
                else:
                    with self.assertRaises(ResponseLimitError):
                        await buffered_response(client, "http://127.0.0.1", {}, limit, observer)
                self.assertTrue(stream.closed)

    async def test_cancellation_closes_stream(self):
        stream = FragmentStream([], asyncio.Event())
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))) as client:
            task = asyncio.create_task(buffered_response(client, "http://127.0.0.1", {}, 100, Mock()))
            await stream.entered.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(stream.closed)

    async def test_upstream_error_body_is_not_observed(self):
        stream = FragmentStream([b"private prompt echoed in error"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, stream=stream))) as client:
            observer = Mock()
            result = await buffered_response(client, "http://127.0.0.1", {}, 100, observer)
            self.assertEqual(500, result.status_code)
            observer.assert_not_called()
