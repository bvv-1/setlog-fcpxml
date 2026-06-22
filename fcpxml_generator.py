from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable
from xml.dom import minidom
from xml.etree import ElementTree as ET


VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}
FCPXML_VERSION = "1.10"


@dataclass(frozen=True)
class MediaClip:
    path: Path
    duration: Fraction
    width: int
    height: int
    frame_rate: Fraction
    has_audio: bool
    audio_channels: int | None = None
    audio_rate: int | None = None
    rotation: int = 0


@dataclass(frozen=True)
class TimelineClip:
    path: Path
    asset_duration: Fraction
    source_start: Fraction
    timeline_duration: Fraction
    width: int
    height: int
    frame_rate: Fraction
    has_audio: bool
    name: str | None = None
    audio_channels: int | None = None
    audio_rate: int | None = None


class MediaProbeError(RuntimeError):
    pass


def natural_sort_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", path.name.casefold())
    return [int(part) if part.isdigit() else part for part in parts]


def find_video_files(folder: Path) -> list[Path]:
    return sorted(
        (
            item
            for item in folder.iterdir()
            if item.is_file() and item.suffix.casefold() in VIDEO_EXTENSIONS
        ),
        key=natural_sort_key,
    )


def parse_rate(value: str | None, default: Fraction = Fraction(30, 1)) -> Fraction:
    if not value or value == "0/0":
        return default
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return default


def normalize_rotation(value: object) -> int:
    try:
        rotation = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return rotation % 360


def detect_video_rotation(video_stream: dict[str, object]) -> int:
    side_data = video_stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, dict) and "rotation" in item:
                return normalize_rotation(item.get("rotation"))

    tags = video_stream.get("tags")
    if isinstance(tags, dict) and "rotate" in tags:
        return normalize_rotation(tags.get("rotate"))

    return 0


def seconds_to_fcpx_time(seconds: float | str | Fraction) -> str:
    fraction = seconds if isinstance(seconds, Fraction) else Fraction(str(seconds))
    fraction = fraction.limit_denominator(1_000_000)
    if fraction.denominator == 1:
        return f"{fraction.numerator}s"
    return f"{fraction.numerator}/{fraction.denominator}s"


def frame_duration_for_rate(frame_rate: Fraction) -> str:
    if frame_rate <= 0:
        frame_rate = Fraction(30, 1)
    return seconds_to_fcpx_time(Fraction(frame_rate.denominator, frame_rate.numerator))


def probe_media(path: Path, ffprobe_path: str = "ffprobe") -> MediaClip:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise MediaProbeError(
            "ffprobe が見つかりません。Homebrew などで ffmpeg をインストールしてください。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise MediaProbeError(f"{path.name} の解析に失敗しました: {detail}") from exc

    data = json.loads(completed.stdout)
    streams = data.get("streams", [])
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    if not video_stream:
        raise MediaProbeError(f"{path.name} に動画ストリームが見つかりません。")

    audio_streams = [
        stream for stream in streams if stream.get("codec_type") == "audio"
    ]
    duration = (
        video_stream.get("duration") or data.get("format", {}).get("duration") or "0"
    )
    duration_fraction = Fraction(str(duration)).limit_denominator(1_000_000)
    if duration_fraction <= 0:
        raise MediaProbeError(f"{path.name} の尺を取得できません。")

    audio_channels = None
    audio_rate = None
    if audio_streams:
        first_audio = audio_streams[0]
        audio_channels = int(first_audio.get("channels") or 0) or None
        sample_rate = first_audio.get("sample_rate")
        audio_rate = int(sample_rate) if sample_rate else None

    rotation = detect_video_rotation(video_stream)
    width = int(video_stream.get("width") or 1920)
    height = int(video_stream.get("height") or 1080)
    if rotation in {90, 270}:
        width, height = height, width

    return MediaClip(
        path=path.resolve(),
        duration=duration_fraction,
        width=width,
        height=height,
        frame_rate=parse_rate(
            video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
        ),
        has_audio=bool(audio_streams),
        audio_channels=audio_channels,
        audio_rate=audio_rate,
        rotation=rotation,
    )


def probe_folder(folder: Path, ffprobe_path: str = "ffprobe") -> list[MediaClip]:
    files = find_video_files(folder)
    if not files:
        extensions = ", ".join(sorted(VIDEO_EXTENSIONS))
        raise MediaProbeError(f"素材フォルダに対象動画がありません: {extensions}")
    return [probe_media(path, ffprobe_path=ffprobe_path) for path in files]


def build_fcpxml(clips: Iterable[MediaClip], project_name: str = "timeline") -> str:
    export_clips = [
        TimelineClip(
            path=clip.path,
            asset_duration=clip.duration,
            source_start=Fraction(0, 1),
            timeline_duration=clip.duration,
            width=clip.width,
            height=clip.height,
            frame_rate=clip.frame_rate,
            has_audio=clip.has_audio,
            name=clip.path.stem,
            audio_channels=clip.audio_channels,
            audio_rate=clip.audio_rate,
        )
        for clip in clips
    ]
    return build_fcpxml_timeline(export_clips, project_name=project_name)


def build_fcpxml_timeline(
    clips: Iterable[TimelineClip], project_name: str = "timeline"
) -> str:
    clip_list = list(clips)
    if not clip_list:
        raise ValueError("少なくとも1本のクリップが必要です。")

    first_clip = clip_list[0]
    total_duration = sum((clip.timeline_duration for clip in clip_list), Fraction(0, 1))

    fcpxml = ET.Element("fcpxml", {"version": FCPXML_VERSION})
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": f"FFVideoFormat{first_clip.width}x{first_clip.height}",
            "frameDuration": frame_duration_for_rate(first_clip.frame_rate),
            "width": str(first_clip.width),
            "height": str(first_clip.height),
        },
    )

    for index, clip in enumerate(clip_list, start=2):
        attributes = {
            "id": f"r{index}",
            "name": clip.name or clip.path.stem,
            "uid": f"clip-{index - 1}",
            "start": "0s",
            "duration": seconds_to_fcpx_time(clip.asset_duration),
            "hasVideo": "1",
            "format": "r1",
        }
        if clip.has_audio:
            attributes["hasAudio"] = "1"
            attributes["audioSources"] = "1"
            if clip.audio_channels:
                attributes["audioChannels"] = str(clip.audio_channels)
            if clip.audio_rate:
                attributes["audioRate"] = str(clip.audio_rate)
        asset = ET.SubElement(resources, "asset", attributes)
        ET.SubElement(
            asset,
            "media-rep",
            {
                "kind": "original-media",
                "src": clip.path.as_uri(),
            },
        )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": project_name})
    project = ET.SubElement(event, "project", {"name": project_name})
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": seconds_to_fcpx_time(total_duration),
            "tcStart": "0s",
            "tcFormat": "NDF",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    offset = Fraction(0, 1)
    for index, clip in enumerate(clip_list, start=2):
        ET.SubElement(
            spine,
            "asset-clip",
            {
                "name": clip.name or clip.path.stem,
                "ref": f"r{index}",
                "offset": seconds_to_fcpx_time(offset),
                "start": seconds_to_fcpx_time(clip.source_start),
                "duration": seconds_to_fcpx_time(clip.timeline_duration),
            },
        )
        offset += clip.timeline_duration

    rough_xml = ET.tostring(fcpxml, encoding="utf-8")
    pretty_xml = minidom.parseString(rough_xml).toprettyxml(
        indent="  ", encoding="utf-8"
    )
    return pretty_xml.decode("utf-8")


def write_fcpxml(
    clips: Iterable[MediaClip], output_path: Path, project_name: str = "timeline"
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_fcpxml(clips, project_name=project_name), encoding="utf-8"
    )
    return output_path


def write_fcpxml_timeline(
    clips: Iterable[TimelineClip],
    output_path: Path,
    project_name: str = "timeline",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_fcpxml_timeline(clips, project_name=project_name), encoding="utf-8"
    )
    return output_path
