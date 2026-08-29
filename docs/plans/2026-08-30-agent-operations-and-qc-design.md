# Agent Operations and QC Design

## Objective

Close every gap found during the first real two-shot production so a fresh agent can author, render, inspect, review, bridge continuity, and approve an assembly without raw REST calls, direct SQLite queries, or hand-written ffmpeg commands.

## Design

The CLI remains JSON-first and adds five surfaces. `production status` and `storyboard audit` expose render/approval/compilation readiness; `render list` exposes attempts without requiring IDs. `comfy ping`, `comfy queue`, and `comfy preflight` provide live server and H3 dependency checks. Rendering is split into atomic `render execute --no-wait` and `render wait`, while existing synchronous commands remain compatible. Expected render failures carry the already-created render ID and paths in their JSON details.

A new quality inspector owns ffprobe/ffmpeg diagnostics and creates project-relative reports, contact sheets, cut-boundary sheets, decode checks, black/freeze findings, hashes, and audio levels. Reports are stored in SQLite and callable through `render qc` and `compile qc`. Compilation reviews mirror render reviews with approve, reject, and history commands.

`shot bridge` extracts the final frame from an approved source render, registers and hashes it as a reusable image, creates one new target-shot revision with a `first_frame` relationship, and switches that revision to image-to-video. Database changes are one transaction; filesystem staging is cleaned on failure.

H3 converts a schema-level negative prompt into explicit avoidance instructions because its workflow has no negative-conditioning socket. This conversion is visible in the saved workflow instead of silently dropping author intent.

## Storage and compatibility

Migration 2 adds quality reports, compilation reviews, and approved compilation selection without rewriting existing tables. Existing databases migrate in place. Existing synchronous render and compile commands keep their behavior.

## Error handling and tests

Every feature starts with a failing service or CLI test. External commands are argument-list subprocesses with staged outputs and structured failures. Integration tests use generated synthetic video rather than production media. The final gate is Ruff, strict mypy, the complete pytest suite, wheel build/install, CLI smoke tests, and a context-isolated subagent production run.
