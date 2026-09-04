from __future__ import annotations

from storyboardctl.comfy.zimage import ZImageContext, ZImageModels, ZImageTurboAdapter


def test_zimage_workflow_matches_turbo_defaults() -> None:
    adapter = ZImageTurboAdapter()
    workflow = adapter.build_workflow(ZImageContext("A ferris wheel", 1344, 768, 8, 42, "shot-10"))

    assert workflow["2"]["inputs"]["type"] == "lumina2"
    assert workflow["4"]["inputs"]["shift"] == 3.0
    assert workflow["8"]["inputs"] | {} == workflow["8"]["inputs"]
    assert workflow["8"]["inputs"]["cfg"] == 1.0
    assert workflow["8"]["inputs"]["sampler_name"] == "res_multistep"
    assert workflow["8"]["inputs"]["scheduler"] == "simple"
    assert workflow["10"]["class_type"] == "SaveImage"
    assert set(adapter.requirements()["models"]) == {
        "z_image_turbo_int8_convrot.safetensors",
        "qwen_3_4b_fp8_mixed.safetensors",
        "ae.safetensors",
    }


def test_zimage_model_names_can_come_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("STORYBOARDCTL_ZIMAGE_MODEL", "z_image_turbo_bf16.safetensors")
    monkeypatch.setenv("STORYBOARDCTL_ZIMAGE_TEXT_ENCODER", "qwen_3_4b.safetensors")
    adapter = ZImageTurboAdapter()
    assert adapter.models == ZImageModels(
        diffusion_model="z_image_turbo_bf16.safetensors",
        text_encoder="qwen_3_4b.safetensors",
    )
