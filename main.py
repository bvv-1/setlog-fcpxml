from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

import flet as ft

from fcpxml_generator import MediaProbeError
from timeline_edit import (
    EditableClip,
    EditableTimeline,
    TimelineError,
    clear_clip_rotation_cache,
    cleanup_intermediate_files,
    ensure_normalized_media,
    export_timeline,
    format_edit_time,
    format_time,
    generate_preview_image,
    generate_timeline_preview_clip,
    load_timeline,
    move_clip,
    save_timeline,
    scan_timeline,
    set_enabled,
    set_note,
    set_rotation,
    trim_clip,
)


DEFAULT_MEDIA_FOLDER = Path("/Users/yamada_kentaro/Movies/20260228_0301_fukui")
AUTOSAVE_INTERVAL_SECONDS = 30


def border_all(width: int, color: ft.ColorValue) -> ft.border.Border:
    side = ft.border.BorderSide(width=width, color=color)
    return ft.border.Border(top=side, right=side, bottom=side, left=side)


def padding_symmetric(horizontal: int, vertical: int) -> ft.padding.Padding:
    return ft.padding.Padding(
        left=horizontal,
        top=vertical,
        right=horizontal,
        bottom=vertical,
    )


def padding_only(left: int = 0, top: int = 0, right: int = 0, bottom: int = 0) -> ft.padding.Padding:
    return ft.padding.Padding(left=left, top=top, right=right, bottom=bottom)


class TimelineApp:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.page.title = "Setlog FCPXML Generator"
        self._set_window_min_size()
        self.page.padding = 0
        self.page.spacing = 0
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.on_keyboard_event = self.on_keyboard_event
        self.page.on_close = self.close
        self._set_default_window_size()

        self.timeline_path: Path | None = None
        self.output_folder: Path | None = None
        self.timeline: EditableTimeline | None = None
        self.selected_clip_id: str | None = None
        self.preview_player: subprocess.Popen[str] | None = None
        self.autosave_enabled = False
        self.autosave_timer: threading.Timer | None = None
        self.is_saving = False
        self.is_preview_loading = False
        self.pregenerate_cancel = False

        self.media_folder_field = ft.TextField(
            label="素材フォルダ",
            value=str(DEFAULT_MEDIA_FOLDER),
            expand=True,
            dense=True,
        )
        self.project_name_field = ft.TextField(
            label="プロジェクト名",
            value="timeline",
            expand=True,
            dense=True,
        )
        self.status_text = ft.Text(
            "素材フォルダを選択してください。", selectable=True, expand=True
        )
        self.start_button = ft.FilledButton("OK", on_click=self.start_editing)

        self.editor_status_text = ft.Text(
            "未保存です。保存すると自動保存が有効になります。",
            selectable=True,
            expand=True,
        )
        self.timeline_summary_text = ft.Text("")
        self.timeline_visual = ft.Row(spacing=1, expand=True)
        self.clip_list = ft.ListView(expand=True, spacing=4, padding=0)
        self.clip_detail_text = ft.Text("", selectable=True)
        self.preview_box = ft.Container(
            content=ft.Text("クリップを選択してください。", text_align=ft.TextAlign.CENTER),
            alignment=ft.Alignment(0, 0),
            bgcolor=ft.Colors.BLACK12,
            border=border_all(1, ft.Colors.OUTLINE_VARIANT),
            expand=True,
            padding=12,
        )
        self.clip_enabled_checkbox = ft.Checkbox(label="採用", value=True)
        self.clip_trim_in_field = ft.TextField(label="in", dense=True, expand=True)
        self.clip_trim_out_field = ft.TextField(label="out", dense=True, expand=True)
        self.note_field = ft.TextField(
            label="メモ",
            multiline=True,
            min_lines=4,
            max_lines=6,
            expand=False,
        )

        self.media_folder_picker = ft.FilePicker()
        self.timeline_picker = ft.FilePicker()
        self.output_folder_picker = ft.FilePicker()
        self.pending_save_after_folder_pick = False
        self._register_service(self.media_folder_picker)
        self._register_service(self.timeline_picker)
        self._register_service(self.output_folder_picker)

        self.start_view = self.build_start_view()
        self.editor_view = self.build_editor_view()
        self.show_start_screen()

    def _register_service(self, service: ft.Service) -> None:
        if hasattr(self.page.services, "register_service"):
            self.page.services.register_service(service)
        else:
            self.page.services.append(service)

    def _set_window_min_size(self) -> None:
        try:
            self.page.window.min_width = 900
            self.page.window.min_height = 560
        except Exception:
            self.page.window_min_width = 900
            self.page.window_min_height = 560

    def _set_default_window_size(self) -> None:
        try:
            self.page.window.maximized = True
        except Exception:
            try:
                self.page.window_maximized = True
            except Exception:
                self.page.window_width = 1200
                self.page.window_height = 780

    def build_start_view(self) -> ft.Control:
        return ft.Container(
            padding=20,
            expand=True,
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self.media_folder_field,
                            ft.OutlinedButton(
                                "選択",
                                icon=ft.Icons.FOLDER_OPEN,
                                on_click=self.choose_media_folder,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(controls=[self.project_name_field]),
                    ft.Row(
                        controls=[
                            self.start_button,
                            ft.OutlinedButton(
                                "既存の timeline.yaml を開く",
                                icon=ft.Icons.UPLOAD_FILE,
                                on_click=self.open_timeline,
                            ),
                        ],
                        wrap=True,
                    ),
                    self.status_text,
                ],
                spacing=12,
            ),
        )

    def build_editor_view(self) -> ft.Control:
        toolbar = ft.Row(
            controls=[
                ft.FilledButton(
                    "一時保存", icon=ft.Icons.SAVE, on_click=self.save_project
                ),
                ft.OutlinedButton(
                    "再読み込み",
                    icon=ft.Icons.REFRESH,
                    on_click=self.reload_timeline,
                ),
                ft.OutlinedButton(
                    "連続プレビュー",
                    icon=ft.Icons.PLAY_ARROW,
                    on_click=self.play_timeline_preview,
                ),
                ft.IconButton(
                    icon=ft.Icons.STOP,
                    tooltip="停止",
                    on_click=self.stop_timeline_preview,
                ),
                self.editor_status_text,
                ft.PopupMenuButton(
                    icon=ft.Icons.SETTINGS,
                    tooltip="設定",
                    items=[
                        ft.PopupMenuItem(
                            content="キャッシュを削除",
                            on_click=lambda _e: self.cleanup_cache(),
                        ),
                        ft.PopupMenuItem(
                            content="キャッシュと出力XMLを削除",
                            on_click=lambda _e: self.cleanup_cache(include_exports=True),
                        ),
                    ],
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        timeline_panel = ft.Column(
            controls=[
                self.timeline_summary_text,
                ft.Container(
                    content=self.timeline_visual,
                    height=76,
                    padding=padding_symmetric(horizontal=12, vertical=14),
                    bgcolor=ft.Colors.with_opacity(0.92, ft.Colors.BLACK),
                    border=border_all(1, ft.Colors.GREY_700),
                ),
            ],
            spacing=4,
        )
        detail_panel = ft.Column(
            controls=[
                self.clip_detail_text,
                self.preview_box,
                ft.Row(
                    controls=[
                        self.clip_enabled_checkbox,
                        self.clip_trim_in_field,
                        self.clip_trim_out_field,
                    ],
                    spacing=8,
                ),
                ft.Row(
                    controls=[
                        ft.OutlinedButton(
                            "上へ",
                            icon=ft.Icons.ARROW_UPWARD,
                            on_click=self.move_selected_up,
                        ),
                        ft.OutlinedButton(
                            "下へ",
                            icon=ft.Icons.ARROW_DOWNWARD,
                            on_click=self.move_selected_down,
                        ),
                        ft.FilledButton(
                            "変更を保存",
                            icon=ft.Icons.CHECK,
                            on_click=self.save_selected_edit,
                        ),
                    ],
                    wrap=True,
                ),
                ft.Row(
                    controls=[
                        ft.Text("回転補正"),
                        ft.OutlinedButton(
                            "-90°",
                            icon=ft.Icons.ROTATE_LEFT,
                            on_click=lambda _e: self.rotate_selected_clip_by_delta(-90),
                        ),
                        ft.OutlinedButton(
                            "+90°",
                            icon=ft.Icons.ROTATE_RIGHT,
                            on_click=lambda _e: self.rotate_selected_clip_by_delta(90),
                        ),
                    ],
                    wrap=True,
                ),
                self.note_field,
            ],
            spacing=10,
            expand=True,
        )
        clip_list_header = ft.Container(
            padding=padding_symmetric(horizontal=10, vertical=8),
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=border_all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=6,
            content=ft.Row(
                controls=[
                    ft.Text("ID", width=56, weight=ft.FontWeight.BOLD),
                    ft.Text("採用", width=42, weight=ft.FontWeight.BOLD),
                    ft.Text("開始時間", width=104, weight=ft.FontWeight.BOLD),
                    ft.Text("長さ", width=104, weight=ft.FontWeight.BOLD),
                    ft.Text("クリップ名", expand=True, weight=ft.FontWeight.BOLD),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        body = ft.Row(
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            clip_list_header,
                            self.clip_list,
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    expand=3,
                ),
                ft.VerticalDivider(width=1),
                ft.Container(content=detail_panel, expand=4),
            ],
            expand=True,
        )
        return ft.Container(
            padding=16,
            expand=True,
            content=ft.Column(
                controls=[toolbar, timeline_panel, body],
                spacing=12,
                expand=True,
            ),
        )

    def show_start_screen(self) -> None:
        self.page.controls.clear()
        self.page.add(self.start_view)
        self.page.update()

    def show_editor_screen(self) -> None:
        self.page.controls.clear()
        self.page.add(self.editor_view)
        self.page.update()

    async def choose_media_folder(self, _event: ft.ControlEvent | None = None) -> None:
        folder = await self.media_folder_picker.get_directory_path(
            dialog_title="素材フォルダを選択"
        )
        if folder:
            self.media_folder_field.value = folder
            self.page.update()

    def start_editing(self, _event: ft.ControlEvent | None = None) -> None:
        media_folder = Path(self.media_folder_field.value or "").expanduser()
        project_name = (self.project_name_field.value or "").strip() or "timeline"

        if not media_folder.is_dir():
            self.show_error("入力エラー", "有効な素材フォルダを選択してください。")
            return

        self.start_button.disabled = True
        self.status_text.value = "素材を解析しています..."
        self.page.update()
        threading.Thread(
            target=self._scan_in_background,
            args=(media_folder, project_name),
            daemon=True,
        ).start()

    def _scan_in_background(self, media_folder: Path, project_name: str) -> None:
        try:
            timeline = scan_timeline(media_folder, project_name=project_name)
            self._finish_scan_success(timeline)
        except (OSError, MediaProbeError, TimelineError, ValueError) as exc:
            self._finish_scan_error(str(exc))

    def _finish_scan_success(self, timeline: EditableTimeline) -> None:
        self.start_button.disabled = False
        self.timeline = timeline
        self.timeline_path = None
        self.output_folder = None
        self.autosave_enabled = False
        self.editor_status_text.value = (
            "未保存です。一時保存または Ctrl+S で保存先を選択してください。"
        )
        self.status_text.value = f"クリップ数: {len(timeline.clips)}"
        self.show_editor_screen()
        self.pregenerate_previews()

    def _finish_scan_error(self, message: str) -> None:
        self.start_button.disabled = False
        self.status_text.value = message
        self.page.update()
        self.show_error("読み込み失敗", message)

    async def open_timeline(self, _event: ft.ControlEvent | None = None) -> None:
        files = await self.timeline_picker.pick_files(
            dialog_title="timeline.yaml を選択",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["yaml", "yml", "json"],
            allow_multiple=False,
        )
        if files:
            self.load_timeline_file(Path(files[0].path))

    def reload_timeline(self, _event: ft.ControlEvent | None = None) -> None:
        if not self.timeline_path:
            self.show_error("入力エラー", "保存済みの timeline.yaml がありません。")
            return
        self.load_timeline_file(self.timeline_path)

    def load_timeline_file(self, path: Path) -> None:
        try:
            self.timeline = ensure_normalized_media(load_timeline(path), path)
            save_timeline(self.timeline, path)
            self.timeline_path = path
            self.output_folder = path.parent
            self.project_name_field.value = self.timeline.project_name
            self.media_folder_field.value = str(self.timeline.media_folder)
            self.editor_status_text.value = f"{path} を読み込みました。自動保存はONです。"
            self.show_editor_screen()
            self.pregenerate_previews()
            self.enable_autosave()
        except (OSError, TimelineError) as exc:
            self.show_error("読み込み失敗", str(exc))

    def pregenerate_previews(self) -> None:
        if not self.timeline or not self.timeline.clips:
            self.populate_clip_list(select_id=self.selected_clip_id)
            return

        self.pregenerate_cancel = False
        progress_bar = ft.ProgressBar(width=400, value=0.0)
        progress_text = ft.Text("プレビュー画像を生成しています... (0/0)")

        def on_cancel(_e: ft.ControlEvent | None) -> None:
            self.pregenerate_cancel = True
            self.close_dialog()
            self.populate_clip_list(select_id=self.selected_clip_id)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("プレビュー生成中"),
            content=ft.Column(
                [
                    progress_text,
                    progress_bar,
                ],
                tight=True,
                spacing=10,
            ),
            actions=[
                ft.TextButton("スキップ", on_click=on_cancel),
            ],
        )
        self.open_dialog(dialog)

        threading.Thread(
            target=self._pregenerate_previews_in_background,
            args=(dialog, progress_bar, progress_text),
            daemon=True,
        ).start()

    def _pregenerate_previews_in_background(
        self,
        dialog: ft.AlertDialog,
        progress_bar: ft.ProgressBar,
        progress_text: ft.Text,
    ) -> None:
        import concurrent.futures
        import os

        try:
            if not self.timeline:
                return

            clips = self.timeline.clips
            total = len(clips)
            if total == 0:
                self.close_dialog()
                self.populate_clip_list(select_id=self.selected_clip_id)
                return

            preview_base = self.get_cache_base_path()
            max_workers = min(8, os.cpu_count() or 4)

            completed_count = 0
            update_lock = threading.Lock()

            def process_clip(clip: EditableClip) -> None:
                nonlocal completed_count
                if self.pregenerate_cancel:
                    return

                try:
                    generate_preview_image(clip, preview_base)
                except TimelineError as exc:
                    raise exc
                except Exception:
                    pass

                with update_lock:
                    completed_count += 1
                    progress_text.value = f"プレビュー画像を生成しています... ({completed_count}/{total})"
                    progress_bar.value = completed_count / total
                    self.page.update()

            # 初期表示の更新
            progress_text.value = f"プレビュー画像を生成しています... (0/{total})"
            progress_bar.value = 0.0
            self.page.update()

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(process_clip, clip): clip for clip in clips}

                while futures:
                    if self.pregenerate_cancel:
                        for future in list(futures.keys()):
                            future.cancel()
                        break

                    done, _ = concurrent.futures.wait(
                        futures.keys(), timeout=0.1, return_when=concurrent.futures.FIRST_COMPLETED
                    )

                    for future in done:
                        try:
                            future.result()
                        except TimelineError as exc:
                            self.pregenerate_cancel = True
                            self.close_dialog()
                            self.show_error("プレビュー生成失敗", str(exc))
                            self.populate_clip_list(select_id=self.selected_clip_id)
                            return

                        futures.pop(future)

            if self.pregenerate_cancel:
                return

            self.close_dialog()
            self.populate_clip_list(select_id=self.selected_clip_id)
        except Exception as exc:
            if not self.pregenerate_cancel:
                self.close_dialog()
                self.show_error(
                    "プレビュー生成エラー", f"予期しないエラーが発生しました: {exc}"
                )
                self.populate_clip_list(select_id=self.selected_clip_id)

    def populate_clip_list(self, select_id: str | None = None) -> None:
        if not self.timeline:
            return
        self.clip_list.controls.clear()
        offset = Fraction(0, 1)
        for clip in self.timeline.clips:
            start = format_time(offset) if clip.enabled else "-"
            row = self.build_clip_row(clip, start)
            self.clip_list.controls.append(row)
            if clip.enabled:
                offset += clip.timeline_duration
        if self.timeline.clips:
            valid_ids = {clip.id for clip in self.timeline.clips}
            next_id = select_id if select_id in valid_ids else self.timeline.clips[0].id
            self.select_clip(next_id, load_preview=True)
        self.draw_timeline_visual()
        self.page.update()

    def build_clip_row(self, clip: EditableClip, start: str) -> ft.Control:
        selected = clip.id == self.selected_clip_id
        return ft.Container(
            key=clip.id,
            data=clip.id,
            on_click=lambda _e, clip_id=clip.id: self.select_clip(clip_id),
            padding=padding_symmetric(horizontal=10, vertical=8),
            bgcolor=ft.Colors.PRIMARY_CONTAINER if selected else None,
            border=border_all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=6,
            content=ft.Row(
                controls=[
                    ft.Text(clip.id, width=56, weight=ft.FontWeight.BOLD),
                    ft.Text("yes" if clip.enabled else "no", width=42),
                    ft.Text(start, width=104),
                    ft.Text(format_time(clip.timeline_duration), width=104),
                    ft.Text(clip.name, expand=True, overflow=ft.TextOverflow.ELLIPSIS),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def draw_timeline_visual(self) -> None:
        self.timeline_visual.controls.clear()
        if not self.timeline:
            self.timeline_summary_text.value = ""
            self.timeline_visual.controls.append(
                ft.Container(
                    content=ft.Text("タイムラインを読み込んでください", color=ft.Colors.WHITE70),
                    alignment=ft.Alignment(0, 0),
                    expand=True,
                )
            )
            return

        enabled_clips = [clip for clip in self.timeline.clips if clip.enabled]
        total_duration = sum(
            (clip.timeline_duration for clip in enabled_clips), Fraction(0, 1)
        )
        if total_duration <= 0:
            self.timeline_summary_text.value = "有効なクリップがありません"
            self.timeline_visual.controls.append(
                ft.Container(
                    content=ft.Text("表示できるクリップがありません", color=ft.Colors.WHITE70),
                    alignment=ft.Alignment(0, 0),
                    expand=True,
                )
            )
            return

        self.timeline_summary_text.value = (
            f"タイムライン: {len(enabled_clips)} clips / total {format_time(total_duration)}"
        )
        colors = (
            ft.Colors.BLUE_400,
            ft.Colors.TEAL_400,
            ft.Colors.AMBER_500,
            ft.Colors.PINK_300,
            ft.Colors.LIGHT_GREEN_500,
            ft.Colors.RED_300,
        )
        for index, clip in enumerate(enabled_clips):
            weight = max(1, int(float(clip.timeline_duration / total_duration) * 1000))
            selected = clip.id == self.selected_clip_id
            self.timeline_visual.controls.append(
                ft.Container(
                    expand=weight,
                    height=40,
                    bgcolor=colors[index % len(colors)],
                    border=border_all(
                        3 if selected else 1,
                        ft.Colors.WHITE if selected else ft.Colors.BLACK54,
                    ),
                    padding=padding_only(left=6),
                    alignment=ft.Alignment(-1, 0),
                    content=ft.Text(
                        clip.id,
                        color=ft.Colors.WHITE,
                        max_lines=1,
                        overflow=ft.TextOverflow.CLIP,
                    ),
                    on_click=lambda _e, clip_id=clip.id: self.select_clip(clip_id),
                )
            )

    def select_clip(self, clip_id: str, load_preview: bool = True) -> None:
        if not self.timeline:
            return
        clip = next((item for item in self.timeline.clips if item.id == clip_id), None)
        if not clip:
            return
        self.selected_clip_id = clip.id
        self.clip_enabled_checkbox.value = clip.enabled
        self.clip_trim_in_field.value = format_edit_time(clip.trim_in)
        self.clip_trim_out_field.value = format_edit_time(clip.trim_out)
        self.note_field.value = clip.note
        self.clip_detail_text.value = (
            f"{clip.id}  {clip.name}  in {format_time(clip.trim_in)} / "
            f"out {format_time(clip.trim_out)} / dur {format_time(clip.timeline_duration)} / "
            f"rotation {clip.rotation}"
        )
        self.refresh_clip_selection_styles()
        self.draw_timeline_visual()
        self.page.update()
        if load_preview:
            self._start_preview_loading()
            threading.Thread(
                target=self._load_preview_in_background, args=(clip,), daemon=True
            ).start()

    def refresh_clip_selection_styles(self) -> None:
        for control in self.clip_list.controls:
            if isinstance(control, ft.Container):
                control.bgcolor = (
                    ft.Colors.PRIMARY_CONTAINER
                    if control.data == self.selected_clip_id
                    else None
                )

    def _load_preview_in_background(self, clip: EditableClip) -> None:
        preview_base = self.get_cache_base_path()
        try:
            preview_path = generate_preview_image(clip, preview_base)
            self._show_preview_image(preview_path, clip.id)
        except (OSError, TimelineError) as exc:
            self._show_preview_error(str(exc), clip.id)

    def _show_preview_image(self, preview_path: Path, clip_id: str) -> None:
        if self.selected_clip_id != clip_id:
            return
        self.is_preview_loading = False
        self.preview_box.content = ft.Image(
            src=str(preview_path),
            fit=ft.BoxFit.CONTAIN,
            expand=True,
        )
        self.editor_status_text.value = f"プレビュー: {preview_path}"
        self.page.update()

    def _show_preview_error(self, message: str, clip_id: str | None = None) -> None:
        if clip_id is not None and self.selected_clip_id != clip_id:
            return
        self.is_preview_loading = False
        self.preview_box.content = ft.Text(message, text_align=ft.TextAlign.CENTER)
        self.editor_status_text.value = message
        self.page.update()

    def _start_preview_loading(self) -> None:
        self.is_preview_loading = True
        self.preview_box.content = ft.Column(
            controls=[
                ft.ProgressRing(),
                ft.Text("プレビュー生成中...", text_align=ft.TextAlign.CENTER),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.editor_status_text.value = "プレビュー生成中..."
        self.page.update()

    async def save_selected_edit(self, _event: ft.ControlEvent | None = None) -> None:
        if not self.apply_selected_edit(show_dialogs=True):
            return
        await self.save_project()

    def rotate_selected_clip(self, rotation: int) -> None:
        if not self.timeline or not self.selected_clip_id:
            self.show_error("入力エラー", "クリップを選択してください。")
            return
        if not self.apply_selected_edit(show_dialogs=True):
            return

        clip_id = self.selected_clip_id
        try:
            self.stop_timeline_preview(update_status=False)
            self.timeline = set_rotation(self.timeline, clip_id, rotation)
            clear_clip_rotation_cache(self.get_cache_base_path(), clip_id)
            self.editor_status_text.value = f"{clip_id} の回転補正を {rotation} に更新中..."
            self.page.update()
            self.timeline = ensure_normalized_media(
                self.timeline, self.get_cache_base_path()
            )
            if self.timeline_path and self.output_folder:
                if self.save_project_to_folder(self.output_folder, show_dialogs=True):
                    self.populate_clip_list(select_id=clip_id)
                    self.select_clip(clip_id)
            else:
                self.populate_clip_list(select_id=clip_id)
                self.select_clip(clip_id)
                self.editor_status_text.value = (
                    f"{clip_id} の回転補正を {rotation} に更新しました。未保存です。"
                )
                self.page.update()
        except (OSError, TimelineError, ValueError) as exc:
            self.show_error("回転補正失敗", str(exc))

    def rotate_selected_clip_by_delta(
        self, delta: int, _event: ft.ControlEvent | None = None
    ) -> None:
        clip = self.find_selected_clip()
        if not clip:
            self.show_error("入力エラー", "クリップを選択してください。")
            return
        self.rotate_selected_clip((clip.rotation + delta) % 360)

    def play_timeline_preview(self, _event: ft.ControlEvent | None = None) -> None:
        if not self.timeline:
            self.show_error("入力エラー", "編集するタイムラインがありません。")
            return
        if self.selected_clip_id and not self.apply_selected_edit(show_dialogs=True):
            return

        enabled_clips = [clip for clip in self.timeline.clips if clip.enabled]
        if not enabled_clips:
            self.show_error("入力エラー", "採用中のクリップがありません。")
            return

        self.stop_timeline_preview(update_status=False)
        try:
            self.timeline = ensure_normalized_media(
                self.timeline, self.get_cache_base_path()
            )
            enabled_clips = [clip for clip in self.timeline.clips if clip.enabled]
            concat_path = self.write_preview_concat_file(enabled_clips)
            self.preview_player = subprocess.Popen(
                [
                    "ffplay",
                    "-hide_banner",
                    "-window_title",
                    "Setlog 連続プレビュー",
                    "-autoexit",
                    "-an",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except FileNotFoundError:
            self.show_error(
                "プレビュー失敗",
                "ffplay が見つかりません。Homebrew などで ffmpeg をインストールしてください。",
            )
            return
        except TimelineError as exc:
            self.show_error("プレビュー失敗", str(exc))
            return
        except OSError as exc:
            self.show_error("プレビュー失敗", str(exc))
            return

        threading.Timer(
            0.3, self.focus_timeline_preview_window, args=(self.preview_player.pid,)
        ).start()
        total_duration = sum(
            (clip.timeline_duration for clip in enabled_clips), Fraction(0, 1)
        )
        self.editor_status_text.value = (
            f"連続プレビュー中: {len(enabled_clips)} clips / {format_time(total_duration)}"
        )
        self.page.update()

    def focus_timeline_preview_window(self, process_id: int, attempts: int = 4) -> None:
        if sys.platform != "darwin":
            return
        if (
            not self.preview_player
            or self.preview_player.pid != process_id
            or self.preview_player.poll() is not None
        ):
            return
        script = f"""
        tell application "System Events"
            set frontmost of (first process whose unix id is {process_id}) to true
        end tell
        """
        try:
            completed = subprocess.run(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            completed = None
        if attempts > 1 and (completed is None or completed.returncode != 0):
            threading.Timer(
                0.3,
                self.focus_timeline_preview_window,
                args=(process_id, attempts - 1),
            ).start()

    def stop_timeline_preview(
        self,
        _event: ft.ControlEvent | None = None,
        update_status: bool = True,
    ) -> None:
        if self.preview_player and self.preview_player.poll() is None:
            self.preview_player.terminate()
        self.preview_player = None
        if update_status:
            self.editor_status_text.value = "連続プレビューを停止しました。"
            self.page.update()

    def cleanup_cache(
        self,
        _event: ft.ControlEvent | None = None,
        include_exports: bool = False,
    ) -> None:
        if include_exports and not self.timeline_path:
            self.show_error(
                "削除できません", "出力XMLを削除するには、先に一時保存してください。"
            )
            return

        target_label = ".setlog のキャッシュ"
        if include_exports:
            target_label += " と timeline.fcpxml"

        def confirmed(_event: ft.ControlEvent | None = None) -> None:
            self.close_dialog()
            self.cleanup_cache_confirmed(include_exports=include_exports)

        self.show_confirm(
            "削除確認",
            f"{target_label} を削除します。よろしいですか？",
            confirmed,
        )

    def cleanup_cache_confirmed(self, include_exports: bool = False) -> None:
        self.stop_timeline_preview(update_status=False)
        try:
            removed = cleanup_intermediate_files(
                self.get_cache_base_path(), include_exports=include_exports
            )
        except OSError as exc:
            self.show_error("削除失敗", str(exc))
            return

        self.is_preview_loading = False
        self.preview_box.content = ft.Text(
            "キャッシュを削除しました。クリップを選択すると再生成します。",
            text_align=ft.TextAlign.CENTER,
        )
        count = len(removed)
        if count:
            self.editor_status_text.value = f"{count}件の途中生成物を削除しました。"
        else:
            self.editor_status_text.value = "削除対象の途中生成物はありませんでした。"
        self.page.update()

    def get_cache_base_path(self) -> Path:
        if self.timeline_path:
            return self.timeline_path
        return Path(tempfile.gettempdir()) / "setlog-fcpxml" / "timeline.yaml"

    def close(self, _event: object | None = None) -> None:
        self.stop_timeline_preview(update_status=False)
        if self.autosave_timer:
            self.autosave_timer.cancel()
            self.autosave_timer = None

    def write_preview_concat_file(self, clips: list[EditableClip]) -> Path:
        preview_base = self.get_cache_base_path()
        preview_dir = preview_base.parent / ".setlog" / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        concat_path = preview_dir / "timeline_preview.ffconcat"
        lines = ["ffconcat version 1.0"]
        if not self.timeline:
            raise TimelineError("編集するタイムラインがありません。")
        for clip in clips:
            preview_path = generate_timeline_preview_clip(
                clip,
                preview_base,
                self.timeline.sequence_width,
                self.timeline.sequence_height,
                self.timeline.sequence_frame_rate,
            )
            lines.append(f"file {self.quote_ffconcat_path(preview_path)}")
        concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return concat_path

    @staticmethod
    def quote_ffconcat_path(path: Path) -> str:
        return "'" + str(path).replace("\\", "\\\\").replace("'", "'\\''") + "'"

    def apply_selected_edit(self, show_dialogs: bool) -> bool:
        if not self.timeline or not self.selected_clip_id:
            if show_dialogs:
                self.show_error("入力エラー", "クリップを選択してください。")
            return False
        clip = self.find_selected_clip()
        if not clip:
            if show_dialogs:
                self.show_error("入力エラー", "選択クリップが見つかりません。")
            return False
        try:
            timeline = trim_clip(
                self.timeline,
                clip.id,
                self.clip_trim_in_field.value or "",
                self.clip_trim_out_field.value or "",
            )
            timeline = set_enabled(
                timeline, clip.id, bool(self.clip_enabled_checkbox.value)
            )
            timeline = set_note(timeline, clip.id, (self.note_field.value or "").strip())
            self.timeline = timeline
            self.populate_clip_list(select_id=clip.id)
            return True
        except (TimelineError, ValueError) as exc:
            if show_dialogs:
                self.show_error("保存失敗", str(exc))
            else:
                self.editor_status_text.value = f"自動保存できません: {exc}"
                self.page.update()
            return False

    def find_selected_clip(self) -> EditableClip | None:
        if not self.timeline or not self.selected_clip_id:
            return None
        return next(
            (item for item in self.timeline.clips if item.id == self.selected_clip_id),
            None,
        )

    def move_selected_up(self, _event: ft.ControlEvent | None = None) -> None:
        self.move_selected_clip(-1)

    def move_selected_down(self, _event: ft.ControlEvent | None = None) -> None:
        self.move_selected_clip(1)

    def move_selected_clip(self, direction: int) -> None:
        if not self.timeline or not self.selected_clip_id:
            self.show_error("入力エラー", "クリップを選択してください。")
            return
        if not self.apply_selected_edit(show_dialogs=True):
            return
        ids = [clip.id for clip in self.timeline.clips]
        try:
            index = ids.index(self.selected_clip_id)
        except ValueError:
            self.show_error("入力エラー", "選択クリップが見つかりません。")
            return
        target_index = index + direction
        if target_index < 0 or target_index >= len(ids):
            return
        clip_id = ids[index]
        target_id = ids[target_index]
        try:
            if direction < 0:
                self.timeline = move_clip(
                    self.timeline, clip_id, before=target_id, after=None
                )
            else:
                self.timeline = move_clip(
                    self.timeline, clip_id, before=None, after=target_id
                )
            self.populate_clip_list(select_id=clip_id)
            if self.timeline_path and self.output_folder:
                self.save_project_to_folder(self.output_folder, show_dialogs=True)
            else:
                self.editor_status_text.value = f"{clip_id} を移動しました。未保存です。"
                self.page.update()
        except (OSError, TimelineError) as exc:
            self.show_error("移動失敗", str(exc))

    async def on_keyboard_event(self, event: ft.KeyboardEvent) -> None:
        key = (event.key or "").lower()
        if key == "s" and (event.ctrl or event.meta):
            await self.save_project()
        elif key == "arrow down":
            self.select_next_clip()
        elif key == "arrow up":
            self.select_prev_clip()

    def select_next_clip(self) -> None:
        if not self.timeline or not self.timeline.clips:
            return
        if not self.selected_clip_id:
            self.select_clip(self.timeline.clips[0].id)
            return
        ids = [clip.id for clip in self.timeline.clips]
        try:
            idx = ids.index(self.selected_clip_id)
        except ValueError:
            return
        if idx + 1 < len(ids):
            next_id = ids[idx + 1]
            self.select_clip(next_id)
            self.scroll_to_clip(next_id)

    def select_prev_clip(self) -> None:
        if not self.timeline or not self.timeline.clips:
            return
        if not self.selected_clip_id:
            self.select_clip(self.timeline.clips[0].id)
            return
        ids = [clip.id for clip in self.timeline.clips]
        try:
            idx = ids.index(self.selected_clip_id)
        except ValueError:
            return
        if idx - 1 >= 0:
            prev_id = ids[idx - 1]
            self.select_clip(prev_id)
            self.scroll_to_clip(prev_id)

    def scroll_to_clip(self, clip_id: str) -> None:
        try:
            self.clip_list.scroll_to(key=clip_id, duration=100)
        except Exception:
            pass

    async def save_project(self, _event: ft.ControlEvent | None = None) -> bool:
        return await self._save_project(
            show_dialogs=True, choose_directory=self.output_folder is None
        )

    async def _save_project(
        self, show_dialogs: bool, choose_directory: bool = False
    ) -> bool:
        if not self.timeline:
            if show_dialogs:
                self.show_error("入力エラー", "編集するタイムラインがありません。")
            return False
        if self.is_saving:
            return False
        if self.selected_clip_id and not self.apply_selected_edit(
            show_dialogs=show_dialogs
        ):
            return False

        output_folder = self.output_folder
        if choose_directory or output_folder is None:
            if show_dialogs:
                self.pending_save_after_folder_pick = True
                selected = await self.output_folder_picker.get_directory_path(
                    dialog_title="保存先ディレクトリを選択",
                    initial_directory=str(self.timeline.media_folder),
                )
                self.pending_save_after_folder_pick = False
                if selected:
                    self.output_folder = Path(selected).expanduser()
                    return self.save_project_to_folder(
                        self.output_folder, show_dialogs=True
                    )
            return False

        return self.save_project_to_folder(output_folder, show_dialogs=show_dialogs)

    def save_project_to_folder(self, output_folder: Path, show_dialogs: bool) -> bool:
        if not self.timeline:
            return False
        timeline_path = output_folder / "timeline.yaml"
        fcpxml_path = output_folder / "timeline.fcpxml"
        self.is_saving = True
        try:
            self.editor_status_text.value = "回転補正済みメディアを確認しています..."
            self.page.update()
            self.timeline = ensure_normalized_media(self.timeline, timeline_path)
            save_timeline(self.timeline, timeline_path)
            export_timeline(self.timeline, fcpxml_path)
        except (OSError, TimelineError, ValueError) as exc:
            if show_dialogs:
                self.show_error("保存失敗", str(exc))
            else:
                self.editor_status_text.value = f"自動保存失敗: {exc}"
                self.page.update()
            return False
        finally:
            self.is_saving = False

        self.output_folder = output_folder
        self.timeline_path = timeline_path
        self.editor_status_text.value = (
            f"{timeline_path} / {fcpxml_path} を保存しました。自動保存はONです。"
        )
        self.enable_autosave()
        self.page.update()
        return True

    def enable_autosave(self) -> None:
        self.autosave_enabled = True
        if self.autosave_timer is None:
            self.autosave_timer = threading.Timer(
                AUTOSAVE_INTERVAL_SECONDS, self.run_autosave
            )
            self.autosave_timer.daemon = True
            self.autosave_timer.start()

    def run_autosave(self) -> None:
        self.autosave_timer = None
        if self.autosave_enabled and self.timeline_path and self.output_folder:
            self.save_project_to_folder(self.output_folder, show_dialogs=False)
        if self.autosave_enabled:
            self.enable_autosave()

    def show_error(self, title: str, message: str) -> None:
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(title),
            content=ft.Text(message, selectable=True),
            actions=[
                ft.TextButton("OK", on_click=lambda _e: self.close_dialog()),
            ],
        )
        self.open_dialog(dialog)

    def show_confirm(
        self,
        title: str,
        message: str,
        on_confirm: Callable[[ft.ControlEvent | None], None],
    ) -> None:
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(title),
            content=ft.Text(message),
            actions=[
                ft.TextButton("キャンセル", on_click=lambda _e: self.close_dialog()),
                ft.FilledButton("削除", on_click=on_confirm),
            ],
        )
        self.open_dialog(dialog)

    def open_dialog(self, dialog: ft.AlertDialog) -> None:
        self.page.show_dialog(dialog)

    def close_dialog(self) -> None:
        self.page.pop_dialog()


def app_main(page: ft.Page) -> None:
    TimelineApp(page)


def main() -> None:
    ft.run(app_main)


if __name__ == "__main__":
    main()
