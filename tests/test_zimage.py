from __future__ import annotations

from storyboardctl.comfy.zimage import ZImageContext, ZImageTurboAdapter


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
