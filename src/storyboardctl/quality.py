from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from storyboardctl.database import Database
from storyboardctl.errors import Conflict, ExternalServiceFailure, IntegrityFailure, NotFound
from storyboardctl.paths import file_sha256, resolve_project_path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ExternalServiceFailure(f"media QC command failed: {detail}") from error


def _probe(path: Path) -> dict[str, Any]:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ]
    )
    return cast(dict[str, Any], json.loads(result.stdout))


class QualityInspector:
    def __init__(self, database: Database, project_root: str | Path) -> None:
        self.database = database
        self.project_root = Path(project_root).resolve()

    def _entity(self, entity_type: str, entity_id: str) -> tuple[dict[str, Any], list[float]]:
        with self.database.connect() as connection:
            if entity_type == "render":
                row = connection.execute(
                    "SELECT id, state, output_path, output_sha256 FROM renders WHERE id = ?",
                    (entity_id,),
                ).fetchone()
                durations: list[float] = []
            else:
                row = connection.execute(
                    "SELECT id, state, output_path, output_sha256 FROM compilations WHERE id = ?",
                    (entity_id,),
                ).fetchone()
                durations = [
                    float(item[0])
                    for item in connection.execute(
                        "SELECT duration_seconds FROM compilation_items WHERE compilation_id = ? ORDER BY item_order",
                        (entity_id,),
                    ).fetchall()
                ]
        if row is None:
            raise NotFound(f"{entity_type} not found: {entity_id}")
        if row["state"] != "completed":
            raise Conflict(f"{entity_type} must be completed before QC: {row['state']}")
        if not row["output_path"] or not row["output_sha256"]:
            raise IntegrityFailure(f"{entity_type} has incomplete output provenance")
        result = dict(row)
        result["path"] = resolve_project_path(self.project_root, row["output_path"], must_exist=True)
        actual = file_sha256(result["path"])
        if actual != row["output_sha256"]:
            raise IntegrityFailure(
                f"{entity_type} output hash mismatch",
                details={"expected": row["output_sha256"], "actual": actual},
            )
        return result, durations

    def _next_report(self, entity_type: str, entity_id: str) -> int:
        column = "render_id" if entity_type == "render" else "compilation_id"
        with self.database.connect() as connection:
            return int(
                connection.execute(
                    f"SELECT COALESCE(MAX(report_number), 0) + 1 FROM quality_reports WHERE {column} = ?",
                    (entity_id,),
                ).fetchone()[0]
            )

    def _contact_sheet(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                "fps=4,scale=368:208:force_original_aspect_ratio=decrease,pad=368:208:(ow-iw)/2:(oh-ih)/2,tile=4x4",
                "-frames:v",
                "1",
                str(destination),
            ]
        )

    def _boundary_sheets(self, source: Path, durations: list[float], directory: Path) -> list[Path]:
        boundaries: list[Path] = []
        elapsed = 0.0
        for index, duration in enumerate(durations[:-1], start=1):
            elapsed += duration
            destination = directory / f"boundary_{index:03d}.jpg"
            _run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{max(0.0, elapsed - 0.1):.6f}",
                    "-i",
                    str(source),
                    "-vf",
                    "fps=24,scale=368:208,tile=5x1",
                    "-frames:v",
                    "1",
                    str(destination),
                ]
            )
            boundaries.append(destination)
        return boundaries

    def _inspect(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        entity, durations = self._entity(entity_type, entity_id)
        source = cast(Path, entity["path"])
        number = self._next_report(entity_type, entity_id)
        directory = self.project_root / "review" / f"{entity_type}s" / entity_id[:8] / f"qc_{number:03d}"
        contact = directory / "contact.jpg"
        self._contact_sheet(source, contact)
        diagnostics = _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(source),
                "-vf",
                "blackdetect=d=0.1:pix_th=0.05,freezedetect=n=-50dB:d=0.5",
                "-an",
                "-f",
                "null",
                "/dev/null",
            ]
        )
        black_segments = [
            {"start": float(start), "end": float(end)}
            for start, end in re.findall(r"black_start:([0-9.]+).*?black_end:([0-9.]+)", diagnostics.stderr)
        ]
        freeze_events = [line.strip() for line in diagnostics.stderr.splitlines() if "freeze_" in line]
        probe = _probe(source)
        streams = probe.get("streams", [])
        has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
        audio: dict[str, Any] = {"present": has_audio, "mean_db": None, "max_db": None}
        if has_audio:
            levels = _run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-af",
                    "volumedetect",
                    "-f",
                    "null",
                    "/dev/null",
                ]
            ).stderr
            mean = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", levels)
            peak = re.search(r"max_volume:\s*(-?[0-9.]+) dB", levels)
            audio.update(
                {
                    "mean_db": float(mean.group(1)) if mean else None,
                    "max_db": float(peak.group(1)) if peak else None,
                }
            )
        boundaries = self._boundary_sheets(source, durations, directory)
        findings = []
        if black_segments:
            findings.append({"code": "black_frames", "count": len(black_segments)})
        if freeze_events:
            findings.append({"code": "freeze_events", "count": len(freeze_events)})
        report_path = directory / "report.json"
        report = {
            "schema_version": 1,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "report_number": number,
            "verdict": "warning" if findings else "pass",
            "decode_ok": True,
            "duration_seconds": float(probe["format"]["duration"]),
            "sha256": entity["output_sha256"],
            "streams": streams,
            "audio": audio,
            "black_segments": black_segments,
            "freeze_events": freeze_events,
            "findings": findings,
            "contact_sheet_path": contact.relative_to(self.project_root).as_posix(),
            "boundary_sheet_paths": [path.relative_to(self.project_root).as_posix() for path in boundaries],
            "report_path": report_path.relative_to(self.project_root).as_posix(),
            "created_at": _now(),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_id = str(uuid.uuid4())
        render_id = entity_id if entity_type == "render" else None
        compilation_id = entity_id if entity_type == "compilation" else None
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO quality_reports(id, render_id, compilation_id, report_number, verdict, "
                "report_path, report_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report_id,
                    render_id,
                    compilation_id,
                    number,
                    report["verdict"],
                    report["report_path"],
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    report["created_at"],
                ),
            )
        report["quality_report_id"] = report_id
        return report

    def inspect_render(self, render_id: str) -> dict[str, Any]:
        return self._inspect("render", render_id)

    def inspect_compilation(self, compilation_id: str) -> dict[str, Any]:
        return self._inspect("compilation", compilation_id)
