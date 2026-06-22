from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from fcpxml_generator import MediaClip, MediaProbeError, TimelineClip, probe_folder, probe_media, write_fcpxml_timeline


TIMELINE_VERSION = 1


class TimelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class EditableClip:
    id: str
    path: Path
    original_duration: Fraction
    trim_in: Fraction
    trim_out: Fraction
    width: int
    height: int
    frame_rate: Fraction
    has_audio: bool
    name: str
    enabled: bool = True
    note: str = ""
    thumbnail: str | None = None
    audio_channels: int | None = None
    audio_rate: int | None = None
    original_path: Path | None = None
    rotation: int = 0
    normalized_path: Path | None = None
    rotation_checked: bool = True

    @property
    def timeline_duration(self) -> Fraction:
        return self.trim_out - self.trim_in

    @property
    def source_path(self) -> Path:
        return self.original_path or self.path


@dataclass(frozen=True)
class EditableTimeline:
    project_name: str
    media_folder: Path
    sequence_width: int
    sequence_height: int
    sequence_frame_rate: Fraction
    clips: list[EditableClip]


def fraction_to_text(value: Fraction) -> str:
    value = value.limit_denominator(1_000_000)
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def fraction_from_text(value: Any) -> Fraction:
    if isinstance(value, int):
        return Fraction(value, 1)
    if isinstance(value, float):
        return Fraction(str(value)).limit_denominator(1_000_000)
    if not isinstance(value, str):
        raise TimelineError(f"時刻値を解釈できません: {value!r}")
    return Fraction(value)


def parse_timecode(value: str, frame_rate: Fraction | None = None) -> Fraction:
    text = value.strip()
    if not text:
        raise TimelineError("時刻が空です。")
    if ":" not in text:
        return Fraction(text).limit_denominator(1_000_000)

    parts = text.split(":")
    if len(parts) == 3 and "." in parts[-1]:
        hours, minutes, seconds_text = parts
        return (
            Fraction(int(hours) * 3600, 1)
            + Fraction(int(minutes) * 60, 1)
            + Fraction(seconds_text).limit_denominator(1_000_000)
        )

    if len(parts) == 3:
        hours, minutes, seconds_text = parts
        return (
            Fraction(int(hours) * 3600, 1)
            + Fraction(int(minutes) * 60, 1)
            + Fraction(seconds_text).limit_denominator(1_000_000)
        )

    if len(parts) == 4:
        if frame_rate is None:
            raise TimelineError("フレーム付きタイムコードにはフレームレートが必要です。")
        hours, minutes, seconds, frames = (int(part) for part in parts)
        return Fraction(hours * 3600 + minutes * 60 + seconds, 1) + Fraction(frames, 1) / frame_rate

    raise TimelineError(f"時刻形式を解釈できません: {value}")


def format_time(value: Fraction) -> str:
    total_ms = int(round(float(value) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_edit_time(value: Fraction) -> str:
    total_us = int(round(float(value) * 1_000_000))
    hours, remainder = divmod(total_us, 3_600_000_000)
    minutes, remainder = divmod(remainder, 60_000_000)
    seconds, micros = divmod(remainder, 1_000_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{micros:06d}"


def scan_timeline(media_folder: Path, project_name: str = "timeline") -> EditableTimeline:
    media_folder = media_folder.expanduser().resolve()
    clips = probe_folder(media_folder)
    if not clips:
        raise TimelineError("素材が見つかりません。")
    first = clips[0]
    return EditableTimeline(
        project_name=project_name,
        media_folder=media_folder,
        sequence_width=first.width,
        sequence_height=first.height,
        sequence_frame_rate=first.frame_rate,
        clips=[_editable_clip_from_media(index, clip) for index, clip in enumerate(clips, start=1)],
    )


def _editable_clip_from_media(index: int, clip: MediaClip) -> EditableClip:
    return EditableClip(
        id=f"c{index:03d}",
        path=clip.path,
        original_duration=clip.duration,
        trim_in=Fraction(0, 1),
        trim_out=clip.duration,
        width=clip.width,
        height=clip.height,
        frame_rate=clip.frame_rate,
        has_audio=clip.has_audio,
        name=clip.path.stem,
        audio_channels=clip.audio_channels,
        audio_rate=clip.audio_rate,
        original_path=clip.path,
        rotation=clip.rotation,
        rotation_checked=True,
    )


def timeline_to_dict(timeline: EditableTimeline) -> dict[str, Any]:
    return {
        "version": TIMELINE_VERSION,
        "project_name": timeline.project_name,
        "media_folder": str(timeline.media_folder),
        "sequence": {
            "width": timeline.sequence_width,
            "height": timeline.sequence_height,
            "frame_rate": fraction_to_text(timeline.sequence_frame_rate),
        },
        "clips": [
            {
                "id": clip.id,
                "path": str(clip.path),
                "original_duration": fraction_to_text(clip.original_duration),
                "in": fraction_to_text(clip.trim_in),
                "out": fraction_to_text(clip.trim_out),
                "width": clip.width,
                "height": clip.height,
                "frame_rate": fraction_to_text(clip.frame_rate),
                "has_audio": clip.has_audio,
                "audio_channels": clip.audio_channels,
                "audio_rate": clip.audio_rate,
                "original_path": str(clip.original_path or clip.path),
                "rotation": clip.rotation,
                "normalized_path": str(clip.normalized_path) if clip.normalized_path else None,
                "name": clip.name,
                "enabled": clip.enabled,
                "note": clip.note,
                "thumbnail": clip.thumbnail,
            }
            for clip in timeline.clips
        ],
    }


def timeline_from_dict(data: dict[str, Any]) -> EditableTimeline:
    if data.get("version") != TIMELINE_VERSION:
        raise TimelineError(f"未対応のtimeline versionです: {data.get('version')}")
    sequence = data["sequence"]
    clips = []
    for item in data["clips"]:
        path = Path(item["path"]).expanduser()
        original_path = Path(item.get("original_path") or item["path"]).expanduser()
        normalized_path = item.get("normalized_path")
        clips.append(
            EditableClip(
                id=item["id"],
                path=path,
                original_path=original_path,
                original_duration=fraction_from_text(item["original_duration"]),
                trim_in=fraction_from_text(item["in"]),
                trim_out=fraction_from_text(item["out"]),
                width=int(item["width"]),
                height=int(item["height"]),
                frame_rate=fraction_from_text(item["frame_rate"]),
                has_audio=bool(item["has_audio"]),
                audio_channels=item.get("audio_channels"),
                audio_rate=item.get("audio_rate"),
                rotation=int(item.get("rotation") or 0),
                normalized_path=Path(normalized_path).expanduser() if normalized_path else None,
                rotation_checked="rotation" in item,
                name=item.get("name") or path.stem,
                enabled=bool(item.get("enabled", True)),
                note=item.get("note") or "",
                thumbnail=item.get("thumbnail"),
            )
        )
    return EditableTimeline(
        project_name=data.get("project_name") or "timeline",
        media_folder=Path(data["media_folder"]).expanduser(),
        sequence_width=int(sequence["width"]),
        sequence_height=int(sequence["height"]),
        sequence_frame_rate=fraction_from_text(sequence["frame_rate"]),
        clips=clips,
    )


def load_timeline(path: Path) -> EditableTimeline:
    try:
        return timeline_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise TimelineError(
            "timeline.yaml を読み込めません。現MVPではJSON互換YAMLとして保存しています。"
        ) from exc


def save_timeline(timeline: EditableTimeline, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(timeline_to_dict(timeline), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def ensure_normalized_media(
    timeline: EditableTimeline,
    timeline_path: Path,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
) -> EditableTimeline:
    normalized_dir = timeline_path.parent / ".setlog" / "normalized"
    next_clips = []
    for clip in timeline.clips:
        checked_clip = clip
        if not checked_clip.rotation_checked:
            checked_clip = detect_clip_rotation(checked_clip, ffprobe_path=ffprobe_path)
        if checked_clip.rotation == 0:
            next_clips.append(
                EditableClip(
                    **{
                        **checked_clip.__dict__,
                        "path": checked_clip.source_path,
                        "normalized_path": None,
                        "rotation_checked": True,
                    }
                )
            )
            continue

        normalized_dir.mkdir(parents=True, exist_ok=True)
        normalized_path = normalized_dir / f"{checked_clip.id}.mp4"
        if needs_normalized_regeneration(checked_clip.source_path, normalized_path):
            write_normalized_clip(checked_clip, normalized_path, ffmpeg_path=ffmpeg_path)
        next_clips.append(
            EditableClip(
                **{
                    **checked_clip.__dict__,
                    "path": normalized_path,
                    "normalized_path": normalized_path,
                    "rotation_checked": True,
                }
            )
        )

    return EditableTimeline(
        project_name=timeline.project_name,
        media_folder=timeline.media_folder,
        sequence_width=timeline.sequence_width,
        sequence_height=timeline.sequence_height,
        sequence_frame_rate=timeline.sequence_frame_rate,
        clips=next_clips,
    )


def detect_clip_rotation(clip: EditableClip, ffprobe_path: str = "ffprobe") -> EditableClip:
    try:
        media = probe_media(clip.source_path, ffprobe_path=ffprobe_path)
    except MediaProbeError as exc:
        raise TimelineError(str(exc)) from exc
    except OSError:
        return EditableClip(**{**clip.__dict__, "rotation_checked": True})
    return EditableClip(
        **{
            **clip.__dict__,
            "width": media.width,
            "height": media.height,
            "rotation": media.rotation,
            "rotation_checked": True,
        }
    )


def needs_normalized_regeneration(source_path: Path, normalized_path: Path) -> bool:
    if not normalized_path.exists():
        return True
    try:
        return normalized_path.stat().st_mtime < source_path.stat().st_mtime
    except OSError:
        return True


def write_normalized_clip(clip: EditableClip, normalized_path: Path, ffmpeg_path: str = "ffmpeg") -> None:
    with tempfile.NamedTemporaryFile(
        prefix=f"{clip.id}.",
        suffix=".mp4",
        dir=normalized_path.parent,
        delete=False,
    ) as temp_file:
        temp_path = Path(temp_file.name)

    command = [
        ffmpeg_path,
        "-y",
        "-autorotate",
        "-i",
        str(clip.source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-map_metadata",
        "-1",
        "-metadata:s:v:0",
        "rotate=0",
        "-movflags",
        "+faststart",
        str(temp_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        temp_path.replace(normalized_path)
    except FileNotFoundError as exc:
        temp_path.unlink(missing_ok=True)
        raise TimelineError("ffmpeg が見つかりません。Homebrew などで ffmpeg をインストールしてください。") from exc
    except subprocess.CalledProcessError as exc:
        temp_path.unlink(missing_ok=True)
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise TimelineError(f"{clip.id} の回転補正済み動画生成に失敗しました: {detail}") from exc
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise


def get_intermediate_paths(timeline_path: Path, include_exports: bool = False) -> list[Path]:
    paths = [timeline_path.parent / ".setlog"]
    if include_exports:
        paths.append(timeline_path.parent / "timeline.fcpxml")
    return paths


def cleanup_intermediate_files(timeline_path: Path, include_exports: bool = False) -> list[Path]:
    removed: list[Path] = []
    for path in get_intermediate_paths(timeline_path, include_exports=include_exports):
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path)
        elif path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def validate_timeline(timeline: EditableTimeline) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for clip in timeline.clips:
        if clip.id in seen_ids:
            errors.append(f"{clip.id}: clip id が重複しています。")
        seen_ids.add(clip.id)
        if not clip.path.exists():
            errors.append(f"{clip.id}: 素材ファイルが存在しません: {clip.path}")
        if clip.trim_in < 0:
            errors.append(f"{clip.id}: in が0未満です。")
        if clip.trim_out > clip.original_duration:
            errors.append(f"{clip.id}: out が元尺を超えています。")
        if clip.trim_out <= clip.trim_in:
            errors.append(f"{clip.id}: out は in より後にしてください。")
    if not any(clip.enabled for clip in timeline.clips):
        errors.append("enabled なクリップがありません。")
    return errors


def require_clip(timeline: EditableTimeline, clip_id: str) -> EditableClip:
    for clip in timeline.clips:
        if clip.id == clip_id:
            return clip
    raise TimelineError(f"clip id が見つかりません: {clip_id}")


def replace_clip(timeline: EditableTimeline, updated_clip: EditableClip) -> EditableTimeline:
    return EditableTimeline(
        project_name=timeline.project_name,
        media_folder=timeline.media_folder,
        sequence_width=timeline.sequence_width,
        sequence_height=timeline.sequence_height,
        sequence_frame_rate=timeline.sequence_frame_rate,
        clips=[updated_clip if clip.id == updated_clip.id else clip for clip in timeline.clips],
    )


def trim_clip(timeline: EditableTimeline, clip_id: str, trim_in: str | None, trim_out: str | None) -> EditableTimeline:
    clip = require_clip(timeline, clip_id)
    next_in = clip.trim_in if trim_in is None else parse_timecode(trim_in, clip.frame_rate)
    next_out = clip.trim_out if trim_out is None else parse_timecode(trim_out, clip.frame_rate)
    updated = EditableClip(
        **{
            **clip.__dict__,
            "trim_in": next_in,
            "trim_out": next_out,
        }
    )
    result = replace_clip(timeline, updated)
    errors = validate_timeline(result)
    if errors:
        raise TimelineError("\n".join(errors))
    return result


def set_enabled(timeline: EditableTimeline, clip_id: str, enabled: bool) -> EditableTimeline:
    clip = require_clip(timeline, clip_id)
    updated = EditableClip(**{**clip.__dict__, "enabled": enabled})
    result = replace_clip(timeline, updated)
    errors = validate_timeline(result)
    if errors:
        raise TimelineError("\n".join(errors))
    return result


def set_note(timeline: EditableTimeline, clip_id: str, note: str) -> EditableTimeline:
    clip = require_clip(timeline, clip_id)
    return replace_clip(timeline, EditableClip(**{**clip.__dict__, "note": note}))


def move_clip(timeline: EditableTimeline, clip_id: str, before: str | None, after: str | None) -> EditableTimeline:
    if bool(before) == bool(after):
        raise TimelineError("--before または --after のどちらか1つを指定してください。")
    moving = require_clip(timeline, clip_id)
    target_id = before or after
    if target_id == clip_id:
        raise TimelineError("自分自身の前後には移動できません。")
    require_clip(timeline, target_id or "")

    remaining = [clip for clip in timeline.clips if clip.id != clip_id]
    target_index = next(index for index, clip in enumerate(remaining) if clip.id == target_id)
    insert_at = target_index if before else target_index + 1
    next_clips = remaining[:insert_at] + [moving] + remaining[insert_at:]
    return EditableTimeline(
        project_name=timeline.project_name,
        media_folder=timeline.media_folder,
        sequence_width=timeline.sequence_width,
        sequence_height=timeline.sequence_height,
        sequence_frame_rate=timeline.sequence_frame_rate,
        clips=next_clips,
    )


def export_timeline(timeline: EditableTimeline, output_path: Path) -> Path:
    errors = validate_timeline(timeline)
    if errors:
        raise TimelineError("\n".join(errors))
    clips = [
        TimelineClip(
            path=clip.path,
            asset_duration=clip.original_duration,
            source_start=clip.trim_in,
            timeline_duration=clip.timeline_duration,
            width=clip.width,
            height=clip.height,
            frame_rate=clip.frame_rate,
            has_audio=clip.has_audio,
            name=clip.name,
            audio_channels=clip.audio_channels,
            audio_rate=clip.audio_rate,
        )
        for clip in timeline.clips
        if clip.enabled
    ]
    return write_fcpxml_timeline(clips, output_path, project_name=timeline.project_name)


def make_show_table(timeline: EditableTimeline) -> str:
    rows = [["id", "on", "start", "dur", "in", "out", "name", "thumb", "note"]]
    offset = Fraction(0, 1)
    for clip in timeline.clips:
        enabled = "yes" if clip.enabled else "no"
        start = format_time(offset) if clip.enabled else "-"
        duration = format_time(clip.timeline_duration)
        rows.append(
            [
                clip.id,
                enabled,
                start,
                duration,
                format_time(clip.trim_in),
                format_time(clip.trim_out),
                clip.name,
                clip.thumbnail or "",
                clip.note,
            ]
        )
        if clip.enabled:
            offset += clip.timeline_duration
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    lines = []
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


def generate_thumbnails(timeline: EditableTimeline, timeline_path: Path, ffmpeg_path: str = "ffmpeg") -> EditableTimeline:
    thumb_dir = timeline_path.parent / ".setlog" / "thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    next_clips = []
    for clip in timeline.clips:
        capture_at = clip.trim_in + clip.timeline_duration / 2
        thumb_path = thumb_dir / f"{clip.id}.jpg"
        command = [
            ffmpeg_path,
            "-y",
            "-ss",
            f"{float(capture_at):.6f}",
            "-i",
            str(clip.path if clip.path.exists() else clip.source_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(thumb_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise TimelineError("ffmpeg が見つかりません。Homebrew などで ffmpeg をインストールしてください。") from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            raise TimelineError(f"{clip.id} のサムネイル生成に失敗しました: {detail}") from exc
        next_clips.append(EditableClip(**{**clip.__dict__, "thumbnail": str(thumb_path)}))
    return EditableTimeline(
        project_name=timeline.project_name,
        media_folder=timeline.media_folder,
        sequence_width=timeline.sequence_width,
        sequence_height=timeline.sequence_height,
        sequence_frame_rate=timeline.sequence_frame_rate,
        clips=next_clips,
    )


def generate_preview_image(
    clip: EditableClip,
    timeline_path: Path,
    ffmpeg_path: str = "ffmpeg",
    width: int = 640,
) -> Path:
    preview_dir = timeline_path.parent / ".setlog" / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / f"{clip.id}.png"
    with tempfile.NamedTemporaryFile(prefix=f"{clip.id}.", suffix=".png", dir=preview_dir, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
    capture_at = clip.trim_in + clip.timeline_duration / 2
    command = [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{float(capture_at):.6f}",
        "-i",
        str(clip.path if clip.path.exists() else clip.source_path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-1",
        str(temp_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        temp_path.replace(preview_path)
    except FileNotFoundError as exc:
        temp_path.unlink(missing_ok=True)
        raise TimelineError("ffmpeg が見つかりません。Homebrew などで ffmpeg をインストールしてください。") from exc
    except subprocess.CalledProcessError as exc:
        temp_path.unlink(missing_ok=True)
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise TimelineError(f"{clip.id} のプレビュー生成に失敗しました: {detail}") from exc
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise
    return preview_path
