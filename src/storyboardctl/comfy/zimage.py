from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ZImageContext:
    prompt: str
    width: int
    height: int
    steps: int
    seed: int
    output_key: str


@dataclass(frozen=True)
class ZImageModels:
    diffusion_model: str = "z_image_turbo_int8_convrot.safetensors"
    text_encoder: str = "qwen_3_4b_fp8_mixed.safetensors"
    vae: str = "ae.safetensors"

    @classmethod
    def from_environment(cls) -> ZImageModels:
        return cls(
            diffusion_model=os.environ.get("STORYBOARDCTL_ZIMAGE_MODEL", cls.diffusion_model),
            text_encoder=os.environ.get("STORYBOARDCTL_ZIMAGE_TEXT_ENCODER", cls.text_encoder),
            vae=os.environ.get("STORYBOARDCTL_ZIMAGE_VAE", cls.vae),
        )


class ZImageTurboAdapter:
    """Official ComfyUI Z-Image Turbo text-to-image graph."""

    name = "z-image-turbo"

    def __init__(self, models: ZImageModels | None = None) -> None:
        self.models = models or ZImageModels.from_environment()

    def requirements(self) -> dict[str, tuple[str, ...]]:
        return {
            "nodes": (
                "UNETLoader",
                "CLIPLoader",
                "VAELoader",
                "ModelSamplingAuraFlow",
                "CLIPTextEncode",
                "ConditioningZeroOut",
                "EmptySD3LatentImage",
                "KSampler",
                "VAEDecode",
                "SaveImage",
            ),
            "models": (self.models.diffusion_model, self.models.text_encoder, self.models.vae),
        }

    def build_workflow(self, context: ZImageContext) -> dict[str, dict[str, Any]]:
        return {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": self.models.diffusion_model, "weight_dtype": "default"},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": self.models.text_encoder, "type": "lumina2", "device": "default"},
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": self.models.vae}},
            "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": context.prompt}},
            "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
            "7": {
                "class_type": "EmptySD3LatentImage",
                "inputs": {"width": context.width, "height": context.height, "batch_size": 1},
            },
            "8": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "latent_image": ["7", 0],
                    "seed": context.seed,
                    "control_after_generate": "fixed",
                    "steps": context.steps,
                    "cfg": 1.0,
                    "sampler_name": "res_multistep",
                    "scheduler": "simple",
                    "denoise": 1.0,
                },
            },
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {
                "class_type": "SaveImage",
                "inputs": {"images": ["9", 0], "filename_prefix": f"storyboardctl/{context.output_key}"},
            },
        }
