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
    assert client.upload_image(image) == "uploaded.png"
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

