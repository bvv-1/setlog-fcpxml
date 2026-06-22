from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fcpxml_generator import MediaProbeError
from main import main as gui_main
from timeline_edit import (
    TimelineError,
    cleanup_intermediate_files,
    ensure_normalized_media,
    export_timeline,
    generate_thumbnails,
    load_timeline,
    make_show_table,
    move_clip,
    save_timeline,
    scan_timeline,
    set_enabled,
    set_note,
    trim_clip,
    validate_timeline,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setlog-fcpxml",
        description="素材フォルダから編集用timeline.yamlを作り、DaVinci Resolve向けFCPXMLへ出力します。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="素材フォルダを解析してtimeline.yamlを作成")
    scan.add_argument("media_folder", type=Path)
    scan.add_argument("-o", "--output", type=Path, default=Path("timeline.yaml"))
    scan.add_argument("--project-name", default="timeline")

    show = subparsers.add_parser("show", help="timeline.yamlの編集内容を一覧表示")
    show.add_argument("timeline", type=Path)

    trim = subparsers.add_parser("trim", help="クリップのin/outを変更")
    trim.add_argument("timeline", type=Path)
    trim.add_argument("clip_id")
    trim.add_argument("--in", dest="trim_in")
    trim.add_argument("--out", dest="trim_out")

    enable = subparsers.add_parser("enable", help="クリップを採用")
    enable.add_argument("timeline", type=Path)
    enable.add_argument("clip_id")

    disable = subparsers.add_parser("disable", help="クリップを不採用")
    disable.add_argument("timeline", type=Path)
    disable.add_argument("clip_id")

    move = subparsers.add_parser("move", help="クリップを指定位置へ移動")
    move.add_argument("timeline", type=Path)
    move.add_argument("clip_id")
    move.add_argument("--before")
    move.add_argument("--after")

    note = subparsers.add_parser("note", help="クリップにメモを保存")
    note.add_argument("timeline", type=Path)
    note.add_argument("clip_id")
    note.add_argument("text")

    thumbs = subparsers.add_parser("thumbs", help="必要時にサムネイルを生成")
    thumbs.add_argument("timeline", type=Path)

    export = subparsers.add_parser("export", help="timeline.yamlからFCPXMLを出力")
    export.add_argument("timeline", type=Path)
    export.add_argument("-o", "--output", type=Path, default=Path("timeline.fcpxml"))

    validate = subparsers.add_parser("validate", help="timeline.yamlの整合性を検証")
    validate.add_argument("timeline", type=Path)

    clean = subparsers.add_parser("clean", help=".setlog配下のキャッシュなど途中生成物を削除")
    clean.add_argument("timeline", type=Path)
    clean.add_argument("--include-exports", action="store_true", help="timeline.fcpxml も削除")

    subparsers.add_parser("gui", help="素材フォルダから編集GUIを起動")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            timeline = scan_timeline(args.media_folder, project_name=args.project_name)
            timeline = ensure_normalized_media(timeline, args.output)
            save_timeline(timeline, args.output)
            print(f"{args.output} を作成しました。クリップ数: {len(timeline.clips)}")
            return 0
        if args.command == "show":
            print(make_show_table(load_timeline(args.timeline)))
            return 0
        if args.command == "trim":
            timeline = ensure_normalized_media(load_timeline(args.timeline), args.timeline)
            timeline = trim_clip(timeline, args.clip_id, args.trim_in, args.trim_out)
            save_timeline(timeline, args.timeline)
            print(f"{args.clip_id} のtrimを更新しました。")
            return 0
        if args.command == "enable":
            timeline = ensure_normalized_media(load_timeline(args.timeline), args.timeline)
            timeline = set_enabled(timeline, args.clip_id, True)
            save_timeline(timeline, args.timeline)
            print(f"{args.clip_id} をenabledにしました。")
            return 0
        if args.command == "disable":
            timeline = ensure_normalized_media(load_timeline(args.timeline), args.timeline)
            timeline = set_enabled(timeline, args.clip_id, False)
            save_timeline(timeline, args.timeline)
            print(f"{args.clip_id} をdisabledにしました。")
            return 0
        if args.command == "move":
            timeline = ensure_normalized_media(load_timeline(args.timeline), args.timeline)
            timeline = move_clip(timeline, args.clip_id, args.before, args.after)
            save_timeline(timeline, args.timeline)
            print(f"{args.clip_id} を移動しました。")
            return 0
        if args.command == "note":
            timeline = ensure_normalized_media(load_timeline(args.timeline), args.timeline)
            timeline = set_note(timeline, args.clip_id, args.text)
            save_timeline(timeline, args.timeline)
            print(f"{args.clip_id} のnoteを更新しました。")
            return 0
        if args.command == "thumbs":
            timeline = ensure_normalized_media(load_timeline(args.timeline), args.timeline)
            timeline = generate_thumbnails(timeline, args.timeline)
            save_timeline(timeline, args.timeline)
            print(".setlog/thumbs にサムネイルを生成しました。")
            return 0
        if args.command == "export":
            timeline = ensure_normalized_media(load_timeline(args.timeline), args.timeline)
            save_timeline(timeline, args.timeline)
            export_timeline(timeline, args.output)
            print(f"{args.output} を出力しました。")
            return 0
        if args.command == "validate":
            timeline = ensure_normalized_media(load_timeline(args.timeline), args.timeline)
            save_timeline(timeline, args.timeline)
            errors = validate_timeline(timeline)
            if errors:
                for error in errors:
                    print(error, file=sys.stderr)
                return 1
            print("OK")
            return 0
        if args.command == "clean":
            removed = cleanup_intermediate_files(args.timeline, include_exports=args.include_exports)
            if removed:
                for path in removed:
                    print(f"削除しました: {path}")
            else:
                print("削除対象の途中生成物はありませんでした。")
            return 0
        if args.command == "gui":
            gui_main()
            return 0
    except (TimelineError, MediaProbeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
