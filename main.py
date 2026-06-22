from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from fractions import Fraction
from pathlib import Path
from tkinter import BooleanVar, Canvas, Menu, PhotoImage, StringVar, TclError, Text, Tk, filedialog, messagebox, ttk

from fcpxml_generator import MediaProbeError
from timeline_edit import (
    EditableClip,
    EditableTimeline,
    TimelineError,
    cleanup_intermediate_files,
    ensure_normalized_media,
    export_timeline,
    format_edit_time,
    format_time,
    generate_preview_image,
    load_timeline,
    move_clip,
    save_timeline,
    scan_timeline,
    set_enabled,
    set_note,
    trim_clip,
)


DEFAULT_MEDIA_FOLDER = Path("/Users/yamada_kentaro/Movies/20260228_0301_fukui")
AUTOSAVE_INTERVAL_MS = 30_000


class TimelineApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Setlog FCPXML Generator")
        self.root.minsize(900, 560)
        self._set_default_window_size()

        self.media_folder = StringVar(value=str(DEFAULT_MEDIA_FOLDER))
        self.project_name = StringVar(value="timeline")
        self.status = StringVar(value="素材フォルダを選択してください。")
        self.timeline_path: Path | None = None
        self.output_folder: Path | None = None
        self.timeline: EditableTimeline | None = None
        self.selected_clip_id: str | None = None
        self.preview_image: PhotoImage | None = None
        self.editor_status = StringVar(value="未保存です。保存すると自動保存が有効になります。")
        self.clip_enabled = BooleanVar(value=True)
        self.clip_detail = StringVar()
        self.clip_trim_in = StringVar()
        self.clip_trim_out = StringVar()
        self.timeline_summary = StringVar(value="")
        self.timeline_visual_segments: list[tuple[float, float, str]] = []
        self.preview_player: subprocess.Popen[str] | None = None
        self.autosave_enabled = False
        self.autosave_after_id: str | None = None
        self.is_saving = False

        self._build_ui()
        self.root.bind_all("<Control-s>", self.save_project_shortcut)
        self.root.bind_all("<Command-s>", self.save_project_shortcut)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _set_default_window_size(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = max(900, screen_width)
        height = max(560, screen_height)
        self.root.geometry(f"{width}x{height}+0+0")

        try:
            self.root.state("zoomed")
        except Exception:
            try:
                self.root.attributes("-zoomed", True)
            except Exception:
                pass

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.start_frame = ttk.Frame(self.root, padding=20)
        self.editor_frame = ttk.Frame(self.root, padding=16)
        self._build_start_screen(self.start_frame)
        self._build_editor_screen(self.editor_frame)
        self.show_start_screen()

    def _build_start_screen(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="素材フォルダ").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(frame, textvariable=self.media_folder).grid(row=0, column=1, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(frame, text="選択", command=self.choose_media_folder).grid(row=0, column=2, pady=(0, 8))

        ttk.Label(frame, text="プロジェクト名").grid(row=1, column=0, sticky="w", pady=(0, 16))
        ttk.Entry(frame, textvariable=self.project_name).grid(row=1, column=1, sticky="ew", padx=8, pady=(0, 16))

        actions = ttk.Frame(frame)
        actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 16))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        self.start_button = ttk.Button(actions, text="OK", command=self.start_editing)
        self.start_button.grid(row=0, column=0, sticky="ew")
        ttk.Button(actions, text="既存の timeline.yaml を開く", command=self.open_timeline).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(8, 0),
        )

        ttk.Label(frame, textvariable=self.status, wraplength=720).grid(row=3, column=0, columnspan=3, sticky="w")

    def _build_editor_screen(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(frame)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Button(toolbar, text="一時保存", command=self.save_project).pack(side="left")
        ttk.Button(toolbar, text="再読み込み", command=self.reload_timeline).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="連続プレビュー", command=self.play_timeline_preview).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="停止", command=self.stop_timeline_preview).pack(side="left", padx=(8, 0))
        ttk.Label(toolbar, textvariable=self.editor_status).pack(side="left", padx=(16, 0))
        settings_button = ttk.Menubutton(toolbar, text="⚙")
        settings_menu = Menu(settings_button, tearoff=False)
        settings_menu.add_command(label="キャッシュを削除", command=self.cleanup_cache)
        settings_menu.add_command(label="キャッシュと出力XMLを削除", command=lambda: self.cleanup_cache(include_exports=True))
        settings_button.configure(menu=settings_menu)
        settings_button.pack(side="right")

        timeline_frame = ttk.Frame(frame)
        timeline_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        timeline_frame.columnconfigure(0, weight=1)
        ttk.Label(timeline_frame, textvariable=self.timeline_summary).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.timeline_canvas = Canvas(
            timeline_frame,
            height=74,
            background="#202124",
            highlightthickness=1,
            highlightbackground="#3c4043",
            cursor="hand2",
        )
        self.timeline_canvas.grid(row=1, column=0, sticky="ew")
        self.timeline_canvas.bind("<Configure>", self.draw_timeline_visual)
        self.timeline_canvas.bind("<Button-1>", self.on_timeline_canvas_click)

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 12))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        columns = ("on", "start", "dur", "name")
        self.clip_tree = ttk.Treeview(list_frame, columns=columns, show="tree headings", selectmode="browse")
        self.clip_tree.heading("#0", text="id")
        self.clip_tree.heading("on", text="on")
        self.clip_tree.heading("start", text="start")
        self.clip_tree.heading("dur", text="dur")
        self.clip_tree.heading("name", text="name")
        self.clip_tree.column("#0", width=72, stretch=False)
        self.clip_tree.column("on", width=48, stretch=False)
        self.clip_tree.column("start", width=112, stretch=False)
        self.clip_tree.column("dur", width=112, stretch=False)
        self.clip_tree.column("name", width=220, stretch=True)
        self.clip_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.clip_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.clip_tree.configure(yscrollcommand=scrollbar.set)
        self.clip_tree.bind("<<TreeviewSelect>>", self.on_clip_selected)

        detail = ttk.Frame(frame)
        detail.grid(row=2, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(1, weight=1)

        ttk.Label(detail, textvariable=self.clip_detail).grid(row=0, column=0, sticky="w", pady=(0, 8))
        preview_frame = ttk.Frame(detail, relief="solid", borderwidth=1)
        preview_frame.grid(row=1, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(preview_frame, text="クリップを選択してください。", anchor="center")
        self.preview_label.grid(row=0, column=0, sticky="nsew")

        controls = ttk.Frame(detail)
        controls.grid(row=2, column=0, sticky="ew", pady=(12, 8))
        controls.columnconfigure(2, weight=1)
        controls.columnconfigure(4, weight=1)
        ttk.Checkbutton(controls, text="採用", variable=self.clip_enabled).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Label(controls, text="in").grid(row=0, column=1, sticky="e")
        ttk.Entry(controls, textvariable=self.clip_trim_in, width=16).grid(row=0, column=2, sticky="ew", padx=(4, 12))
        ttk.Label(controls, text="out").grid(row=0, column=3, sticky="e")
        ttk.Entry(controls, textvariable=self.clip_trim_out, width=16).grid(row=0, column=4, sticky="ew", padx=(4, 0))

        actions = ttk.Frame(detail)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="上へ", command=self.move_selected_up).pack(side="left")
        ttk.Button(actions, text="下へ", command=self.move_selected_down).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="変更を保存", command=self.save_selected_edit).pack(side="left", padx=(12, 0))

        ttk.Label(detail, text="メモ").grid(row=4, column=0, sticky="w")
        self.note_text = Text(detail, height=5, wrap="word")
        self.note_text.grid(row=5, column=0, sticky="ew")

    def show_start_screen(self) -> None:
        self.editor_frame.grid_remove()
        self.start_frame.grid(row=0, column=0, sticky="nsew")

    def show_editor_screen(self) -> None:
        self.start_frame.grid_remove()
        self.editor_frame.grid(row=0, column=0, sticky="nsew")

    def choose_media_folder(self) -> None:
        folder = filedialog.askdirectory(title="素材フォルダを選択")
        if folder:
            self.media_folder.set(folder)

    def start_editing(self) -> None:
        media_folder = Path(self.media_folder.get()).expanduser()
        project_name = self.project_name.get().strip() or "timeline"

        if not media_folder.is_dir():
            messagebox.showerror("入力エラー", "有効な素材フォルダを選択してください。")
            return

        self.start_button.configure(state="disabled")
        self.status.set("素材を解析しています...")
        thread = threading.Thread(
            target=self._scan_in_background,
            args=(media_folder, project_name),
            daemon=True,
        )
        thread.start()

    def _scan_in_background(self, media_folder: Path, project_name: str) -> None:
        try:
            timeline = scan_timeline(media_folder, project_name=project_name)
            self.root.after(0, self._finish_scan_success, timeline)
        except (OSError, MediaProbeError, TimelineError, ValueError) as exc:
            self.root.after(0, self._finish_scan_error, str(exc))

    def _finish_scan_success(self, timeline: EditableTimeline) -> None:
        self.start_button.configure(state="normal")
        self.timeline = timeline
        self.timeline_path = None
        self.output_folder = None
        self.autosave_enabled = False
        self.editor_status.set("未保存です。一時保存または Ctrl+S で保存先を選択してください。")
        self.status.set(f"クリップ数: {len(timeline.clips)}")
        self.show_editor_screen()
        self.populate_clip_tree()

    def _finish_scan_error(self, message: str) -> None:
        self.start_button.configure(state="normal")
        self.status.set(message)
        messagebox.showerror("読み込み失敗", message)

    def open_timeline(self) -> None:
        path = filedialog.askopenfilename(
            title="timeline.yaml を選択",
            filetypes=(("Timeline", "*.yaml *.yml *.json"), ("All files", "*.*")),
        )
        if path:
            self.load_timeline_file(Path(path))

    def reload_timeline(self) -> None:
        if not self.timeline_path:
            messagebox.showerror("入力エラー", "保存済みの timeline.yaml がありません。")
            return
        self.load_timeline_file(self.timeline_path)

    def load_timeline_file(self, path: Path) -> None:
        try:
            self.timeline = ensure_normalized_media(load_timeline(path), path)
            save_timeline(self.timeline, path)
            self.timeline_path = path
            self.output_folder = path.parent
            self.project_name.set(self.timeline.project_name)
            self.media_folder.set(str(self.timeline.media_folder))
            self.editor_status.set(f"{path} を読み込みました。自動保存はONです。")
            self.show_editor_screen()
            self.populate_clip_tree()
            self.enable_autosave()
        except (OSError, TimelineError) as exc:
            messagebox.showerror("読み込み失敗", str(exc))

    def populate_clip_tree(self, select_id: str | None = None) -> None:
        if not self.timeline:
            return
        self.clip_tree.delete(*self.clip_tree.get_children())
        offset = Fraction(0, 1)
        for clip in self.timeline.clips:
            start = format_time(offset) if clip.enabled else "-"
            self.clip_tree.insert(
                "",
                "end",
                iid=clip.id,
                text=clip.id,
                values=("yes" if clip.enabled else "no", start, format_time(clip.timeline_duration), clip.name),
            )
            if clip.enabled:
                offset += clip.timeline_duration
        if self.timeline.clips:
            next_id = select_id if select_id in {clip.id for clip in self.timeline.clips} else self.timeline.clips[0].id
            self.clip_tree.selection_set(next_id)
            self.clip_tree.focus(next_id)
        self.draw_timeline_visual()

    def draw_timeline_visual(self, _event: object | None = None) -> None:
        self.timeline_visual_segments = []
        self.timeline_canvas.delete("all")
        width = max(self.timeline_canvas.winfo_width(), 1)
        height = max(self.timeline_canvas.winfo_height(), 1)
        padding_x = 12
        track_y = 24
        track_height = 30
        ruler_y = 62

        if not self.timeline:
            self.timeline_summary.set("")
            self.timeline_canvas.create_text(
                width / 2,
                height / 2,
                text="タイムラインを読み込んでください",
                fill="#bdc1c6",
                anchor="center",
            )
            return

        enabled_clips = [clip for clip in self.timeline.clips if clip.enabled]
        total_duration = sum((clip.timeline_duration for clip in enabled_clips), Fraction(0, 1))
        if total_duration <= 0:
            self.timeline_summary.set("有効なクリップがありません")
            self.timeline_canvas.create_text(
                width / 2,
                height / 2,
                text="表示できるクリップがありません",
                fill="#bdc1c6",
                anchor="center",
            )
            return

        self.timeline_summary.set(
            f"タイムライン: {len(enabled_clips)} clips / total {format_time(total_duration)}"
        )
        track_width = max(width - padding_x * 2, 1)
        self.timeline_canvas.create_rectangle(
            padding_x,
            track_y,
            width - padding_x,
            track_y + track_height,
            fill="#15171a",
            outline="#3c4043",
        )

        colors = ("#5f8dd3", "#5dbb9d", "#d7a94f", "#c77dbb", "#8ab766", "#d86f66")
        offset = Fraction(0, 1)
        for index, clip in enumerate(enabled_clips):
            start_ratio = float(offset / total_duration)
            end_ratio = float((offset + clip.timeline_duration) / total_duration)
            x1 = padding_x + track_width * start_ratio
            x2 = padding_x + track_width * end_ratio
            if x2 - x1 < 1:
                x2 = x1 + 1
            fill = colors[index % len(colors)]
            outline = "#f8fafd" if clip.id == self.selected_clip_id else "#202124"
            outline_width = 3 if clip.id == self.selected_clip_id else 1
            self.timeline_canvas.create_rectangle(
                x1,
                track_y,
                x2,
                track_y + track_height,
                fill=fill,
                outline=outline,
                width=outline_width,
            )
            if x2 - x1 >= 42:
                self.timeline_canvas.create_text(
                    x1 + 6,
                    track_y + track_height / 2,
                    text=clip.id,
                    fill="#ffffff",
                    anchor="w",
                )
            self.timeline_visual_segments.append((x1, x2, clip.id))
            offset += clip.timeline_duration

        self.timeline_canvas.create_text(padding_x, ruler_y, text="00:00:00.000", fill="#bdc1c6", anchor="w")
        self.timeline_canvas.create_text(
            width - padding_x,
            ruler_y,
            text=format_time(total_duration),
            fill="#bdc1c6",
            anchor="e",
        )

    def on_timeline_canvas_click(self, event: object) -> None:
        x = getattr(event, "x", 0)
        for x1, x2, clip_id in self.timeline_visual_segments:
            if x1 <= x <= x2:
                self.clip_tree.selection_set(clip_id)
                self.clip_tree.focus(clip_id)
                self.clip_tree.see(clip_id)
                self.on_clip_selected()
                return

    def on_clip_selected(self, _event: object | None = None) -> None:
        if not self.timeline:
            return
        selection = self.clip_tree.selection()
        if not selection:
            return
        clip_id = selection[0]
        clip = next((item for item in self.timeline.clips if item.id == clip_id), None)
        if not clip:
            return
        self.selected_clip_id = clip.id
        self.clip_enabled.set(clip.enabled)
        self.clip_trim_in.set(format_edit_time(clip.trim_in))
        self.clip_trim_out.set(format_edit_time(clip.trim_out))
        self.note_text.delete("1.0", "end")
        self.note_text.insert("1.0", clip.note)
        self.clip_detail.set(
            f"{clip.id}  {clip.name}  in {format_time(clip.trim_in)} / out {format_time(clip.trim_out)} / dur {format_time(clip.timeline_duration)}"
        )
        self.draw_timeline_visual()
        self.preview_label.configure(text="プレビュー生成中...", image="")
        threading.Thread(target=self._load_preview_in_background, args=(clip,), daemon=True).start()

    def _load_preview_in_background(self, clip: EditableClip) -> None:
        preview_base = self.get_cache_base_path()
        try:
            preview_path = generate_preview_image(clip, preview_base)
            self.root.after(0, self._show_preview_image, preview_path, clip.id)
        except (OSError, TimelineError) as exc:
            self.root.after(0, self._show_preview_error, str(exc))

    def _show_preview_image(self, preview_path: Path, clip_id: str) -> None:
        if self.selected_clip_id != clip_id:
            return
        try:
            self.preview_image = PhotoImage(file=str(preview_path))
        except TclError as exc:
            self._show_preview_error(f"プレビュー画像を読み込めません: {exc}")
            return
        self.preview_label.configure(image=self.preview_image, text="")
        self.editor_status.set(f"プレビュー: {preview_path}")

    def _show_preview_error(self, message: str) -> None:
        self.preview_label.configure(text=message, image="")
        self.editor_status.set(message)

    def save_selected_edit(self) -> None:
        if not self.apply_selected_edit(show_dialogs=True):
            return
        self.save_project()

    def play_timeline_preview(self) -> None:
        if not self.timeline:
            messagebox.showerror("入力エラー", "編集するタイムラインがありません。")
            return
        if self.selected_clip_id and not self.apply_selected_edit(show_dialogs=True):
            return

        enabled_clips = [clip for clip in self.timeline.clips if clip.enabled]
        if not enabled_clips:
            messagebox.showerror("入力エラー", "採用中のクリップがありません。")
            return

        self.stop_timeline_preview(update_status=False)
        try:
            self.timeline = ensure_normalized_media(self.timeline, self.get_cache_base_path())
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
            messagebox.showerror("プレビュー失敗", "ffplay が見つかりません。Homebrew などで ffmpeg をインストールしてください。")
            return
        except OSError as exc:
            messagebox.showerror("プレビュー失敗", str(exc))
            return

        self.root.after(300, self.focus_timeline_preview_window, self.preview_player.pid)
        total_duration = sum((clip.timeline_duration for clip in enabled_clips), Fraction(0, 1))
        self.editor_status.set(f"連続プレビュー中: {len(enabled_clips)} clips / {format_time(total_duration)}")

    def focus_timeline_preview_window(self, process_id: int, attempts: int = 4) -> None:
        if sys.platform != "darwin":
            return
        if not self.preview_player or self.preview_player.pid != process_id or self.preview_player.poll() is not None:
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
            self.root.after(300, self.focus_timeline_preview_window, process_id, attempts - 1)

    def stop_timeline_preview(self, update_status: bool = True) -> None:
        if self.preview_player and self.preview_player.poll() is None:
            self.preview_player.terminate()
        self.preview_player = None
        if update_status:
            self.editor_status.set("連続プレビューを停止しました。")

    def cleanup_cache(self, include_exports: bool = False) -> None:
        if include_exports and not self.timeline_path:
            messagebox.showerror("削除できません", "出力XMLを削除するには、先に一時保存してください。")
            return

        target_label = ".setlog のキャッシュ"
        if include_exports:
            target_label += " と timeline.fcpxml"
        if not messagebox.askyesno("削除確認", f"{target_label} を削除します。よろしいですか？"):
            return

        self.stop_timeline_preview(update_status=False)
        try:
            removed = cleanup_intermediate_files(self.get_cache_base_path(), include_exports=include_exports)
        except OSError as exc:
            messagebox.showerror("削除失敗", str(exc))
            return

        self.preview_image = None
        self.preview_label.configure(text="キャッシュを削除しました。クリップを選択すると再生成します。", image="")
        count = len(removed)
        if count:
            self.editor_status.set(f"{count}件の途中生成物を削除しました。")
        else:
            self.editor_status.set("削除対象の途中生成物はありませんでした。")

    def get_cache_base_path(self) -> Path:
        if self.timeline_path:
            return self.timeline_path
        return Path(tempfile.gettempdir()) / "setlog-davinci-resolve-integration" / "timeline.yaml"

    def close(self) -> None:
        self.stop_timeline_preview(update_status=False)
        self.root.destroy()

    def write_preview_concat_file(self, clips: list[EditableClip]) -> Path:
        preview_base = self.get_cache_base_path()
        preview_dir = preview_base.parent / ".setlog" / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        concat_path = preview_dir / "timeline_preview.ffconcat"
        lines = ["ffconcat version 1.0"]
        for clip in clips:
            lines.append(f"file {self.quote_ffconcat_path(clip.path)}")
            lines.append(f"inpoint {float(clip.trim_in):.6f}")
            lines.append(f"outpoint {float(clip.trim_out):.6f}")
        concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return concat_path

    @staticmethod
    def quote_ffconcat_path(path: Path) -> str:
        return "'" + str(path).replace("\\", "\\\\").replace("'", "'\\''") + "'"

    def apply_selected_edit(self, show_dialogs: bool) -> bool:
        if not self.timeline or not self.selected_clip_id:
            if show_dialogs:
                messagebox.showerror("入力エラー", "クリップを選択してください。")
            return False
        clip = self.find_selected_clip()
        if not clip:
            if show_dialogs:
                messagebox.showerror("入力エラー", "選択クリップが見つかりません。")
            return False
        try:
            timeline = trim_clip(self.timeline, clip.id, self.clip_trim_in.get(), self.clip_trim_out.get())
            timeline = set_enabled(timeline, clip.id, self.clip_enabled.get())
            timeline = set_note(timeline, clip.id, self.note_text.get("1.0", "end").strip())
            self.timeline = timeline
            self.populate_clip_tree(select_id=clip.id)
            return True
        except (TimelineError, ValueError) as exc:
            if show_dialogs:
                messagebox.showerror("保存失敗", str(exc))
            else:
                self.editor_status.set(f"自動保存できません: {exc}")
            return False

    def find_selected_clip(self) -> EditableClip | None:
        if not self.timeline or not self.selected_clip_id:
            return None
        return next((item for item in self.timeline.clips if item.id == self.selected_clip_id), None)

    def move_selected_up(self) -> None:
        self.move_selected_clip(-1)

    def move_selected_down(self) -> None:
        self.move_selected_clip(1)

    def move_selected_clip(self, direction: int) -> None:
        if not self.timeline or not self.selected_clip_id:
            messagebox.showerror("入力エラー", "クリップを選択してください。")
            return
        if not self.apply_selected_edit(show_dialogs=True):
            return
        ids = [clip.id for clip in self.timeline.clips]
        try:
            index = ids.index(self.selected_clip_id)
        except ValueError:
            messagebox.showerror("入力エラー", "選択クリップが見つかりません。")
            return
        target_index = index + direction
        if target_index < 0 or target_index >= len(ids):
            return
        clip_id = ids[index]
        target_id = ids[target_index]
        try:
            if direction < 0:
                self.timeline = move_clip(self.timeline, clip_id, before=target_id, after=None)
            else:
                self.timeline = move_clip(self.timeline, clip_id, before=None, after=target_id)
            self.populate_clip_tree(select_id=clip_id)
            if self.timeline_path:
                self.save_project()
            else:
                self.editor_status.set(f"{clip_id} を移動しました。未保存です。")
        except (OSError, TimelineError) as exc:
            messagebox.showerror("移動失敗", str(exc))

    def save_project_shortcut(self, _event: object | None = None) -> str:
        self.save_project()
        return "break"

    def save_project(self) -> None:
        self._save_project(show_dialogs=True, choose_directory=self.output_folder is None)

    def _save_project(self, show_dialogs: bool, choose_directory: bool = False) -> bool:
        if not self.timeline:
            if show_dialogs:
                messagebox.showerror("入力エラー", "編集するタイムラインがありません。")
            return False
        if self.is_saving:
            return False
        if self.selected_clip_id and not self.apply_selected_edit(show_dialogs=show_dialogs):
            return False

        output_folder = self.output_folder
        if choose_directory or output_folder is None:
            selected = filedialog.askdirectory(
                title="保存先ディレクトリを選択",
                initialdir=str(self.timeline.media_folder),
            )
            if not selected:
                return False
            output_folder = Path(selected).expanduser()

        timeline_path = output_folder / "timeline.yaml"
        fcpxml_path = output_folder / "timeline.fcpxml"
        self.is_saving = True
        try:
            self.editor_status.set("回転補正済みメディアを確認しています...")
            self.root.update_idletasks()
            self.timeline = ensure_normalized_media(self.timeline, timeline_path)
            save_timeline(self.timeline, timeline_path)
            export_timeline(self.timeline, fcpxml_path)
        except (OSError, TimelineError, ValueError) as exc:
            if show_dialogs:
                messagebox.showerror("保存失敗", str(exc))
            else:
                self.editor_status.set(f"自動保存失敗: {exc}")
            return False
        finally:
            self.is_saving = False

        self.output_folder = output_folder
        self.timeline_path = timeline_path
        self.editor_status.set(f"{timeline_path} / {fcpxml_path} を保存しました。自動保存はONです。")
        self.enable_autosave()
        return True

    def enable_autosave(self) -> None:
        self.autosave_enabled = True
        if self.autosave_after_id is None:
            self.autosave_after_id = self.root.after(AUTOSAVE_INTERVAL_MS, self.run_autosave)

    def run_autosave(self) -> None:
        self.autosave_after_id = None
        if self.autosave_enabled and self.timeline_path and self.output_folder:
            self._save_project(show_dialogs=False, choose_directory=False)
        if self.autosave_enabled:
            self.autosave_after_id = self.root.after(AUTOSAVE_INTERVAL_MS, self.run_autosave)


def main() -> None:
    root = Tk()
    TimelineApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
