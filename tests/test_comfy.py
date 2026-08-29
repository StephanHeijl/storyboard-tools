from __future__ import annotations

import json

import httpx
import pytest

from storyboardctl.comfy.client import ComfyClient, ComfySettings, discover_video_output
from storyboardctl.errors import ExternalServiceFailure


def test_upload_enqueue_poll_and_download(tmp_path) -> None:
    requests: list[httpx.Request] = []
    history_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal history_calls
        requests.append(request)
        if request.url.path == "/upload/image":
            assert request.headers["content-type"].startswith("multipart/form-data")
            return httpx.Response(200, json={"name": "uploaded.png"})
        if request.url.path == "/prompt":
            assert json.loads(request.content)["prompt"]["1"]["class_type"] == "SaveVideo"
            return httpx.Response(200, json={"prompt_id": "job-1"})
        if request.url.path == "/history/job-1":
            history_calls += 1
            if history_calls == 1:
                return httpx.Response(200, json={"job-1": {"status": {"status_str": "running"}}})
            return httpx.Response(
                200,
                json={
                    "job-1": {
                        "status": {"completed": True},
                        "outputs": {"9": {"video": [{"filename": "clip.mp4", "subfolder": "x"}]}},
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"video")
        raise AssertionError(request.url)

    settings = ComfySettings(base_url="http://comfy.test", bearer_token="super-secret")
    assert "super-secret" not in repr(settings)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ComfyClient(settings, http_client=http)
    image = tmp_path / "frame.png"
    image.write_bytes(b"png")
    assert client.upload_image(image, remote_name="abc123-frame.png") == "uploaded.png"
    upload_body = requests[0].content.decode("utf-8", errors="ignore")
    assert 'filename="abc123-frame.png"' in upload_body
    assert "false" in upload_body
    prompt_id = client.enqueue({"1": {"class_type": "SaveVideo", "inputs": {}}})
    result = client.wait_for_completion(prompt_id, poll_seconds=0, timeout_seconds=1)
    target = tmp_path / "clip.mp4"
    client.download(result, target)
    assert target.read_bytes() == b"video"
    assert all(request.headers["authorization"] == "Bearer super-secret" for request in requests)


def test_execution_errors_and_missing_outputs_are_clear() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "bad": {
                    "status": {
                        "status_str": "error",
                        "messages": [["execution_error", {"message": "model missing"}]],
                    }
                }
            },
        )

    client = ComfyClient(
        ComfySettings(base_url="http://comfy.test"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ExternalServiceFailure, match="model missing"):
        client.wait_for_completion("bad", poll_seconds=0, timeout_seconds=1)
    with pytest.raises(ExternalServiceFailure, match="video output"):
        client.download({"outputs": {}}, __import__("pathlib").Path("unused.mp4"))


def test_video_discovery_is_recursive() -> None:
    output = {"a": [{"nested": {"filename": "ignore.png"}}, {"filename": "movie.webm"}]}
    assert discover_video_output(output)["filename"] == "movie.webm"


def test_download_streams_to_a_staged_file(tmp_path) -> None:
    client = ComfyClient(
        ComfySettings(base_url="http://comfy.test"),
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"large-video"))
        ),
    )
    target = tmp_path / "result.mp4"
    client.download({"outputs": {"x": {"filename": "result.mp4"}}}, target)
    assert target.read_bytes() == b"large-video"
    assert not target.with_suffix(".mp4.part").exists()


def test_live_diagnostics_report_ping_queue_and_preflight() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "1.2.3"}, "devices": [{"name": "gpu"}]})
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [[1, "job-running"]], "queue_pending": []})
        if request.url.path == "/object_info":
            return httpx.Response(
                200,
                json={
                    "UNETLoader": {"input": {"required": {"unet_name": [["model.safetensors"]]}}},
                    "SaveVideo": {"input": {"required": {}}},
                },
            )
        raise AssertionError(request.url)

    client = ComfyClient(
        ComfySettings(base_url="http://comfy.test"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.ping()["comfyui_version"] == "1.2.3"
    assert client.queue_status() == {
        "running": 1,
        "pending": 0,
        "queue_running": [[1, "job-running"]],
        "queue_pending": [],
    }
    assert client.preflight(nodes=("UNETLoader", "SaveVideo"), models=("model.safetensors",))["ok"] is True
    missing = client.preflight(nodes=("MissingNode",), models=("missing.safetensors",))
    assert missing["ok"] is False
    assert missing["missing_nodes"] == ["MissingNode"]
    assert missing["missing_models"] == ["missing.safetensors"]
