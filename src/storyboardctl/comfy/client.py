from __future__ import annotations

import mimetypes
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from storyboardctl.errors import ExternalServiceFailure

VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv")


def _all_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {item for child in value.values() for item in _all_strings(child)}
    if isinstance(value, list):
        return {item for child in value for item in _all_strings(child)}
    return set()


@dataclass(frozen=True)
class ComfySettings:
    base_url: str = "http://127.0.0.1:8188"
    bearer_token: str | None = field(default=None, repr=False)
    request_timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> ComfySettings:
        return cls(
            base_url=os.environ.get("STORYBOARDCTL_COMFY_URL", "http://127.0.0.1:8188"),
            bearer_token=os.environ.get("STORYBOARDCTL_COMFY_TOKEN"),
        )


def _video_outputs(value: Any) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if isinstance(value, dict):
        filename = value.get("filename")
        if isinstance(filename, str) and filename.lower().endswith(VIDEO_EXTENSIONS):
            matches.append(value)
        for child in value.values():
            matches.extend(_video_outputs(child))
    elif isinstance(value, list):
        for child in value:
            matches.extend(_video_outputs(child))
    return matches


def discover_video_output(value: Any) -> dict[str, Any]:
    matches = _video_outputs(value)
    if not matches:
        raise ExternalServiceFailure("ComfyUI completed without a video output")
    return matches[0]


class ComfyClient:
    def __init__(
        self,
        settings: ComfySettings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        headers = {"Authorization": f"Bearer {settings.bearer_token}"} if settings.bearer_token else {}
        self.http = http_client or httpx.Client(
            base_url=settings.base_url.rstrip("/"),
            headers=headers,
            timeout=settings.request_timeout_seconds,
        )
        if http_client is not None:
            self.http.headers.update(headers)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.settings.base_url.rstrip('/')}{path}"
        try:
            response = self.http.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as error:
            raise ExternalServiceFailure(f"ComfyUI request failed: {method} {path}: {error}") from error

    def ping(self) -> dict[str, Any]:
        payload = self._request("GET", "/system_stats").json()
        system = payload.get("system", {})
        return {
            "ok": True,
            "base_url": self.settings.base_url.rstrip("/"),
            "comfyui_version": system.get("comfyui_version"),
            "python_version": system.get("python_version"),
            "devices": payload.get("devices", []),
        }

    def queue_status(self) -> dict[str, Any]:
        payload = self._request("GET", "/queue").json()
        running = payload.get("queue_running", [])
        pending = payload.get("queue_pending", [])
        return {
            "running": len(running) if isinstance(running, list) else 0,
            "pending": len(pending) if isinstance(pending, list) else 0,
            "queue_running": running,
            "queue_pending": pending,
        }

    def preflight(self, *, nodes: tuple[str, ...], models: tuple[str, ...]) -> dict[str, Any]:
        payload = self._request("GET", "/object_info").json()
        available_nodes = set(payload) if isinstance(payload, dict) else set()
        available_values = _all_strings(payload)
        missing_nodes = sorted(set(nodes) - available_nodes)
        missing_models = sorted(set(models) - available_values)
        return {
            "ok": not missing_nodes and not missing_models,
            "required_nodes": list(nodes),
            "required_models": list(models),
            "missing_nodes": missing_nodes,
            "missing_models": missing_models,
        }

    def upload_image(self, image_path: Path, *, remote_name: str | None = None) -> str:
        upload_name = remote_name or image_path.name
        media_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        with image_path.open("rb") as stream:
            response = self._request(
                "POST",
                "/upload/image",
                files={"image": (upload_name, stream, media_type)},
                data={"overwrite": "false", "subfolder": "storyboardctl"},
            )
        payload = response.json()
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise ExternalServiceFailure("ComfyUI image upload returned no filename")
        subfolder = payload.get("subfolder")
        return f"{subfolder}/{name}" if isinstance(subfolder, str) and subfolder else name

    def enqueue(self, workflow: dict[str, dict[str, Any]]) -> str:
        payload = self._request(
            "POST",
            "/prompt",
            json={"prompt": workflow, "client_id": str(uuid.uuid4())},
        ).json()
        if payload.get("error"):
            raise ExternalServiceFailure(f"ComfyUI rejected workflow: {payload['error']}")
        prompt_id = payload.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ExternalServiceFailure("ComfyUI prompt submission returned no prompt_id")
        return prompt_id

    def wait_for_completion(
        self,
        prompt_id: str,
        *,
        timeout_seconds: float = 1800,
        poll_seconds: float = 3,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() <= deadline:
            payload = self._request("GET", f"/history/{prompt_id}").json()
            entry = payload.get(prompt_id)
            if isinstance(entry, dict):
                status = entry.get("status")
                status = status if isinstance(status, dict) else {}
                status_text = str(status.get("status_str", ""))
                messages = status.get("messages", [])
                if "error" in status_text.lower() or "execution_error" in str(messages):
                    raise ExternalServiceFailure(f"ComfyUI execution failed: {messages}")
                if status.get("completed") or entry.get("outputs"):
                    return entry
            if poll_seconds:
                time.sleep(poll_seconds)
        raise ExternalServiceFailure(f"timed out waiting for ComfyUI prompt {prompt_id}")

    def download(self, history_entry: dict[str, Any], destination: Path) -> Path:
        item = discover_video_output(history_entry.get("outputs", {}))
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = destination.with_suffix(destination.suffix + ".part")
        url = f"{self.settings.base_url.rstrip('/')}/view"
        try:
            with self.http.stream(
                "GET",
                url,
                params={
                    "filename": item["filename"],
                    "subfolder": item.get("subfolder", ""),
                    "type": item.get("type", "output"),
                },
            ) as response:
                response.raise_for_status()
                with staged.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            staged.replace(destination)
        except (OSError, httpx.HTTPError) as error:
            staged.unlink(missing_ok=True)
            raise ExternalServiceFailure(f"ComfyUI download failed: {error}") from error
        return destination
