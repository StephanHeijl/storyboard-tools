# Rapid Visual Storyboarding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist structured dialogue, compile it for H3, render rapid Z-Image storyboard frames, and assemble lightly animated subtitled preview videos.

**Architecture:** Extend immutable shot revisions with dialogue JSON, add separate board-frame and board-preview provenance tables, and keep Comfy/FFmpeg work outside database transactions. A Z-Image adapter builds ComfyUI's official Turbo graph; a preview assembler converts approved revision-aligned stills and dialogue cues into a deterministic silent MP4.

**Tech Stack:** Python 3.11, Pydantic 2, SQLite migrations, Typer, httpx, ComfyUI HTTP API, FFmpeg/FFprobe, uv, pytest.

---

### Task 1: Structured dialogue and H3 prompt compilation

**Files:**
- Modify: `src/storyboardctl/models.py`
- Create: `src/storyboardctl/prompts.py`
- Modify: `src/storyboardctl/comfy/adapters.py`
- Modify: `src/storyboardctl/comfy/h3.py`
- Test: `tests/test_models.py`
- Test: `tests/test_h3_adapter.py`

1. Write failing tests for cue validation, exact round-trip, and native H3 dialogue notation.
2. Run `uv run pytest -q tests/test_models.py tests/test_h3_adapter.py` and confirm the new tests fail.
3. Add `DialogueCueSpec`, shot-level timing/overlap validation, `WorkflowContext.dialogue`, and deterministic prompt compilation.
4. Run the focused tests and confirm they pass.
5. Commit `feat: add structured H3 dialogue cues`.

### Task 2: Persist dialogue and rapid-board provenance

**Files:**
- Modify: `src/storyboardctl/migrations.py`
- Modify: `src/storyboardctl/service.py`
- Test: `tests/test_database.py`
- Create: `tests/test_board_service.py`

1. Write failing migration and service tests for dialogue round-trip, monotonic board attempts, immutable output paths, completion hashes, discovery, and snapshot compatibility.
2. Run the focused tests and confirm missing migration/table/service failures.
3. Add migration 4 and minimal service methods for planning, transitioning, completing, and listing board frames and previews.
4. Run focused tests and commit `feat: persist rapid storyboard attempts`.

### Task 3: Z-Image Turbo adapter and image transport

**Files:**
- Create: `src/storyboardctl/comfy/zimage.py`
- Modify: `src/storyboardctl/comfy/client.py`
- Modify: `src/storyboardctl/comfy/__init__.py`
- Create: `tests/test_zimage_adapter.py`
- Modify: `tests/test_comfy.py`

1. Write failing tests for the official INT8 graph, requirements, image discovery, and atomic image download.
2. Run the focused tests and confirm failures.
3. Implement `ZImageModels`, `ZImageTurboAdapter`, `discover_image_output`, and `download_image`.
4. Run focused tests and commit `feat: render board frames with Z-Image Turbo`.

### Task 4: Board frame runner and CLI

**Files:**
- Create: `src/storyboardctl/boarding.py`
- Modify: `src/storyboardctl/cli.py`
- Create: `tests/test_boarding.py`
- Modify: `tests/test_cli.py`

1. Write failing tests for single-shot rendering, version rendering, missing-model preflight, retries, and JSON CLI operations.
2. Run focused tests and confirm failures.
3. Implement the runner plus `board preflight`, `board frame`, `board render`, and `board list`.
4. Run focused tests and commit `feat: add rapid board rendering commands`.

### Task 5: Animated subtitled preview assembly

**Files:**
- Create: `src/storyboardctl/preview.py`
- Modify: `src/storyboardctl/service.py`
- Modify: `src/storyboardctl/cli.py`
- Create: `tests/test_preview.py`
- Modify: `tests/test_ffmpeg.py`
- Modify: `tests/test_cli.py`

1. Write failing tests for cue-to-ASS timing, escaping, alternating pan direction, missing-frame readiness, monotonic manifests, FFmpeg output duration, and CLI commands.
2. Run focused tests and confirm failures.
3. Implement deterministic ASS generation, staged FFmpeg clips/concat, `board build`, and end-to-end `board create`.
4. Run focused tests and commit `feat: build animated subtitled board previews`.

### Task 6: Documentation, examples, and release verification

**Files:**
- Modify: `README.md`
- Modify: `docs/schema.md`
- Modify: `examples/moonlight_delivery.py`
- Modify: `examples/moonlight_delivery.json`
- Modify: `tests/test_example.py`
- Modify: `tests/test_uv_project.py`

1. Add failing documentation/example assertions for dialogue and board commands.
2. Document Z-Image installation, model files, rapid-board workflow, H3 dialogue compilation, and subtitle behavior; update the example.
3. Run `uv lock --check`, Ruff format/check, strict mypy, all tests, `uv build`, and CLI smoke tests.
4. Run live `board preflight` and confirm it either passes or reports the exact missing Z-Image models without submitting work.
5. Request independent code review, address Critical/Important findings, rerun the complete gate, and commit final documentation/fixes.
