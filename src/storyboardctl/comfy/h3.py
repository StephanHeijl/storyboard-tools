from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from storyboardctl.comfy.adapters import WorkflowContext
from storyboardctl.errors import Conflict
from storyboardctl.models import RenderMode


@dataclass(frozen=True)
class H3Models:
    fl2va_model: str = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    ref2va_model: str = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    clip_model: str = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    video_vae: str = "minimax_h3_video_vae_fp16.safetensors"
    audio_vae: str = "minimax_h3_audio_vae_fp32.safetensors"
    ref_image_size: str = "max"
    output_prefix: str = "video/storyboardctl"


class H3Adapter:
    name = "h3"

    def __init__(self, models: H3Models | None = None) -> None:
        self.models = models or H3Models()

    def scrub_workflow(
        self, workflow: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        return workflow

    def build_workflow(self, context: WorkflowContext) -> dict[str, dict[str, Any]]:
        if context.render_mode is RenderMode.custom:
            raise Conflict("the H3 adapter cannot build a custom render mode")
        model = (
            self.models.ref2va_model
            if context.render_mode is RenderMode.reference_to_video
            else self.models.fl2va_model
        )
        workflow: dict[str, dict[str, Any]] = {
            "1": {
                "inputs": {"unet_name": model, "weight_dtype": "default"},
                "class_type": "UNETLoader",
            },
            "2": {
                "inputs": {
                    "clip_name": self.models.clip_model,
                    "type": "minimax",
                    "device": "default",
                },
                "class_type": "CLIPLoader",
            },
            "3": {"inputs": {"vae_name": self.models.video_vae}, "class_type": "VAELoader"},
            "4": {"inputs": {"vae_name": self.models.audio_vae}, "class_type": "VAELoader"},
            "6": {"inputs": {"sampler_name": "res_multistep"}, "class_type": "KSamplerSelect"},
            "7": {"inputs": {"noise_seed": context.seed}, "class_type": "RandomNoise"},
            "8": {
                "inputs": {
                    "scheduler": "simple",
                    "steps": context.steps,
                    "denoise": 1.0,
                    "model": ["1", 0],
                },
                "class_type": "BasicScheduler",
            },
            "9": {
                "inputs": {"model": ["1", 0], "conditioning": ["5", 0]},
                "class_type": "BasicGuider",
            },
            "11": {
                "inputs": {
                    "noise": ["7", 0],
                    "guider": ["9", 0],
                    "sampler": ["6", 0],
                    "sigmas": ["8", 0],
                    "latent_image": ["5", 1],
                },
                "class_type": "SamplerCustomAdvanced",
            },
            "12": {"inputs": {"samples": ["11", 0], "vae": ["3", 0]}, "class_type": "VAEDecode"},
            "13": {"inputs": {"samples": ["11", 0], "vae": ["4", 0]}, "class_type": "VAEDecodeAudio"},
            "14": {
                "inputs": {"fps": 24.0, "bit_depth": 8, "images": ["12", 0], "audio": ["13", 0]},
                "class_type": "CreateVideo",
            },
            "15": {
                "inputs": {
                    "filename_prefix": f"{self.models.output_prefix}/{context.output_key}",
                    "format": "auto",
                    "codec": "auto",
                    "video": ["14", 0],
                },
                "class_type": "SaveVideo",
            },
        }
        conditioning: dict[str, Any] = {
            "clip": ["2", 0],
            "vae": ["3", 0],
            "prompt": context.prompt,
            "width": context.width,
            "height": context.height,
            "length": context.frames,
        }
        if context.render_mode is RenderMode.reference_to_video:
            conditioning.update({"audio_vae": ["4", 0], "ref_image_size": self.models.ref_image_size})
            for index, filename in enumerate(context.reference_images):
                node_id = str(20 + index)
                workflow[node_id] = {"inputs": {"image": filename}, "class_type": "LoadImage"}
                conditioning[f"ref_images.ref_image_{index}"] = [node_id, 0]
            workflow["5"] = {
                "inputs": conditioning,
                "class_type": "MiniMaxH3ReferenceToVideo",
            }
        else:
            next_node = 20
            if context.first_frame:
                workflow[str(next_node)] = {
                    "inputs": {"image": context.first_frame},
                    "class_type": "LoadImage",
                }
                conditioning["first_frame"] = [str(next_node), 0]
                next_node += 1
            if context.last_frame:
                workflow[str(next_node)] = {
                    "inputs": {"image": context.last_frame},
                    "class_type": "LoadImage",
                }
                conditioning["last_frame"] = [str(next_node), 0]
            workflow["5"] = {"inputs": conditioning, "class_type": "MiniMaxH3ImageToVideo"}
        return workflow
