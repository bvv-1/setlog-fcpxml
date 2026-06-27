from fractions import Fraction
from pathlib import Path

from main import clip_duration_distribution
from timeline_edit import EditableClip


def make_clip(clip_id: str, duration: int) -> EditableClip:
    return EditableClip(
        id=clip_id,
        path=Path(f"{clip_id}.mov"),
        original_duration=Fraction(duration),
        trim_in=Fraction(0),
        trim_out=Fraction(duration),
        width=1920,
        height=1080,
        frame_rate=Fraction(30),
        has_audio=True,
        name=f"{clip_id}.mov",
    )


def test_clip_duration_distribution_finds_outlier_at_1_5_sigma() -> None:
    clips = [
        make_clip(f"c{index}", duration) for index, duration in enumerate([1, 1, 1, 10])
    ]

    mean, standard_deviation, outlier_ids = clip_duration_distribution(clips, 1.5)

    assert mean == 3.25
    assert standard_deviation > 0
    assert outlier_ids == {"c3"}


def test_clip_duration_distribution_has_no_outliers_for_equal_lengths() -> None:
    clips = [make_clip("c1", 5), make_clip("c2", 5)]

    mean, standard_deviation, outlier_ids = clip_duration_distribution(clips, 1.5)

    assert mean == 5
    assert standard_deviation == 0
    assert outlier_ids == set()
