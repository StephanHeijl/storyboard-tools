from __future__ import annotations

from storyboardctl.comfy.adapters import AdapterRegistry, WorkflowContext
from storyboardctl.comfy.h3 import H3Adapter, H3Models
from storyboardctl.models import DialogueCueSpec, RenderMode


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


def test_h3_adapter_declares_preflight_requirements() -> None:
    requirements = H3Adapter().requirements()
    assert "MiniMaxH3ImageToVideo" in requirements["nodes"]
    assert H3Models().fl2va_model in requirements["models"]
    assert H3Models().video_vae in requirements["models"]


def test_h3_adapter_translates_negative_prompt_into_explicit_instructions() -> None:
    workflow = H3Adapter().build_workflow(
        WorkflowContext(
            prompt="One small robot walks through a meadow.",
            negative_prompt="text, duplicate robot, flicker",
            render_mode=RenderMode.text_to_video,
            width=736,
            height=416,
            frames=56,
            steps=8,
            seed=1,
            output_key="negative-test",
        )
    )

    effective = workflow["5"]["inputs"]["prompt"]
    assert effective.startswith("One small robot walks through a meadow.")
    assert "AVOID: text, duplicate robot, flicker" in effective


def test_h3_adapter_compiles_structured_dialogue_into_native_prompt_notation() -> None:
    workflow = H3Adapter().build_workflow(
        WorkflowContext(
            prompt="Medium shot of Alice waiting beside the gate.",
            negative_prompt="unrequested speech, captions",
            render_mode=RenderMode.text_to_video,
            width=736,
            height=416,
            frames=107,
            steps=8,
            seed=1,
            output_key="dialogue-test",
            dialogue=(
                DialogueCueSpec(
                    speaker="Alice",
                    speaker_id="S1",
                    text="We need to leave before sunset.",
                    language="English",
                    start_seconds=1.2,
                    end_seconds=3.4,
                ),
            ),
        )
    )

    effective = workflow["5"]["inputs"]["prompt"]
    assert effective.startswith("integrated_multimodal_description: [Shot 1]")
    assert "At 00:01.200, Alice (S1) says: <d>[English] We need to leave before sunset.</d>" in effective
    assert "The dialogue ends by 00:03.400" in effective
    assert "overall_soundscape:" in effective
    assert "non_diegetic_music: N/A" in effective
    assert "AVOID: unrequested speech, captions" in effective


def test_h3_adapter_leaves_prompt_without_dialogue_backward_compatible() -> None:
    workflow = H3Adapter().build_workflow(
        WorkflowContext(
            prompt="A silent meadow.",
            render_mode=RenderMode.text_to_video,
            width=736,
            height=416,
            frames=107,
            steps=8,
            seed=1,
            output_key="silent-test",
        )
    )
    assert workflow["5"]["inputs"]["prompt"] == "A silent meadow."
