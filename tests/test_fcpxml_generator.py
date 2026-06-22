from __future__ import annotations

import tempfile
import unittest
import json
from subprocess import CompletedProcess
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from fcpxml_generator import (
    MediaClip,
    build_fcpxml,
    find_video_files,
    probe_media,
    seconds_to_fcpx_time,
)


class FcpxmlGeneratorTests(unittest.TestCase):
    def test_find_video_files_uses_natural_sort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ["10.mp4", "1.mp4", "note.txt", "2.mov"]:
                (folder / name).write_text("", encoding="utf-8")

            self.assertEqual(
                [path.name for path in find_video_files(folder)],
                ["1.mp4", "2.mov", "10.mp4"],
            )

    def test_seconds_to_fcpx_time_keeps_rational_values(self) -> None:
        self.assertEqual(seconds_to_fcpx_time(Fraction(1001, 30000)), "1001/30000s")
        self.assertEqual(seconds_to_fcpx_time(Fraction(2, 1)), "2s")

    def test_probe_media_detects_rotation_and_display_dimensions(self) -> None:
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "duration": "3",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30/1",
                    "side_data_list": [{"rotation": -90}],
                }
            ],
            "format": {"duration": "3"},
        }

        with patch(
            "fcpxml_generator.subprocess.run",
            return_value=CompletedProcess(["ffprobe"], 0, json.dumps(payload), ""),
        ):
            clip = probe_media(Path("/tmp/rotated.mp4"))

        self.assertEqual(clip.rotation, 270)
        self.assertEqual(clip.width, 1080)
        self.assertEqual(clip.height, 1920)

    def test_build_fcpxml_outputs_parseable_timeline(self) -> None:
        clips = [
            MediaClip(
                Path("/tmp/素材 1.mp4"),
                Fraction(3, 1),
                1920,
                1080,
                Fraction(30, 1),
                True,
                2,
                48000,
            ),
            MediaClip(
                Path("/tmp/clip10.mp4"),
                Fraction(5, 2),
                1920,
                1080,
                Fraction(30, 1),
                False,
            ),
        ]

        xml_text = build_fcpxml(clips, project_name="timeline")
        root = ET.fromstring(xml_text)
        assets = root.findall("./resources/asset")
        asset_clips = root.findall("./library/event/project/sequence/spine/asset-clip")

        self.assertEqual(root.attrib["version"], "1.10")
        self.assertEqual(len(assets), 2)
        self.assertEqual(len(asset_clips), 2)
        self.assertIn(
            "%E7%B4%A0%E6%9D%90%201.mp4", assets[0].find("media-rep").attrib["src"]
        )
        self.assertEqual(asset_clips[0].attrib["offset"], "0s")
        self.assertEqual(asset_clips[1].attrib["offset"], "3s")
        self.assertEqual(
            root.find("./library/event/project/sequence").attrib["duration"], "11/2s"
        )


if __name__ == "__main__":
    unittest.main()
