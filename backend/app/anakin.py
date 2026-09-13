from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .config import settings


class AnakinError(RuntimeError):
    """A recoverable Anakin API/integration error."""


class AnakinClient:
    """
    Async client for Anakin's REST API.

    Design goals:

    - API key remains backend-only.
    - One HTTP client per Scout session.
    - Explicit per-session operation budgets.
    - Search/scrape/Wire task submissions are tracked separately.
    - Wire discovery and job polling do not consume our task counter.
    """

    def __init__(self) -> None:

        self.client = httpx.AsyncClient(
            base_url=settings.ANAKIN_BASE_URL,
            headers={
                "X-API-Key": settings.ANAKIN_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(
                connect=10.0,
                read=45.0,
                write=20.0,
                pool=10.0,
            ),
            follow_redirects=True,
        )

        self.search_calls = 0
        self.agentic_search_calls = 0
        self.scrape_calls = 0
        self.wire_task_calls = 0

    # =========================================================
    # STATUS
    # =========================================================

    @property
    def enabled(self) -> bool:
        return bool(
            settings.ANAKIN_API_KEY
        )

    @property
    def usage(self) -> dict[str, int]:
        return {
            "search_calls": self.search_calls,
            "agentic_search_calls": self.agentic_search_calls,
            "scrape_calls": self.scrape_calls,
            "wire_task_calls": self.wire_task_calls,
        }

    # =========================================================
    # INTERNAL
    # =========================================================

    def _require_enabled(self) -> None:

        if not self.enabled:
            raise AnakinError(
                "ANAKIN_API_KEY is not configured."
            )

    def _reserve(
        self,
        counter: str,
        limit: int,
        label: str,
    ) -> None:

        self._require_enabled()

        used = getattr(
            self,
            counter,
        )

        if used >= limit:
            raise AnakinError(
                f"Anakin {label} run limit reached: "
                f"{used}/{limit}"
            )

        setattr(
            self,
            counter,
            used + 1,
        )

    @staticmethod
    def _response_error(
        response: httpx.Response,
        operation: str,
    ) -> AnakinError:

        detail = (
            response.text[:800]
            .replace("\n", " ")
        )

        return AnakinError(
            f"Anakin {operation} failed with "
            f"HTTP {response.status_code}: {detail}"
        )

    # =========================================================
    # CLOSE
    # =========================================================

    async def close(self) -> None:

        await self.client.aclose()

    # =========================================================
    # SEARCH
    # =========================================================

    async def search(
        self,
        prompt: str,
        limit: int = 5,
    ) -> Any:

        self._reserve(
            "search_calls",
            settings.ANAKIN_MAX_SEARCH_CALLS,
            "Search",
        )

        prompt = str(prompt).strip()

        if not prompt:
            raise AnakinError(
                "Anakin Search prompt cannot be empty."
            )

        try:

            response = await self.client.post(
                "/search",
                json={
                    "prompt": prompt,
                    "limit": min(
                        max(
                            int(limit),
                            1,
                        ),
                        20,
                    ),
                },
            )

        except httpx.TimeoutException as exc:

            raise AnakinError(
                "Anakin Search timed out."
            ) from exc

        except httpx.RequestError as exc:

            raise AnakinError(
                f"Anakin Search connection failed: {exc}"
            ) from exc

        if response.status_code >= 400:

            raise self._response_error(
                response,
                "Search",
            )

        try:

            return response.json()

        except ValueError as exc:

            raise AnakinError(
                "Anakin Search returned invalid JSON."
            ) from exc

    # =========================================================
    # AGENTIC SEARCH
    # =========================================================

    async def agentic_search(self, prompt: str, *, timeout_seconds: int = 240) -> Any:
        """Run one Anakin Agentic Search job and poll it to completion.

        Agentic Search is Scout's primary live-web research path.  It plans,
        searches, reads and synthesizes sources server-side, and can return
        generatedJson/structured_data which is especially useful for candidate
        extraction.  We keep a separate per-session budget so one Scout run
        cannot accidentally create a research storm.
        """
        self._reserve("agentic_search_calls", settings.ANAKIN_MAX_AGENTIC_SEARCH_CALLS, "Agentic Search")
        prompt = str(prompt).strip()
        if not prompt:
            raise AnakinError("Anakin Agentic Search prompt cannot be empty.")
        try:
            response = await self.client.post("/agentic-search", json={"prompt": prompt}, timeout=httpx.Timeout(timeout_seconds))
        except httpx.TimeoutException as exc:
            raise AnakinError("Anakin Agentic Search submission timed out.") from exc
        except httpx.RequestError as exc:
            raise AnakinError(f"Anakin Agentic Search connection failed: {exc}") from exc
        if response.status_code >= 400:
            raise self._response_error(response, "Agentic Search")
        try:
            data = response.json()
        except ValueError as exc:
            raise AnakinError("Anakin Agentic Search returned invalid JSON.") from exc
        job_id = data.get("id") or data.get("jobId") or data.get("job_id")
        if not job_id:
            return data
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(2.5)
            try:
                poll = await self.client.get(f"/agentic-search/{job_id}", timeout=httpx.Timeout(30.0))
            except (httpx.TimeoutException, httpx.RequestError):
                continue
            if poll.status_code >= 400:
                raise self._response_error(poll, "Agentic Search poll")
            try:
                result = poll.json()
            except ValueError:
                continue
            status = str(result.get("status") or result.get("state") or "").lower()
            if status in {"completed", "complete", "succeeded", "success", "done"}:
                return result
            if status in {"failed", "error", "cancelled", "canceled"}:
                raise AnakinError(f"Anakin Agentic Search job {status}.")
            # Some API responses return generatedJson directly without a status.
            if result.get("generatedJson") is not None or result.get("generated_json") is not None:
                return result
        raise AnakinError("Anakin Agentic Search timed out while polling.")

    # =========================================================
    # URL SCRAPE
    # =========================================================

    async def scrape(
        self,
        url: str,
        *,
        use_browser: bool = False,
        generate_json: bool = False,
    ) -> Any:

        self._reserve(
            "scrape_calls",
            settings.ANAKIN_MAX_SCRAPE_CALLS,
            "URL scrape",
        )

        url = str(url).strip()

        if not url:
            raise AnakinError(
                "Anakin scrape URL cannot be empty."
            )

        try:

            response = await self.client.post(
                "/url-scraper/scrape",
                json={
                    "url": url,
                    "useBrowser": bool(
                        use_browser
                    ),
                    "generateJson": bool(
                        generate_json
                    ),
                },
            )

        except httpx.TimeoutException as exc:

            raise AnakinError(
                "Anakin URL scrape timed out."
            ) from exc

        except httpx.RequestError as exc:

            raise AnakinError(
                f"Anakin URL scrape connection failed: {exc}"
            ) from exc

        if response.status_code >= 400:

            raise self._response_error(
                response,
                "URL scrape",
            )

        try:

            return response.json()

        except ValueError as exc:

            raise AnakinError(
                "Anakin URL scrape returned invalid JSON."
            ) from exc

    # =========================================================
    # WIRE DISCOVERY
    # =========================================================

    async def resolve_action(
        self,
        query: str,
        *,
        catalog: str | None = None,
        category: str | None = None,
    ) -> Any:

        self._require_enabled()

        params: dict[str, str] = {
            "q": str(query).strip(),
        }

        if catalog:
            params["catalog"] = catalog

        if category:
            params["category"] = category

        try:

            response = await self.client.get(
                "/wire/resolve",
                params=params,
            )

        except httpx.TimeoutException as exc:

            raise AnakinError(
                "Anakin Wire discovery timed out."
            ) from exc

        except httpx.RequestError as exc:

            raise AnakinError(
                f"Anakin Wire discovery connection failed: {exc}"
            ) from exc

        if response.status_code >= 400:

            raise self._response_error(
                response,
                "Wire discovery",
            )

        try:

            return response.json()

        except ValueError as exc:

            raise AnakinError(
                "Anakin Wire discovery returned invalid JSON."
            ) from exc

    # =========================================================
    # WIRE TASK
    # =========================================================

    async def wire_task(
        self,
        action_id: str,
        params: dict[str, Any],
        credential_id: str | None = None,
    ) -> Any:

        self._reserve(
            "wire_task_calls",
            settings.ANAKIN_MAX_WIRE_TASK_CALLS,
            "Wire task",
        )

        action_id = str(
            action_id
        ).strip()

        if not action_id:
            raise AnakinError(
                "Anakin Wire action_id cannot be empty."
            )

        payload: dict[str, Any] = {
            "action_id": action_id,
            "params": params,
        }

        if credential_id:
            payload[
                "credential_id"
            ] = credential_id

        try:

            response = await self.client.post(
                "/wire/task",
                json=payload,
            )

        except httpx.TimeoutException as exc:

            raise AnakinError(
                "Anakin Wire task timed out."
            ) from exc

        except httpx.RequestError as exc:

            raise AnakinError(
                f"Anakin Wire task connection failed: {exc}"
            ) from exc

        if response.status_code >= 400:

            raise self._response_error(
                response,
                "Wire task",
            )

        try:

            return response.json()

        except ValueError as exc:

            raise AnakinError(
                "Anakin Wire task returned invalid JSON."
            ) from exc

    # =========================================================
    # WIRE JOB POLLING
    # =========================================================

    async def wire_job(
        self,
        job_id: str,
        *,
        timeout_seconds: int = 90,
    ) -> Any:

        self._require_enabled()

        started = time.monotonic()

        delay = 0.8

        terminal_states = {
            "completed",
            "complete",
            "success",
            "succeeded",
            "failed",
            "error",
            "cancelled",
            "canceled",
        }

        while True:

            try:

                response = await self.client.get(
                    f"/wire/jobs/{job_id}"
                )

            except httpx.TimeoutException as exc:

                raise AnakinError(
                    "Anakin Wire job polling timed out."
                ) from exc

            except httpx.RequestError as exc:

                raise AnakinError(
                    f"Anakin Wire job polling failed: {exc}"
                ) from exc

            if response.status_code >= 400:

                raise self._response_error(
                    response,
                    "Wire job polling",
                )

            try:

                data = response.json()

            except ValueError as exc:

                raise AnakinError(
                    "Anakin Wire job returned invalid JSON."
                ) from exc

            status = str(
                data.get(
                    "status",
                    "",
                )
            ).lower()

            if status in terminal_states:
                return data

            if (
                time.monotonic()
                - started
                >= timeout_seconds
            ):

                raise AnakinError(
                    f"Anakin Wire job timed out "
                    f"after {timeout_seconds}s: {job_id}"
                )

            await asyncio.sleep(
                delay
            )

            delay = min(
                delay * 1.35,
                3.0,
            )