import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import httpx

from local_codex.app import create_app
from local_codex.config import load_model_config
from local_codex.runtime import Runtime
from local_codex.service import Result, execute, _prepare_body
from local_codex.settings import SETTINGS
from local_codex.routing import TurnRouter
from local_codex.state import ResponseStateStore


class ConfigurationTests(unittest.TestCase):
    def test_import_has_no_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "data"
            subprocess.run(
                [sys.executable, "-c", "import local_codex.app"],
                env={**os.environ, "LOCAL_CODEX_HOME": str(destination)},
                check=True,
            )
            self.assertFalse(destination.exists())

    def test_profiles_are_validated_and_defaults_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "localcodex.toml"
            path.write_text(
                'schema_version = 1\n[models.build]\nsource = "my-coder:7b"\ncontext = 16384\nreasoning = "none"\n'
            )
            config = load_model_config(path)
            self.assertEqual("my-coder:7b", config.build.source)
            self.assertEqual(16384, config.build.context)
            self.assertEqual("qwen3-vl:30b", config.vision.source)
            for invalid in (
                'source = "https://example.com"',
                "context = true",
                'source = "model:cloud"',
                'reasoning = "ultra"',
                "typo = 1",
            ):
                with self.subTest(invalid=invalid):
                    path.write_text("schema_version = 1\n[models.build]\n" + invalid)
                    with self.assertRaises(ValueError):
                        load_model_config(path)

    def test_turn_ids_are_scoped_to_session(self):
        router = TurnRouter()
        first = router.choose({"input": "create a plan"}, {"session-id": "a", "x-client-request-id": "same"})
        second = router.choose({"input": "implement"}, {"session-id": "b", "x-client-request-id": "same"})
        self.assertEqual(SETTINGS.plan_model, first.model)
        self.assertEqual(SETTINGS.build_model, second.model)

    def test_previous_response_requires_matching_live_session(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResponseStateStore(Path(directory) / "state.db", 3600, 10)
            try:
                store.put("r", "a", "t", [], [])
                self.assertEqual([], store.expand("r", [], session_id="a"))
                for response, session in (("r", "b"), ("missing", "a")):
                    with self.assertRaises(ValueError):
                        store.expand(response, [], session_id=session)
                store.ttl_seconds = -1
                with self.assertRaises(ValueError):
                    store.expand("r", [], session_id="a")
            finally:
                store.close()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = Runtime(state_dir=Path(self.directory.name))

    async def asyncTearDown(self):
        await self.runtime.stop()
        self.directory.cleanup()

    async def test_application_factory_owns_lifespan(self):
        runtime = AsyncMock()
        application = create_app(lambda: runtime)
        async with application.router.lifespan_context(application):
            self.assertIs(runtime, application.state.runtime)
            runtime.start.assert_awaited_once()
        runtime.stop.assert_awaited_once()

    async def test_failed_start_closes_resources(self):
        runtime = AsyncMock()
        runtime.start.side_effect = RuntimeError("startup")
        application = create_app(lambda: runtime)
        with self.assertRaises(RuntimeError):
            async with application.router.lifespan_context(application):
                pass
        runtime.stop.assert_awaited_once()

    async def test_host_instructions_and_model_profile_survive(self):
        decision = self.runtime.router.choose({"input": "code"}, {"session-id": "s"})
        prepared, _ = _prepare_body(
            self.runtime, {"input": "code", "instructions": "Preserve my rules"}, {}, decision
        )
        self.assertTrue(prepared["instructions"].startswith("Preserve my rules"))
        self.assertNotIn("summary", prepared["reasoning"])

    async def test_serializes_whole_service_and_releases_queue_on_cancel(self):
        entered = asyncio.Event()
        finish = asyncio.Event()

        async def slow(*args):
            entered.set()
            await finish.wait()
            return Result({})

        with patch("local_codex.service._execute", side_effect=slow) as operation:
            first = asyncio.create_task(execute(self.runtime, {}, {}))
            await entered.wait()
            second = asyncio.create_task(execute(self.runtime, {}, {}))
            await asyncio.sleep(0)
            self.assertEqual(1, operation.call_count)
            second.cancel()
            await asyncio.gather(second, return_exceptions=True)
            self.assertEqual(1, self.runtime.pending_requests)
            finish.set()
            await first
            self.assertEqual(0, self.runtime.pending_requests)
            self.assertFalse(self.runtime.inference_lock.locked())

    async def test_queue_overload_and_timeout(self):
        self.runtime.pending_requests = SETTINGS.max_pending_requests + 1
        self.assertEqual(429, (await execute(self.runtime, {}, {})).status_code)
        self.runtime.pending_requests = 0
        self.runtime.settings = replace(SETTINGS, request_timeout_seconds=0.01)
        await self.runtime.inference_lock.acquire()
        try:
            self.assertEqual(504, (await execute(self.runtime, {}, {})).status_code)
        finally:
            self.runtime.inference_lock.release()
        self.assertEqual(0, self.runtime.pending_requests)

    async def test_real_transport_contract_with_fake_ollama(self):
        payload = {
            "id": "r1",
            "output": [
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}
            ],
            "usage": {"input_tokens": 5, "output_tokens": 1},
        }

        async def upstream(request):
            if request.url.path == "/api/ps":
                return httpx.Response(200, json={"models": []})
            if request.url.path == "/api/show":
                return httpx.Response(200, json={"capabilities": ["completion", "tools", "thinking"]})
            return httpx.Response(
                200,
                text="data: "
                + json.dumps({"type": "response.completed", "response": payload})
                + "\n\ndata: [DONE]\n\n",
            )

        self.runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        application = create_app()
        application.state.runtime = self.runtime
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1"
        ) as client:
            response = await client.post(
                "/v1/responses", json={"input": "hello", "stream": True}, headers={"session-id": "s"}
            )
            self.assertEqual(200, response.status_code, response.text)
            self.assertIn("response.completed", response.text)
            self.assertEqual(1, self.runtime.usage.statistics(period="all")["output_tokens"])
