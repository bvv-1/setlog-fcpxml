from __future__ import annotations

import tempfile
import unittest
from subprocess import CompletedProcess
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from timeline_edit import (
    EditableClip,
    EditableTimeline,
    clear_clip_rotation_cache,
    cleanup_intermediate_files,
    ensure_normalized_media,
    export_timeline,
    format_edit_time,
    generate_timeline_preview_clip,
    load_timeline,
    move_clip,
    parse_timecode,
    save_timeline,
    set_enabled,
    set_rotation,
    trim_clip,
    validate_timeline,
)


def make_timeline(tmp: Path) -> EditableTimeline:
    first = tmp / "1.mp4"
    second = tmp / "2.mp4"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")
    return EditableTimeline(
        project_name="timeline",
        media_folder=tmp,
        sequence_width=1920,
        sequence_height=1080,
        sequence_frame_rate=Fraction(30, 1),
        clips=[
            EditableClip(
                "c001",
                first,
                Fraction(10, 1),
                Fraction(0, 1),
                Fraction(10, 1),
                1920,
                1080,
                Fraction(30, 1),
                True,
                "1",
            ),
            EditableClip(
                "c002",
                second,
                Fraction(8, 1),
                Fraction(0, 1),
                Fraction(8, 1),
                1920,
                1080,
                Fraction(30, 1),
                True,
                "2",
            ),
        ],
    )


class TimelineEditTests(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "timeline.yaml"
            timeline = make_timeline(Path(tmpdir))

            save_timeline(timeline, path)
            loaded = load_timeline(path)

            self.assertEqual(loaded.clips[0].id, "c001")
            self.assertEqual(loaded.clips[1].original_duration, Fraction(8, 1))
            self.assertEqual(loaded.clips[0].original_path, Path(tmpdir) / "1.mp4")
            self.assertEqual(loaded.clips[0].rotation, 0)

    def test_load_legacy_timeline_marks_rotation_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "timeline.yaml"
            save_timeline(make_timeline(Path(tmpdir)), path)
            data = path.read_text(encoding="utf-8")
            data = data.replace(
                '      "original_path": "' + str(Path(tmpdir) / "1.mp4") + '",\n', ""
            )
            data = data.replace('      "rotation": 0,\n', "")
            data = data.replace('      "normalized_path": null,\n', "")
            path.write_text(data, encoding="utf-8")

            loaded = load_timeline(path)

            self.assertFalse(loaded.clips[0].rotation_checked)
            self.assertEqual(loaded.clips[0].original_path, Path(tmpdir) / "1.mp4")

    def test_parse_timecode_variants(self) -> None:
        self.assertEqual(parse_timecode("1.25"), Fraction(5, 4))
        self.assertEqual(parse_timecode("00:00:01.250"), Fraction(5, 4))
        self.assertEqual(parse_timecode("00:00:01:15", Fraction(30, 1)), Fraction(3, 2))

    def test_format_edit_time_preserves_microsecond_duration(self) -> None:
        duration = Fraction(1039933, 1000000)

        self.assertEqual(parse_timecode(format_edit_time(duration)), duration)

    def test_trim_disable_move_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline = make_timeline(Path(tmpdir))
            timeline = trim_clip(timeline, "c001", "1", "3")
            timeline = set_enabled(timeline, "c002", False)
            timeline = move_clip(timeline, "c001", after="c002", before=None)
            output = Path(tmpdir) / "timeline.fcpxml"

            self.assertEqual(validate_timeline(timeline), [])
            export_timeline(timeline, output)
            root = ET.fromstring(output.read_text(encoding="utf-8"))
            asset_clips = root.findall(
                "./library/event/project/sequence/spine/asset-clip"
            )

            self.assertEqual([clip.id for clip in timeline.clips], ["c002", "c001"])
            self.assertEqual(len(asset_clips), 1)
            self.assertEqual(asset_clips[0].attrib["start"], "1s")
            self.assertEqual(asset_clips[0].attrib["duration"], "2s")

    def test_ensure_normalized_media_writes_rotated_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            source = base / "rotated.mp4"
            source.write_text("source", encoding="utf-8")
            timeline = EditableTimeline(
                project_name="timeline",
                media_folder=base,
                sequence_width=1920,
                sequence_height=1080,
                sequence_frame_rate=Fraction(30, 1),
                clips=[
                    EditableClip(
                        "c001",
                        source,
                        Fraction(10, 1),
                        Fraction(0, 1),
                        Fraction(10, 1),
                        1920,
                        1080,
                        Fraction(30, 1),
                        True,
                        "rotated",
                        original_path=source,
                        rotation=180,
                    )
                ],
            )

            def fake_run(
                command: list[str], **_kwargs: object
            ) -> CompletedProcess[str]:
                Path(command[-1]).write_text("normalized", encoding="utf-8")
                return CompletedProcess(command, 0, "", "")

            with patch("timeline_edit.subprocess.run", side_effect=fake_run) as run:
                normalized = ensure_normalized_media(timeline, base / "timeline.yaml")

            clip = normalized.clips[0]
            self.assertEqual(clip.path, base / ".setlog" / "normalized" / "c001.mp4")
            self.assertEqual(clip.normalized_path, clip.path)
            self.assertTrue(clip.path.exists())
            command = run.call_args_list[0].args[0]
            self.assertIn("-noautorotate", command)
            self.assertEqual(command[command.index("-display_rotation") + 1], "0")
            self.assertEqual(command[command.index("-vf") + 1], "hflip,vflip")

    def test_export_uses_normalized_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            source = base / "1.mp4"
            normalized = base / ".setlog" / "normalized" / "c001.mp4"
            source.write_text("", encoding="utf-8")
            normalized.parent.mkdir(parents=True)
            normalized.write_text("", encoding="utf-8")
            timeline = EditableTimeline(
                project_name="timeline",
                media_folder=base,
                sequence_width=1920,
                sequence_height=1080,
                sequence_frame_rate=Fraction(30, 1),
                clips=[
                    EditableClip(
                        "c001",
                        normalized,
                        Fraction(10, 1),
                        Fraction(0, 1),
                        Fraction(10, 1),
                        1920,
                        1080,
                        Fraction(30, 1),
                        True,
                        "1",
                        original_path=source,
                        rotation=180,
                        normalized_path=normalized,
                    )
                ],
            )
            output = base / "timeline.fcpxml"

            export_timeline(timeline, output)
            root = ET.fromstring(output.read_text(encoding="utf-8"))
            media_rep = root.find("./resources/asset/media-rep")

            self.assertIsNotNone(media_rep)
            self.assertEqual(media_rep.attrib["src"], normalized.as_uri())

    def test_set_rotation_resets_proxy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            source = base / "1.mp4"
            normalized = base / ".setlog" / "normalized" / "c001.mp4"
            source.write_text("", encoding="utf-8")
            timeline = EditableTimeline(
                project_name="timeline",
                media_folder=base,
                sequence_width=1920,
                sequence_height=1080,
                sequence_frame_rate=Fraction(30, 1),
                clips=[
                    EditableClip(
                        "c001",
                        normalized,
                        Fraction(10, 1),
                        Fraction(0, 1),
                        Fraction(10, 1),
                        1920,
                        1080,
                        Fraction(30, 1),
                        True,
                        "1",
                        original_path=source,
                        rotation=0,
                        normalized_path=normalized,
                    )
                ],
            )

            updated = set_rotation(timeline, "c001", 90)

            clip = updated.clips[0]
            self.assertEqual(clip.path, source)
            self.assertEqual(clip.rotation, 90)
            self.assertIsNone(clip.normalized_path)
            self.assertTrue(clip.rotation_checked)
            self.assertTrue(clip.force_normalized)

    def test_clear_clip_rotation_cache_removes_clip_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            timeline_path = base / "timeline.yaml"
            targets = [
                base / ".setlog" / "normalized" / "c021.mp4",
                base / ".setlog" / "previews" / "c021.preview.mp4",
                base / ".setlog" / "previews" / "c021.preview.json",
                base / ".setlog" / "previews" / "c021.png",
                base / ".setlog" / "thumbs" / "c021.jpg",
            ]
            untouched = base / ".setlog" / "previews" / "c022.preview.mp4"
            for path in [*targets, untouched]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("cache", encoding="utf-8")

            removed = clear_clip_rotation_cache(timeline_path, "c021")

            self.assertEqual(removed, targets)
            self.assertTrue(untouched.exists())
            for path in targets:
                self.assertFalse(path.exists())

    def test_timeline_preview_clip_uses_existing_normalized_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            source = base / "1.mp4"
            normalized = base / ".setlog" / "normalized" / "c004.mp4"
            source.write_text("source", encoding="utf-8")
            normalized.parent.mkdir(parents=True)
            normalized.write_text("normalized", encoding="utf-8")
            clip = EditableClip(
                "c004",
                normalized,
                Fraction(10, 1),
                Fraction(1, 1),
                Fraction(4, 1),
                1080,
                1920,
                Fraction(30, 1),
                False,
                "1",
                original_path=source,
                rotation=90,
                normalized_path=normalized,
            )

            def fake_run(
                command: list[str], **_kwargs: object
            ) -> CompletedProcess[str]:
                Path(command[-1]).write_text("preview", encoding="utf-8")
                return CompletedProcess(command, 0, "", "")

            with patch("timeline_edit.subprocess.run", side_effect=fake_run) as run:
                preview = generate_timeline_preview_clip(
                    clip,
                    base / "timeline.yaml",
                    1920,
                    1080,
                    Fraction(30, 1),
                )

            command = run.call_args.args[0]
            self.assertEqual(
                preview, base / ".setlog" / "previews" / "c004.preview.mp4"
            )
            self.assertEqual(command[command.index("-i") + 1], str(normalized))
            self.assertEqual(command[command.index("-ss") + 1], "1.000000")
            self.assertEqual(command[command.index("-t") + 1], "3.000000")
            self.assertTrue(
                (base / ".setlog" / "previews" / "c004.preview.json").exists()
            )

    def test_invalid_trim_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline = make_timeline(Path(tmpdir))
            with self.assertRaisesRegex(Exception, "out"):
                trim_clip(timeline, "c001", "4", "2")

    def test_cleanup_intermediate_files_removes_setlog_without_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            timeline_path = base / "timeline.yaml"
            fcpxml_path = base / "timeline.fcpxml"
            cache_file = base / ".setlog" / "previews" / "c001.png"
            save_timeline(make_timeline(base), timeline_path)
            fcpxml_path.write_text("<fcpxml />", encoding="utf-8")
            cache_file.parent.mkdir(parents=True)
            cache_file.write_text("cache", encoding="utf-8")

            removed = cleanup_intermediate_files(timeline_path)

            self.assertEqual(removed, [base / ".setlog"])
            self.assertTrue(timeline_path.exists())
            self.assertTrue(fcpxml_path.exists())
            self.assertFalse((base / ".setlog").exists())

    def test_cleanup_intermediate_files_can_remove_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            timeline_path = base / "timeline.yaml"
            fcpxml_path = base / "timeline.fcpxml"
            save_timeline(make_timeline(base), timeline_path)
            fcpxml_path.write_text("<fcpxml />", encoding="utf-8")

            removed = cleanup_intermediate_files(timeline_path, include_exports=True)

            self.assertEqual(removed, [fcpxml_path])
            self.assertTrue(timeline_path.exists())
            self.assertFalse(fcpxml_path.exists())


if __name__ == "__main__":
    unittest.main()
