from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from storyboardctl.database import Database
from storyboardctl.errors import Conflict, ExternalServiceFailure, IntegrityFailure, NotFound
from storyboardctl.paths import file_sha256, resolve_project_path


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class CompilationSettings:
    width: int = 1920
    height: int = 1080
    fps: int = 24
    video_codec: str = "libx264"
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    audio_rate: int = 48_000

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or self.fps <= 0 or self.audio_rate <= 0:
            raise ValueError("compilation dimensions, fps, and audio rate must be positive")


def _probe(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ExternalServiceFailure(f"ffprobe failed for {path}: {detail}") from error
    return cast(dict[str, Any], json.loads(result.stdout))


def probe_duration(path: str | Path) -> float:
    payload = _probe(Path(path))
    try:
        return float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise ExternalServiceFailure(f"ffprobe returned no duration for {path}") from error


def _has_audio(path: Path) -> bool:
    payload = _probe(path)
    return any(stream.get("codec_type") == "audio" for stream in payload.get("streams", []))


class Compiler:
    def __init__(self, database: Database, project_root: str | Path) -> None:
        self.database = database
        self.project_root = Path(project_root).resolve()

    def create_manifest(self, version_name: str, settings: CompilationSettings | None = None) -> dict[str, Any]:
        resolved_settings = settings or CompilationSettings()
        with self.database.connect() as connection:
            version = connection.execute(
                "SELECT id, name, snapshot FROM storyboard_versions WHERE production_id = 1 AND name = ?",
                (version_name,),
            ).fetchone()
            if version is None:
                raise NotFound(f"storyboard version not found: {version_name}")
            rows = connection.execute(
                "SELECT vs.position, s.id AS shot_id, s.shot_key, sr.id AS revision_id, "
                "sr.duration_seconds AS intended_duration, r.id AS render_id, r.output_path, "
                "r.output_sha256, r.duration_seconds AS render_duration "
                "FROM version_shots vs JOIN shots s ON s.id = vs.shot_id "
                "JOIN shot_revisions sr ON sr.id = vs.revision_id "
                "LEFT JOIN approved_renders ar ON ar.revision_id = sr.id "
                "LEFT JOIN renders r ON r.id = ar.render_id "
                "WHERE vs.version_id = ? AND vs.archived_at IS NULL ORDER BY vs.position",
                (version["id"],),
            ).fetchall()
            music_by_revision = {
                row["revision_id"]: [
                    {
                        "cue_key": music["cue_key"],
                        "title": music["title"],
                        "asset_path": music["asset_path"],
                        "relationship": music["relationship"],
                        "offset_seconds": float(music["offset_seconds"]),
                        "gain_db": music["gain_db"],
                        "fade_in_seconds": music["fade_in_seconds"],
                        "fade_out_seconds": music["fade_out_seconds"],
                    }
                    for music in connection.execute(
                        "SELECT mc.cue_key, mc.title, a.path AS asset_path, sm.relationship, "
                        "sm.offset_seconds, sm.gain_db, sm.fade_in_seconds, sm.fade_out_seconds "
                        "FROM shot_music sm JOIN music_cues mc ON mc.id = sm.music_cue_id "
                        "JOIN assets a ON a.id = mc.asset_id WHERE sm.revision_id = ? "
                        "ORDER BY mc.cue_key",
                        (row["revision_id"],),
                    ).fetchall()
                ]
                for row in rows
            }
        if not rows:
            raise Conflict(f"storyboard version has no active shots: {version_name}")

        items: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if row["render_id"] is None:
                raise Conflict(f"no approved render for shot {row['position']}")
            if row["output_path"] is None or row["output_sha256"] is None:
                raise IntegrityFailure(f"approved render has incomplete provenance for shot {row['position']}")
            source = resolve_project_path(self.project_root, row["output_path"], must_exist=True)
            actual_hash = file_sha256(source)
            if actual_hash != row["output_sha256"]:
                raise IntegrityFailure(
                    f"approved render hash mismatch for shot {row['position']}",
                    details={"expected": row["output_sha256"], "actual": actual_hash},
                )
            if float(row["render_duration"] or 0) + 0.001 < float(row["intended_duration"]):
                raise IntegrityFailure(f"approved render for shot {row['position']} is shorter than intended duration")
            items.append(
                {
                    "order": index,
                    "position": int(row["position"]),
                    "shot_id": row["shot_id"],
                    "shot_key": row["shot_key"],
                    "revision_id": row["revision_id"],
                    "render_id": row["render_id"],
                    "source_path": row["output_path"],
                    "source_sha256": row["output_sha256"],
                    "duration_seconds": float(row["intended_duration"]),
                    "music": music_by_revision[row["revision_id"]],
                }
            )

        with self.database.transaction(write=True) as connection:
            current = connection.execute(
                "SELECT snapshot FROM storyboard_versions WHERE id = ?", (version["id"],)
            ).fetchone()
            if current is None or int(current["snapshot"]) != int(version["snapshot"]):
                raise Conflict("storyboard changed while compilation manifest was being prepared")
            number = int(
                connection.execute(
                    "SELECT COALESCE(MAX(compilation_number), 0) + 1 FROM compilations WHERE version_id = ?",
                    (version["id"],),
                ).fetchone()[0]
            )
            compilation_id = str(uuid.uuid4())
            manifest_path = f"assembly/{version_name}/cut_{number:03d}.json"
            output_path = f"assembly/{version_name}/cut_{number:03d}.mp4"
            manifest: dict[str, Any] = {
                "schema_version": 1,
                "compilation_id": compilation_id,
                "version": version_name,
                "version_id": version["id"],
                "storyboard_snapshot": int(version["snapshot"]),
                "compilation_number": number,
                "manifest_path": manifest_path,
                "output_path": output_path,
                "settings": asdict(resolved_settings),
                "music_mixed": False,
                "items": items,
            }
            connection.execute(
                "INSERT INTO compilations(id, version_id, compilation_number, storyboard_snapshot, "
                "state, manifest_path, output_path, settings_json, created_at) "
                "VALUES (?, ?, ?, ?, 'planned', ?, ?, ?, ?)",
                (
                    compilation_id,
                    version["id"],
                    number,
                    version["snapshot"],
                    manifest_path,
                    output_path,
                    json.dumps(asdict(resolved_settings), sort_keys=True),
                    _now(),
                ),
            )
            for item in items:
                connection.execute(
                    "INSERT INTO compilation_items(compilation_id, item_order, shot_id, revision_id, "
                    "render_id, source_sha256, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        compilation_id,
                        item["order"],
                        item["shot_id"],
                        item["revision_id"],
                        item["render_id"],
                        item["source_sha256"],
                        item["duration_seconds"],
                    ),
                )
        destination = resolve_project_path(self.project_root, manifest_path)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        except OSError as error:
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE compilations SET state = 'failed', error_message = ? WHERE id = ?",
                    (str(error), compilation_id),
                )
            raise IntegrityFailure(f"could not persist compilation manifest: {error}") from error
        return manifest

    def build(self, version_name: str, settings: CompilationSettings | None = None) -> dict[str, Any]:
        manifest = self.create_manifest(version_name, settings)
        compilation_id = manifest["compilation_id"]
        resolved = CompilationSettings(**manifest["settings"])
        output = resolve_project_path(self.project_root, manifest["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        with self.database.transaction(write=True) as connection:
            connection.execute("UPDATE compilations SET state = 'building' WHERE id = ?", (compilation_id,))
        try:
            with tempfile.TemporaryDirectory(prefix="storyboardctl-", dir=output.parent) as directory:
                temp_root = Path(directory)
                normalized: list[Path] = []
                for item in manifest["items"]:
                    source = resolve_project_path(self.project_root, item["source_path"], must_exist=True)
                    staged_source = temp_root / f"source_{item['order']:04d}{source.suffix}"
                    shutil.copyfile(source, staged_source)
                    if file_sha256(staged_source) != item["source_sha256"]:
                        raise IntegrityFailure(f"source hash changed before assembly for shot {item['position']}")
                    duration = float(item["duration_seconds"])
                    target = temp_root / f"shot_{item['order']:04d}.mp4"
                    command = [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(staged_source),
                    ]
                    audio_input = "0:a:0"
                    if not _has_audio(staged_source):
                        command.extend(
                            [
                                "-f",
                                "lavfi",
                                "-t",
                                f"{duration:g}",
                                "-i",
                                f"anullsrc=r={resolved.audio_rate}:cl=stereo",
                            ]
                        )
                        audio_input = "1:a:0"
                    command.extend(
                        [
                            "-map",
                            "0:v:0",
                            "-map",
                            audio_input,
                            "-t",
                            f"{duration:g}",
                            "-vf",
                            f"scale={resolved.width}:{resolved.height}:force_original_aspect_ratio=decrease,"
                            f"pad={resolved.width}:{resolved.height}:(ow-iw)/2:(oh-ih)/2,"
                            f"fps={resolved.fps},format={resolved.pixel_format}",
                            "-c:v",
                            resolved.video_codec,
                            "-c:a",
                            resolved.audio_codec,
                            "-ar",
                            str(resolved.audio_rate),
                            "-ac",
                            "2",
                            str(target),
                        ]
                    )
                    subprocess.run(command, check=True, capture_output=True, text=True)
                    normalized.append(target)
                concat_file = temp_root / "concat.txt"
                concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in normalized), encoding="utf-8")
                staged_output = temp_root / "assembled.mp4"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat_file),
                        "-c",
                        "copy",
                        str(staged_output),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                shutil.copyfile(staged_output, output)
        except IntegrityFailure as error:
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE compilations SET state = 'failed', error_message = ? WHERE id = ?",
                    (str(error), compilation_id),
                )
            raise
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE compilations SET state = 'failed', error_message = ? WHERE id = ?",
                    (detail, compilation_id),
                )
            raise ExternalServiceFailure(f"ffmpeg assembly failed: {detail}") from error
        digest = file_sha256(output)
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE compilations SET state = 'completed', output_sha256 = ?, completed_at = ? WHERE id = ?",
                (digest, _now(), compilation_id),
            )
        return {
            **manifest,
            "state": "completed",
            "output_sha256": digest,
            "duration_seconds": probe_duration(output),
        }
