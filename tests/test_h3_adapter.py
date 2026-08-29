from __future__ import annotations

from storyboardctl.comfy.adapters import AdapterRegistry, WorkflowContext
from storyboardctl.comfy.h3 import H3Adapter, H3Models
from storyboardctl.models import RenderMode


def test_registry_and_h3_reference_workflow_use_structured_context() -> None:
    registry = AdapterRegistry()
    adapter = H3Adapter(H3Models(output_prefix="video/example"))
    registry.register(adapter)
    assert registry.get("h3") is adapter

    workflow = adapter.build_workflow(
        WorkflowContext(
            prompt="A fictional courier lands.",
            render_mode=RenderMode.reference_to_video,
            width=736,
            height=416,
            frames=107,
            steps=20,
            seed=123,
            output_key="shot_0010_take_001",
            reference_images=("courier.png", "moon.png"),
        )
    )
    assert workflow["1"]["inputs"]["unet_name"] == H3Models().ref2va_model
    assert workflow["5"]["class_type"] == "MiniMaxH3ReferenceToVideo"
    assert workflow["5"]["inputs"]["ref_images.ref_image_0"] == ["20", 0]
    assert workflow["5"]["inputs"]["ref_images.ref_image_1"] == ["21", 0]
    assert workflow["7"]["inputs"]["noise_seed"] == 123
    assert workflow["15"]["inputs"]["filename_prefix"].endswith("shot_0010_take_001")


def test_h3_image_workflow_connects_first_and_last_frames() -> None:
    workflow = H3Adapter().build_workflow(
        WorkflowContext(
            prompt="Connect two frames.",
            render_mode=RenderMode.image_to_video,
            width=1344,
            height=768,
            frames=107,
            steps=8,
            seed=9,
            output_key="connection",
            first_frame="first.png",
            last_frame="last.png",
        )
    )
    assert workflow["5"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert workflow["5"]["inputs"]["first_frame"] == ["20", 0]
    assert workflow["5"]["inputs"]["last_frame"] == ["21", 0]


def test_h3_adapter_prepares_replay_with_new_runtime_values() -> None:
    adapter = H3Adapter(H3Models(output_prefix="video/example"))
    original_context = WorkflowContext(
        prompt="Original prompt",
        render_mode=RenderMode.image_to_video,
        width=1344,
        height=768,
        frames=107,
        steps=8,
        seed=9,
        output_key="shot_0010_take_001",
    )
    workflow = adapter.build_workflow(original_context)
    replay_context = WorkflowContext(
        prompt="Original prompt",
        render_mode=RenderMode.image_to_video,
        width=1344,
        height=768,
        frames=107,
        steps=8,
        seed=10,
        output_key="shot_0010_retry_002",
    )

    replay = adapter.prepare_replay(workflow, replay_context)

    assert replay["7"]["inputs"]["noise_seed"] == 10
    assert replay["15"]["inputs"]["filename_prefix"] == "video/example/shot_0010_retry_002"
