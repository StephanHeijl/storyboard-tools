from __future__ import annotations

import json
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storyboardctl.errors import ExternalServiceFailure
from storyboardctl.paths import file_sha256, resolve_project_path
from storyboardctl.service import StoryboardService, _json, _now


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
        frames = self.service.latest_board_frames(version_name)
        with self.service.database.transaction(write=True) as connection:
            version = self.service._version(connection, version_name)
            number = int(
                connection.execute(
                    "SELECT COALESCE(MAX(preview_number),0)+1 FROM board_previews WHERE version_id=?", (version["id"],)
                ).fetchone()[0]
            )
            preview_id = str(uuid.uuid4())
            base = f"boards/{version_name}/previews/preview-{number:03d}"
            connection.execute(
                "INSERT INTO board_previews(id,version_id,preview_number,storyboard_snapshot,state,"
                "settings_json,manifest_path,output_path,created_at) "
                "VALUES (?,?,?,?, 'building',?,?,?,?)",
                (
                    preview_id,
                    version["id"],
                    number,
                    version["snapshot"],
                    _json(settings.__dict__),
                    f"{base}.json",
                    f"{base}.mp4",
                    _now(),
                ),
            )
        output = resolve_project_path(self.service.project_root, f"{base}.mp4")
        manifest_path = resolve_project_path(self.service.project_root, f"{base}.json")
        output.parent.mkdir(parents=True, exist_ok=True)
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
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
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
                    ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(output)],
                    capture_output=True,
                    text=True,
                )
                if run.returncode != 0:
                    raise ExternalServiceFailure(f"preview assembly failed: {run.stderr[-1200:]}")
            duration = sum(float(frame["duration_seconds"]) for frame in frames)
            digest = file_sha256(output)
            with self.service.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE board_previews SET state='completed',output_sha256=?,duration_seconds=?,"
                    "completed_at=? WHERE id=?",
                    (digest, duration, _now(), preview_id),
                )
            return {
                "preview_id": preview_id,
                "state": "completed",
                "manifest_path": f"{base}.json",
                "output_path": f"{base}.mp4",
                "output_sha256": digest,
                "duration_seconds": duration,
            }
        except Exception as error:
            with self.service.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE board_previews SET state='failed',error_message=? WHERE id=?", (str(error), preview_id)
                )
            raise
