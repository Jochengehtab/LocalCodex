"""Composition and ownership of local adapters; constructed only during startup."""

from __future__ import annotations
import asyncio
import logging
import os
import signal
import httpx
from .monitor import LeaseRegistry
from .routing import TurnRouter
from .settings import SETTINGS, STATE_DIR, MODEL_CONFIG
from .state import ResponseStateStore
from .telemetry import TelemetryHub
from .usage import UsageStore
from local_search.core import SearchClient
from .model_validation import validate_model_info
from .ports import ResponseStore, UsageRepository

LOGGER = logging.getLogger("local-codex")


class Runtime:
    def __init__(
        self,
        settings=SETTINGS,
        state_dir=STATE_DIR,
        *,
        client=None,
        model_config=MODEL_CONFIG,
        store: ResponseStore | None = None,
        usage: UsageRepository | None = None,
        search=None,
        telemetry=None,
        router=None,
    ):
        self.model_config = model_config
        self.pending_requests = 0
        self.settings = settings
        self.router = router if router is not None else TurnRouter(settings)
        self.store = (
            store
            if store is not None
            else ResponseStateStore(
                state_dir / "router.sqlite3",
                ttl_seconds=self.settings.state_ttl_seconds,
                max_rows=self.settings.state_max_rows,
            )
        )
        self.inference_lock = asyncio.Lock()
        self.current_model: str | None = None
        self.client: httpx.AsyncClient | None = client
        self.search = (
            search
            if search is not None
            else SearchClient("http://127.0.0.1:18082", state_dir / "web_cache.sqlite3")
        )
        self.usage = usage if usage is not None else UsageStore(state_dir / "usage.sqlite3")
        # Deliberately memory-only: the tray monitor must never add writes to
        # Ollama's inference path.
        self.telemetry = telemetry if telemetry is not None else TelemetryHub()
        self.leases = LeaseRegistry(ttl_seconds=15.0)
        self.monitor_task: asyncio.Task[None] | None = None
        self.shutdown_task: asyncio.Task[None] | None = None
        self.ollama_version: str | None = None
        self.managed = os.environ.get("LOCAL_CODEX_MANAGED") == "1"
        self._shutdown_requested = False

    async def start(self) -> None:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=self.settings.request_timeout_seconds, trust_env=False)
        self.telemetry.update_all_statistics(self.usage.statistics(period="all", page_size=1))
        try:
            response = await self.client.get(f"{self.settings.ollama_base_url}/api/ps", timeout=5.0)
            response.raise_for_status()
            ps_payload = response.json()
            local_aliases = {
                self.settings.plan_model,
                self.settings.build_model,
                self.settings.vision_model,
            }
            loaded = [
                item.get("name") for item in ps_payload.get("models", []) if item.get("name") in local_aliases
            ]
            if loaded:
                LOGGER.info("Cleaning up stale LocalCodex model(s): %s", ", ".join(loaded))
                await self.unload_local_models(require_idle=False)
                try:
                    refreshed = await self.client.get(f"{self.settings.ollama_base_url}/api/ps", timeout=5.0)
                    refreshed.raise_for_status()
                    ps_payload = refreshed.json()
                except (httpx.HTTPError, ValueError):
                    ps_payload = {"models": []}
            try:
                version_response = await self.client.get(
                    f"{self.settings.ollama_base_url}/api/version", timeout=3.0
                )
                version_response.raise_for_status()
                self.ollama_version = version_response.json().get("version")
            except (httpx.HTTPError, ValueError):
                self.ollama_version = None
            self.telemetry.update_ollama_runtime(ps_payload, self.ollama_version)
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.warning("Could not inspect already loaded Ollama models: %s", exc)
        self.monitor_task = asyncio.create_task(self._monitor_runtime())

    async def _monitor_runtime(self) -> None:
        """Cache inexpensive Ollama runtime data and reap a managed idle router."""
        while True:
            try:
                lease_state = self.leases.snapshot()
                if self.client:
                    response = await self.client.get(f"{self.settings.ollama_base_url}/api/ps", timeout=3.0)
                    response.raise_for_status()
                    self.telemetry.update_ollama_runtime(response.json(), self.ollama_version)
                if (
                    self.managed
                    and lease_state["ever_had_lease"]
                    and lease_state["active_count"] == 0
                    and float(lease_state["empty_seconds"]) >= 20.0
                    and not self._shutdown_requested
                ):
                    if not self.shutdown_task or self.shutdown_task.done():
                        self.shutdown_task = asyncio.create_task(self.shutdown_if_idle())
                    await asyncio.sleep(1.0)
                    continue
                await asyncio.sleep(2.0 if self.telemetry.snapshot()["active"] else 10.0)
            except asyncio.CancelledError:
                return
            except (httpx.HTTPError, ValueError) as exc:
                LOGGER.debug("Monitor runtime refresh failed: %s", exc)
                await asyncio.sleep(5.0)

    async def stop(self) -> None:
        if self.monitor_task:
            self.monitor_task.cancel()
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        if self.shutdown_task and self.shutdown_task is not asyncio.current_task():
            self.shutdown_task.cancel()
            await asyncio.gather(self.shutdown_task, return_exceptions=True)
        await self.unload_local_models(require_idle=False)
        if self.client:
            await self.client.aclose()
        self.search.close()
        self.usage.close()
        self.store.close()

    @property
    def local_aliases(self) -> tuple[str, str, str]:
        return (
            self.settings.plan_model,
            self.settings.build_model,
            self.settings.vision_model,
        )

    async def unload_local_models(self, *, require_idle: bool = True) -> bool:
        """Unload only LocalCodex aliases and verify the Ollama process list."""
        if not self.client:
            return False
        async with self.inference_lock:
            if require_idle and (self.leases.snapshot()["active_count"] != 0 or getattr(self, "pending_requests", 0)):
                return False
            remaining: set[str] = set(self.local_aliases)
            for attempt in range(3):
                try:
                    ps = await self.client.get(f"{self.settings.ollama_base_url}/api/ps", timeout=5.0)
                    ps.raise_for_status()
                    remaining = {
                        str(item.get("name"))
                        for item in ps.json().get("models", [])
                        if isinstance(item, dict) and item.get("name") in self.local_aliases
                    }
                except (httpx.HTTPError, ValueError):
                    remaining = set(self.local_aliases) if attempt == 0 else remaining
                if not remaining:
                    self.current_model = None
                    return True
                for model in sorted(remaining):
                    try:
                        response = await self.client.post(
                            f"{self.settings.ollama_base_url}/api/generate",
                            json={"model": model, "keep_alive": 0},
                            timeout=15.0,
                        )
                        response.raise_for_status()
                    except httpx.HTTPError as exc:
                        LOGGER.warning("Could not unload %s (attempt %d): %s", model, attempt + 1, exc)
                await asyncio.sleep(0.25)
            LOGGER.warning("LocalCodex model(s) still reported by Ollama: %s", ", ".join(sorted(remaining)))
            return False

    async def shutdown_if_idle(self) -> None:
        """Recheck shared leases, unload aliases, then stop a managed router."""
        await asyncio.sleep(0.2)
        if self.leases.snapshot()["active_count"] != 0:
            return
        if not await self.unload_local_models(require_idle=True):
            return
        if self.leases.snapshot()["active_count"] != 0:
            return
        if self.managed and not self._shutdown_requested:
            self._shutdown_requested = True
            LOGGER.info("No active Codex launcher leases; LocalCodex models unloaded; stopping router")
            os.kill(os.getpid(), signal.SIGTERM)

    async def switch_model(self, target: str) -> None:
        if target not in self.local_aliases:
            raise ValueError("Only managed LocalCodex aliases may be loaded")
        if self.client:
            role = next(
                role
                for role in ("plan", "build", "vision")
                if getattr(self.settings, f"{role}_model") == target
            )
            response = await self.client.post(
                f"{self.settings.ollama_base_url}/api/show", json={"model": target}, timeout=10.0
            )
            response.raise_for_status()
            validate_model_info(target, role, response.json(), self.model_config.profiles()[role].reasoning)
        if self.current_model == target:
            return
        if self.current_model in self.local_aliases and self.client:
            try:
                await self.client.post(
                    f"{self.settings.ollama_base_url}/api/generate",
                    json={"model": self.current_model, "keep_alive": 0},
                    timeout=15.0,
                )
            except httpx.HTTPError as exc:
                LOGGER.warning("Could not unload %s: %s", self.current_model, exc)
        self.current_model = target
