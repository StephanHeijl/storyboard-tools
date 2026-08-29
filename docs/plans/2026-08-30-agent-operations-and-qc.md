# Agent Operations and QC Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the complete storyboard production and review loop agent-native, including status, live ComfyUI diagnostics, asynchronous execution, QC, compilation review, continuity bridging, and negative-prompt handling.

**Architecture:** Extend the service layer for database-backed queries and reviews, the Comfy client/runner for live and split execution, and add one focused quality-inspection module for ffmpeg/ffprobe operations. Expose every operation through stable JSON CLI commands while preserving existing interfaces.

**Tech Stack:** Python 3.11+, SQLite, Pydantic, Typer, HTTPX, ffmpeg/ffprobe, pytest, Ruff, mypy.

---

### Task 1: Operational status and render discovery

**Files:** Modify `src/storyboardctl/service.py`, `src/storyboardctl/cli.py`; test `tests/test_status.py`, `tests/test_cli.py`.

1. Write failing tests for filtered render listing, production status, and storyboard audit.
2. Run the focused tests and confirm missing-method failures.
3. Implement read-only queries and JSON CLI commands.
4. Run focused tests, then commit.

### Task 2: Live ComfyUI diagnostics and split execution

**Files:** Modify `src/storyboardctl/comfy/client.py`, `src/storyboardctl/comfy/h3.py`, `src/storyboardctl/rendering.py`, `src/storyboardctl/cli.py`; test `tests/test_comfy.py`, `tests/test_rendering.py`, `tests/test_cli.py`.

1. Write failing tests for ping, queue, H3 preflight, submit-without-wait, wait, and render-context errors.
2. Confirm failures.
3. Add client diagnostic calls, adapter requirements, runner submit/wait, and CLI commands.
4. Keep synchronous execution as submit plus wait; run tests and commit.

### Task 3: Automated media QC

**Files:** Create `src/storyboardctl/quality.py`; modify `src/storyboardctl/migrations.py`, `src/storyboardctl/cli.py`; create `tests/test_quality.py`.

1. Write failing synthetic-video tests for render and compilation QC reports.
2. Confirm the module/commands are absent.
3. Implement probing, decode checks, contact sheets, boundary sheets, black/freeze parsing, loudness, report persistence, and CLI commands.
4. Run focused tests and commit.

### Task 4: Compilation reviews

**Files:** Modify `src/storyboardctl/migrations.py`, `src/storyboardctl/service.py`, `src/storyboardctl/cli.py`; test `tests/test_compilation_reviews.py`.

1. Write failing tests for approve, reject, supersession, and history.
2. Confirm failures.
3. Implement migration and service/CLI operations.
4. Run tests and commit.

### Task 5: Continuity-frame bridge

**Files:** Modify `src/storyboardctl/service.py`, `src/storyboardctl/cli.py`; create `tests/test_bridge.py`.

1. Write a failing test using a generated approved video and target shot.
2. Confirm `shot bridge` is absent.
3. Implement staged last-frame extraction and one-transaction asset/revision/link mutation.
4. Verify hashes, snapshot behavior, and cleanup; commit.

### Task 6: Negative-prompt semantics and documentation

**Files:** Modify `src/storyboardctl/comfy/adapters.py`, `src/storyboardctl/comfy/h3.py`, `src/storyboardctl/rendering.py`, `README.md`, `docs/schema.md`; test `tests/test_h3_adapter.py`, `tests/test_rendering.py`.

1. Write failing tests proving negative prompts reach the saved workflow.
2. Confirm failure.
3. Add negative prompt to workflow context and translate it into explicit H3 avoidance instructions.
4. Document every new command and behavior; commit.

### Task 7: Release and fresh-agent validation

1. Run Ruff format/check, strict mypy, and the complete pytest suite.
2. Build and install the wheel in a clean environment.
3. Spawn a context-isolated subagent to create and execute a new two-shot production using only published tooling where possible.
4. Require the subagent to report exact tool/ad-hoc counts and newly discovered gaps.
5. Independently inspect its artifacts and results before reporting completion.
