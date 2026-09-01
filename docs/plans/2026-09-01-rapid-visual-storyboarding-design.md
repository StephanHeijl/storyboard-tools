# Rapid Visual Storyboarding Design

## Goal

Add a low-cost storyboard pass that renders one Z-Image-Turbo still per active shot, turns those stills into a lightly animated preview video, and burns deterministic subtitles from structured dialogue cues. The same dialogue source must compile into MiniMax H3's native `(Sx)` and `<d>[Language]…</d>` prompt notation for full video renders.

## Data model

`ShotSpec.dialogue` becomes a list of immutable `DialogueCueSpec` values with `speaker`, `speaker_id`, `text`, `language`, `start_seconds`, and `end_seconds`. Cues must fit inside the shot, may not overlap, and retain exact user text. Revision content hashes include dialogue, so changing a line or timing invalidates the selected full render and rapid-board frame for that revision.

Migration 4 adds `dialogue_json` to shot revisions plus two provenance tables. `board_frames` records each Z-Image attempt, workflow snapshot, seed, prompt, output path/hash, Comfy prompt ID, state, and version/position snapshot. `board_previews` records monotonic preview compilations pinned to one storyboard snapshot, their manifest, output, settings, duration, and state. Binary media remains project-relative.

## Generation paths

The H3 adapter leaves prompts without dialogue unchanged. With dialogue, a deterministic compiler produces the official three-section base prompt and places each cue chronologically inside `integrated_multimodal_description`, using stable speaker IDs and verbatim `<d>[Language]…</d>` blocks. Timing is an instruction to H3, not a guarantee; the structured database timing remains authoritative.

The Z-Image adapter follows ComfyUI's official INT8 Turbo graph: UNET loader, Lumina2 Qwen encoder, Flux VAE, zeroed negative conditioning, AuraFlow shift 3, eight-step `res_multistep` sampling, VAE decode, and `SaveImage`. Defaults are configurable but preflight requires the configured nodes and model filenames. The initial server is expected to report the three Z-Image models missing until installed.

## Preview assembly

Each shot uses its latest completed board frame for the selected revision. FFmpeg creates a normalized clip with a restrained alternating pan/zoom (Ken Burns-style), then burns each cue over its shot-local interval using generated ASS subtitles. Text is escaped and wrapped deterministically; dialogue never enters the generated image. Clips are concatenated in storyboard order with no audio. The preview duration equals the sum of shot durations.

Agent-facing commands are grouped under `storyboardctl board`: `preflight`, `frame`, `render`, `list`, `build`, and `create`. `create` is the minimal end-to-end operation: preflight, render missing frames, and build the preview. Every command remains JSON-first and non-interactive.

## Safety and testing

Tests cover schema validation, migrations, H3 compilation, exact Z-Image workflow structure, image-output discovery/download, monotonic frame attempts, preview readiness, subtitle escaping/timing, FFmpeg assembly, and CLI output. Comfy transport is faked in tests; FFmpeg integration uses generated stills. Output paths are unique and never overwritten. Database writes do not span network or FFmpeg work.
