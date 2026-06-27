# Setlog DaVinci Resolve Integration

素材フォルダ内の短い動画をファイル名の自然順で並べ、DaVinci Resolve に読み込ませるための `timeline.fcpxml` を生成・編集する最小アプリです。

## 使い方

CLIから編集する場合:

```bash
uv run setlog-fcpxml scan /path/to/media -o timeline.yaml
uv run setlog-fcpxml show timeline.yaml
uv run setlog-fcpxml trim timeline.yaml c001 --in 1.25 --out 4.5
uv run setlog-fcpxml disable timeline.yaml c002
uv run setlog-fcpxml move timeline.yaml c010 --before c003
uv run setlog-fcpxml note timeline.yaml c001 "ここは使う"
uv run setlog-fcpxml rotate timeline.yaml c021 90
uv run setlog-fcpxml thumbs timeline.yaml
uv run setlog-fcpxml validate timeline.yaml
uv run setlog-fcpxml export timeline.yaml -o timeline.fcpxml
uv run setlog-fcpxml clean timeline.yaml
```

`timeline.yaml` は現MVPではJSON互換YAMLとして保存されます。CLIやGUIで読み書きしやすく、FCPXMLは常にこの編集ファイルから再生成します。

GUIで編集する場合:

```bash
uv run setlog-fcpxml-gui
```

開発中にGUIをhot reloadする場合:

```bash
uv run setlog-fcpxml dev
```

`dev` は `watchfiles` で `.py` / `.toml` の変更を監視し、変更時にGUIを自動再起動します。開発依存が未インストールの場合は `uv sync --group dev` を実行してください。

GUIでは素材フォルダを選んで `OK` を押すと、編集画面が開きます。クリップ一覧、代表フレーム、採用状態、in/out、メモを確認・保存できます。選択クリップの上下移動もできます。散布図アイコンからクリップ尺の分布を表示でき、σスライダーで外れ値の境界を調整できます。分布上のドットを選ぶとクリップ一覧の選択も同期します。選択クリップのプレビュー画像は必要に応じて `.setlog/previews/` にPNGとして生成されます。回転メタデータ付きの動画は `.setlog/normalized/` に補正済み動画を作り、`連続プレビュー` とFCPXML書き出しではそちらを使います。向きが合わないクリップは `-90°` / `+90°` で回転補正できます。歯車メニューから `.setlog` 配下のキャッシュを削除できます。

1. 素材フォルダを選択します。
2. 必要ならプロジェクト名を変更します。
3. `OK` を押して編集画面を開きます。
4. `一時保存` または `Ctrl+S` / `Command+S` で保存先ディレクトリを選択します。
5. 初回保存後は30秒ごとに `timeline.yaml` と `timeline.fcpxml` が自動保存されます。

既存の `timeline.yaml` を開くこともできます。その場合は読み込んだファイルのあるディレクトリに `timeline.fcpxml` も保存され、自動保存が有効になります。

対象拡張子は `.mov`, `.mp4`, `.m4v` です。動画ファイルはコピーも変換もせず、FCPXML 内に元ファイルへの `file://` リンクを持たせます。

## CLIコマンド

- `scan <media_folder> -o timeline.yaml`: 素材を解析して編集ファイルを作成します。
- `show timeline.yaml`: クリップID、採用状態、in/out、尺、累積開始時刻、サムネイル、メモを一覧表示します。
- `trim timeline.yaml <clip_id> --in <time> --out <time>`: クリップの使用範囲を変更します。
- `enable` / `disable`: クリップの採用/不採用を切り替えます。
- `move timeline.yaml <clip_id> --before <clip_id>` / `--after <clip_id>`: クリップ順を変更します。
- `note timeline.yaml <clip_id> "text"`: 編集メモを保存します。
- `rotate timeline.yaml <clip_id> <0|90|180|270>`: クリップの回転補正値を手動で変更し、該当クリップのキャッシュを削除します。
- `thumbs timeline.yaml`: `.setlog/thumbs/` に代表サムネイルを生成します。
- `validate timeline.yaml`: 編集ファイルと素材の整合性を確認します。
- `export timeline.yaml -o timeline.fcpxml`: DaVinci Resolve向けFCPXMLを出力します。
- `clean timeline.yaml`: `.setlog/` 配下のプレビュー、サムネイル、回転補正済み動画を削除します。`--include-exports` を付けると `timeline.fcpxml` も削除します。

時刻指定は `1.25`, `00:00:01.250`, `00:00:01:15` の形式に対応します。フレーム付きタイムコードは各クリップのフレームレートで解釈されます。

## DaVinci Resolve での確認

1. DaVinci Resolve を開きます。
2. `File > Import Timeline > Import AAF, EDL, XML...` から生成した `timeline.fcpxml` を選択します。
3. タイムライン上で順序、尺、音声、オフラインメディアの有無を確認します。
4. オフラインになる場合は、Resolve 側で素材フォルダを指定してリリンクしてください。

## テスト

```bash
uv run test
```

## 前提

- Python 3.12 以上
- `ffprobe` が利用可能であること

macOS では Homebrew の ffmpeg で `ffprobe` を入れられます。

```bash
brew install ffmpeg
```
