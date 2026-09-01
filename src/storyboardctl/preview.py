from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storyboardctl.compiler import probe_duration
from storyboardctl.errors import Conflict, ExternalServiceFailure, IntegrityFailure
from storyboardctl.paths import file_sha256, resolve_project_path
from storyboardctl.service import StoryboardService, _now


@dataclass(frozen=True)
class PreviewSettings:
    width: int = 1344
    height: int = 768
    fps: int = 24


def ffmpeg_has_subtitles() -> bool:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return result.returncode == 0 and any(" subtitles " in line for line in result.stdout.splitlines())


def _ass_time(seconds: float) -> str:
    centiseconds = round(seconds * 100)
    hours = centiseconds // 360000
    minutes = (centiseconds // 6000) % 60
    seconds = (centiseconds // 100) % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds % 100:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def ass_document(dialogue: list[dict[str, Any]], width: int, height: int) -> str:
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Alignment, MarginL, MarginR, MarginV, Outline, Shadow\n"
        f"Style: Default,Arial,{max(28, height // 20)},&H00FFFFFF,&H00000000,&H90000000,"
        "0,0,2,80,80,54,2,0\n\n[Events]\nFormat: Layer, Start, End, Style, Text\n"
    )
    events = "".join(
        f"Dialogue: 0,{_ass_time(c['start_seconds'])},{_ass_time(c['end_seconds'])},Default,{_ass_escape(c['text'])}\n"
        for c in dialogue
    )
    return header + events


class PreviewBuilder:
    def __init__(self, service: StoryboardService) -> None:
        self.service = service

    def build(self, version_name: str, settings: PreviewSettings | None = None) -> dict[str, Any]:
        settings = settings or PreviewSettings()
        if not ffmpeg_has_subtitles():
            raise ExternalServiceFailure(
                "ffmpeg lacks the subtitles filter; install an ffmpeg build with libass support"
            )
        plan = self.service.plan_board_preview(version_name, settings.__dict__)
        frames = plan["frames"]
        preview_id = plan["preview_id"]
        base = plan["base"]
        output = resolve_project_path(self.service.project_root, f"{base}.mp4")
        manifest_path = resolve_project_path(self.service.project_root, f"{base}.json")
        manifest = {
            "preview_id": preview_id,
            "version": version_name,
            "settings": settings.__dict__,
            "frames": [
                {
                    k: frame[k]
                    for k in (
                        "frame_id",
                        "revision_id",
                        "position_snapshot",
                        "output_path",
                        "output_sha256",
                        "duration_seconds",
                        "dialogue",
                    )
                }
                for frame in frames
            ],
        }
        staged_output = output.with_name(f".{output.name}.{preview_id}.part.mp4")
        staged_manifest = manifest_path.with_name(f".{manifest_path.name}.{preview_id}.part")
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            for frame in frames:
                source = resolve_project_path(self.service.project_root, frame["output_path"], must_exist=True)
                actual = file_sha256(source)
                if actual != frame["output_sha256"]:
                    raise IntegrityFailure(
                        f"rapid-board frame hash mismatch at position {frame['position_snapshot']}",
                        details={"expected": frame["output_sha256"], "actual": actual},
                    )
            with staged_manifest.open("w", encoding="utf-8") as stream:
                stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            with tempfile.TemporaryDirectory(prefix="storyboardctl-preview-") as temp_name:
                temp = Path(temp_name)
                segments: list[Path] = []
                for index, frame in enumerate(frames):
                    segment = temp / f"{index:04d}.mp4"
                    subtitle = temp / f"{index:04d}.ass"
                    subtitle.write_text(
                        ass_document(frame["dialogue"], settings.width, settings.height), encoding="utf-8"
                    )
                    duration = float(frame["duration_seconds"])
                    count = max(1, round(duration * settings.fps))
                    direction = "iw-iw/zoom" if index % 2 == 0 else "0"
                    vf = (
                        f"scale={settings.width * 2}:{settings.height * 2}:force_original_aspect_ratio=increase,"
                        f"crop={settings.width * 2}:{settings.height * 2},"
                        f"zoompan=z='min(zoom+0.0008,1.04)':x='{direction}':y='ih/2-(ih/zoom/2)':"
                        f"d={count}:s={settings.width}x{settings.height}:fps={settings.fps},"
                        f"subtitles=filename='{subtitle}'"
                    )
                    command = [
                        "ffmpeg",
                        "-y",
                        "-loop",
                        "1",
                        "-i",
                        str(resolve_project_path(self.service.project_root, frame["output_path"], must_exist=True)),
                        "-vf",
                        vf,
                        "-frames:v",
                        str(count),
                        "-an",
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        str(segment),
                    ]
                    run = subprocess.run(command, capture_output=True, text=True)
                    if run.returncode != 0:
                        raise ExternalServiceFailure(f"preview segment failed: {run.stderr[-1200:]}")
                    segments.append(segment)
                concat = temp / "concat.txt"
                concat.write_text("".join(f"file '{p}'\n" for p in segments), encoding="utf-8")
                run = subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat),
                        "-c",
                        "copy",
                        str(staged_output),
                    ],
                    capture_output=True,
                    text=True,
                )
                if run.returncode != 0:
                    raise ExternalServiceFailure(f"preview assembly failed: {run.stderr[-1200:]}")
            duration = probe_duration(staged_output)
            digest = file_sha256(staged_output)
            with self.service.database.transaction(write=True) as connection:
                row = connection.execute(
                    "SELECT state,version_id,storyboard_snapshot FROM board_previews WHERE id=?", (preview_id,)
                ).fetchone()
                if row is None or row["state"] != "building":
                    raise Conflict("board preview is no longer building")
                snapshot = connection.execute(
                    "SELECT snapshot FROM storyboard_versions WHERE id=?", (row["version_id"],)
                ).fetchone()[0]
                if snapshot != row["storyboard_snapshot"]:
                    raise Conflict("storyboard changed while the rapid preview was building")
                staged_manifest.replace(manifest_path)
                staged_output.replace(output)
                updated = connection.execute(
                    "UPDATE board_previews SET state='completed',output_sha256=?,duration_seconds=?,"
                    "completed_at=? WHERE id=? AND state='building'",
                    (digest, duration, _now(), preview_id),
                )
                if updated.rowcount != 1:
                    raise Conflict("board preview is no longer building")
            return {
                "preview_id": preview_id,
                "state": "completed",
                "manifest_path": f"{base}.json",
                "output_path": f"{base}.mp4",
                "output_sha256": digest,
                "duration_seconds": duration,
            }
        except Exception as error:
            staged_output.unlink(missing_ok=True)
            staged_manifest.unlink(missing_ok=True)
            self.service.fail_board_preview(preview_id, str(error))
            raise
