# Roxy Blender AI

Blender 専用の AI チャットデスクトップアプリです。Claude Code と OpenAI Codex を切り替えながら、
自然言語で Blender を操作できます。

**このアプリは AI チャット GUI です。** モデリング・マテリアル・UV・リギング・Geometry Nodes・
UE5 書き出し・Guide 管理などの Blender 制作機能はすべて
[Roxy-BlenderMCP-v2](https://github.com/RoxyCoding/Roxy-BlenderMCP-v2) が担当し、
このアプリはそれを重複実装しません。MCP のソースコードも変更しません。

---

## 構成

```
┌──────────── Tauri デスクトップシェル (Rust) ────────────┐
│  React + TypeScript GUI（チャット / 履歴 / 右パネル）       │
│        │ REST + WebSocket（127.0.0.1・起動ごとのトークン）  │
└────────┼───────────────────────────────────────────────┘
         ▼
  Python バックエンド (FastAPI)  ── SQLite（会話・プロジェクト）
   ├─ Claude Code アダプタ … 公式 Claude Agent SDK → claude CLI ─┐
   ├─ Codex アダプタ ……… 公式 codex app-server (JSON-RPC) ─────┤ AI が MCP を直接起動・利用
   │                                                          ▼
   │                                          roxy-blender-mcp ─→ Blender アドオン (:9876)
   └─ MCP モニター ……… GUI 表示専用の MCP クライアント ──────────┘
        （get_addon_status / scene_state / get_scene_info / viewport_capture のみ）
```

| 場所 | 役割 |
|---|---|
| `app/src` | GUI（React + TypeScript）。外部ライブラリは react-markdown / remark-gfm のみ |
| `app/src-tauri` | デスクトップシェル。バックエンドの起動・再起動・終了を管理 |
| `backend/roxy_ai/adapters/base.py` | **共通アダプターインターフェース**（AI の追加はここを実装して `adapters/__init__.py` に登録） |
| `backend/roxy_ai/adapters/claude_code.py` | Claude Code（Agent SDK、ストリーミング、`can_use_tool` で承認、中断） |
| `backend/roxy_ai/adapters/codex.py` | Codex（app-server：スレッド、ストリーミング、承認、中断、モデル一覧、ログイン） |
| `backend/roxy_ai/mcp_monitor.py` | GUI 用 MCP 接続（状態・ツール一覧・シーン情報・プレビュー） |
| `backend/roxy_ai/chat.py` | 実行管理、承認、競合防止、編集・再送信、AI 間の引き継ぎ、エクスポート |
| `backend/roxy_ai/context.py` | 会話内容の引き継ぎロジック（純粋関数・テストあり） |
| `backend/roxy_ai/db.py` | SQLite（WAL） |

### AI の切り替えと引き継ぎ

会話は AI に依存しない共通形式で保存し、各 AI の内部セッション（Claude のセッション /
Codex のスレッド）は別に記録します。

- 同じ AI が会話をすべて見ている → 内部セッションを再開し、新しいメッセージだけ送信
- 途中で別の AI が応答した → 内部セッションを再開し、その間のやり取りを文章で共有
- 内部セッションがない／失われた → 新しいセッションを開始し、会話全体を文章で引き継ぎ

**会話内容は引き継げますが、AI 内部の状態（ツール実行の文脈など）は引き継げません。**
この区別はチャット上の通知で毎回表示されます。メッセージを編集・再送信した場合は、
古い分岐を含む内部セッションを破棄し、会話内容から再構築します。

### 認証

- **Claude Code**：インストール済み Claude Code CLI のログイン（ご自身のサブスクリプション、または
  CLI が使う API キー）をそのまま使用します。アプリは Claude の認証情報を扱いません。
  任意で API キーモードも選べます（Windows 資格情報マネージャーに保存）。
- **Codex**：Codex CLI のログイン（ChatGPT サインインまたは API キー。保存は Codex 自身）。
  設定画面から公式の `account/login` フローを起動できます。

> **利用条件について**：Anthropic は、第三者が開発した製品で claude.ai のログインやサブスクリプションの
> 利用枠を提供することを、事前承認なしには認めていません（Agent SDK のドキュメント参照）。
> 本アプリはご自身の PC 上で、ご自身がログインした CLI を使う個人用ツールとして設計しています。
> **他の人に配布・提供する場合は、Claude 側は API キーモードを使用してください。**

### 承認フロー

AI の権限確認は迂回しません。Claude Code の権限要求・`AskUserQuestion`、Codex のコマンド／ファイル変更／
権限／MCP ツール承認・質問は、すべて GUI のダイアログに表示されます。
「承認を省略するモード」（`bypassPermissions` / `approval_policy=never` / `danger-full-access`）は設定画面から選べません。

既定では、MCP が `readOnlyHint` を宣言したツールだけ自動承認します（設定で変更可）。

### セキュリティ

- バックエンドは `127.0.0.1` のみで待ち受け、起動ごとのランダムトークンが全リクエストに必要
- アプリ以外の Origin からのリクエストは拒否
- 添付ファイルはアプリのデータフォルダにコピーして ID で参照（任意パスの読み取り・配信はしない）
- バックエンドは Windows のジョブオブジェクトで子プロセス（AI CLI・MCP サーバー）を束ね、
  アプリ終了・異常終了時に OS がまとめて終了させます。シェル側もバックエンドを監視・終了します
- 同じ Blender（ホスト:ポート）に対して同時に実行できる AI は 1 つだけ（競合防止）
- ログ：`%APPDATA%\RoxyBlenderAI\logs\`（`app.log`、操作ログ `operations.jsonl`、`mcp_monitor.log`）

---

## セットアップ（開発環境）

前提：Windows、Node.js 20+、Rust（MSVC）、Python 3.10+、[uv](https://docs.astral.sh/uv/)（推奨）、
Claude Code CLI と Codex CLI（使う方だけでも可）、Roxy-BlenderMCP-v2 のセットアップ済みチェックアウト。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1   # backend\.venv と npm パッケージ
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1     # アプリ起動（Tauri + Vite + バックエンド）
```

初回起動後、**設定 → Blender MCP** で Roxy-BlenderMCP-v2 のフォルダを確認してください
（このフォルダの隣の `..\MCP\Roxy-BlenderMCP-v2` は自動検出されます）。
Blender 側は 3D ビューで N キー →「Roxy Blender MCP」タブから接続します。

シーン変更の自動検知には、更新済みの MCP サーバーとプロトコル20以降の Blender アドオンが必要です。
アドオンを `roxy-blender-mcp update` で更新したら、Blender とアプリを再起動してください。
GUI は3秒ごとに共有カウンターを確認し、変更時にシーン情報を取得します。
プレビューは「シーンが変更されたらプレビューを自動更新する」がオンの場合に更新します。
旧アドオンでは更新案内を表示し、手動更新と従来のAI操作後の更新を利用できます。

MCP の起動はチェックアウト内の実行ファイルを優先し、無ければ `uv run --no-sync` を使います。
初回セットアップや依存関係の変更後は、MCP クライアントを終了して MCP フォルダーで `uv sync` を実行してください。
自分で起動コマンドを指定している場合は、その設定が優先されます。

### ブラウザでのデバッグ（Tauri なし）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-browser.ps1
# 表示された http://127.0.0.1:1420/?port=…&token=… をブラウザで開く
```

### Windows インストーラーのビルド

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
# → app\src-tauri\target\release\bundle\nsis\Roxy Blender AI_0.1.0_x64-setup.exe
```

バックエンドは PyInstaller で単体 exe（Tauri サイドカー）になります。Claude Agent SDK 同梱の CLI
（約 250MB）は含めず、PC にインストール済みの Claude Code CLI を使います。

### テスト

```powershell
cd backend; .venv\Scripts\python.exe -m pytest -q     # バックエンド単体テスト
cd app; npm run typecheck                               # GUI 型チェック
node tests/right-panel.mjs                              # 右パネルの構造化データ・旧形式の表示
```

---

## 動作確認チェックリスト（ユーザー確認用）

1. 起動 → 左下「設定 → AI」で両 AI のアカウントが表示される
2. Blender 起動・アドオン接続 → 上部バーが「Blender接続中」、右パネルで更新ボタン → プレビュー・選択情報
3. 「今のシーンを確認して」を送信 → ストリーミング表示、承認ダイアログ、右パネルに進捗・MCP結果
4. 生成中に ■ ボタンで中断できる
5. 参考画像をドラッグ＆ドロップ／貼り付け → サムネイル、クリックで拡大、差し替え・削除
6. Claude Code ↔ Codex を切り替えて続けて送信 → 「引き継ぎました」の通知
7. ユーザーメッセージの編集・再送信、エラー時の「再試行」
8. 履歴検索、会話の名前変更・削除・エクスポート（Markdown / JSON）
9. プロジェクトの作成・切り替え（Blender 接続先・.blend・作業フォルダ）、再起動後に最後のプロジェクトが開く
10. 設定 → ログ・診断 でアプリ／操作／MCP ログを確認

---

## MCP 側で不足している機能（GUI では代替実装していません）

| 不足 | 影響 | 現在の対応 |
|---|---|---|
| プッシュ型のシーン変更通知（イベント）がない | GUI が変更を確認する必要がある | `scene_state` を3秒ごとにポーリング。旧アドオンでは手動更新とAI操作後の更新 |
| 旧MCPのシーン情報はテキストのみ | 名前や書式によって表示が崩れる可能性がある | 新MCPの `structuredContent` を優先し、旧MCPのみテキストを解析 |
| 旧MCPには一部の読み取り専用注釈がない | 自動承認の対象にならないことがある | 新MCPの `readOnlyHint` を取得。承認の設定は従来どおり適用 |
| ビューポート画像の保存先 (`viewport_store`) がプロセスごと | AI の `look` 結果と GUI のプレビューは別取得 | AI の画像結果はチャットと右パネル「MCPの結果」に表示 |
| `.blend` を開く／保存する専用ツールがない | GUI はプロジェクトと .blend の関連付け情報のみ管理 | ファイル操作は AI が MCP（`execute_blender_code` / `checkpoint`）で行う |
| 旧MCPの起動時ハンドシェイクが初期化を止めることがある | AI 側の MCP 接続がタイムアウトする | 新MCPは接続を遅延。無応答メッセージも「Blender応答なし」と判定し、詳細取得を中止 |
| `uv run` 起動時の環境同期が実行中のファイルと競合することがある | MCP が起動できない | チェックアウトの実行ファイルを直接起動し、フォールバックは `uv run --no-sync` |
