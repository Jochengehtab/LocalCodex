import asyncio
import unittest

from local_codex.app import Runtime
from local_codex.monitor import LeaseRegistry
from local_codex.settings import SETTINGS


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {}
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeOllamaClient:
    def __init__(self, loaded):
        self.loaded = set(loaded)
        self.unloaded = []

    async def get(self, url, timeout=None):
        return FakeResponse({"models": [{"name": name} for name in sorted(self.loaded)]})

    async def post(self, url, json=None, timeout=None):
        model = json.get("model")
        self.unloaded.append(model)
        self.loaded.discard(model)
        return FakeResponse()


class RuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, loaded):
        runtime = Runtime.__new__(Runtime)
        runtime.settings = SETTINGS
        runtime.client = FakeOllamaClient(loaded)
        runtime.inference_lock = asyncio.Lock()
        runtime.leases = LeaseRegistry()
        runtime.current_model = SETTINGS.build_model
        return runtime

    async def test_unloads_only_local_aliases_and_verifies_process_list(self):
        runtime = self.runtime([SETTINGS.build_model, SETTINGS.plan_model, "unrelated:latest"])
        self.assertTrue(await runtime.unload_local_models(require_idle=False))
        self.assertEqual({SETTINGS.build_model, SETTINGS.plan_model}, set(runtime.client.unloaded))
        self.assertEqual({"unrelated:latest"}, runtime.client.loaded)
        self.assertIsNone(runtime.current_model)

    async def test_active_launcher_prevents_idle_unload(self):
        runtime = self.runtime([SETTINGS.build_model])
        runtime.leases.register("active", 1)
        self.assertFalse(await runtime.unload_local_models(require_idle=True))
        self.assertEqual([], runtime.client.unloaded)


if __name__ == "__main__":
    unittest.main()
