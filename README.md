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

### モデリングプロンプトの相談

左の「モデリングプロンプト」から、何を作るかの相談チャットを始められます。
参考画像も添付でき、形・寸法・素材などを相談して、Blender向けのプロンプトにまとめます。
相談で確定した作るもの・用途・形・寸法・素材・色は、入力欄の上にある「制作条件」で確認・編集できます。
完成したプロンプトの「制作チャットへ渡す」を押すと、条件と参考画像も新しい制作チャットに引き継ぎます。
参考画像は会話ごとにコピーするため、片方の会話を削除しても、もう片方の画像は残ります。
引き継いだプロンプトと画像は送信前の下書きとして保持され、会話を開き直しても復元できます。
内容を確認して送信すると制作を開始します。相談チャットの履歴は制作チャットと別に残ります。
制作中は、AIからの工程報告に基づいて「形状・細部・素材・仕上げ」の進捗を表示します。
中断・エラー時に未完了の工程を完了扱いにはしません。

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

### ローカルLLM（LM Studio などは不要）

上部バーの「ローカルLLM」で、PC上のモデルを使えます。外部への送信はなく、ログインも不要です。

- **アプリ内蔵（既定）**：アプリが llama.cpp の `llama-server` を起動し、GGUF モデル（`.gguf`）を直接読み込みます。
  モデルは `%APPDATA%\RoxyBlenderAI\models` と設定で追加したフォルダ、LM Studio のダウンロード先から自動で見つけ、
  同じフォルダの `mmproj-*.gguf` があれば画像にも対応します。使わない時間が続くと自動で GPU メモリを解放します。
  `llama-server` は `winget install ggml.llamacpp` で入る公式ビルドを自動検出します。
- **CPUオフロード**（設定 → AI → ローカルLLM）：CPUに置く層の数・MoEエキスパートをCPUに置く層の数（`--n-cpu-moe`）・
  CPUスレッド数・KVキャッシュをメインメモリに置く（`--no-kv-offload`）を自由な数値で指定できます。VRAMに入りきらないモデル向けです。
- **LM Studio / Ollama のサーバーを使う**こともできます（設定 → AI → ローカルLLM →「実行方法」）。

AI の実行は Codex（`codex app-server`）が担当するため、ストリーミング・承認・中断・Blender MCP の直接利用はほかの AI と同じです。
Codex は MCP ツールを `namespace` 形式で送りますが、ローカルサーバーは対応していないため、バックエンドの中継
（`roxy_ai/local_proxy.py`）が通常の関数ツールに変換し、呼び出し結果を元の形に戻します。あわせて次の調整を行います。

- ローカルモデルに渡すのは Blender MCP と基本ツールのみ（グローバル設定の他の MCP・アプリは文脈に収まらないため除外）
- シェル実行は既定でオフ（小さいモデルが MCP の代わりにシェルを使って失敗するため。設定で許可可能）
- モデルのチャットテンプレートが途中の system メッセージを拒否する場合（Qwen 系など）、自動で修正版を使用
- 思考モード（reasoning）はオフ（ツール呼び出しが思考テキストに埋もれるため）

小さいモデルは、ツールを呼ばずに結果を推測で答えることがあります。重要な作業ではクラウドの AI を使うか、
結果を右パネルで確認してください。

### 承認フロー

AI の権限確認は迂回しません。Claude Code の権限要求・`AskUserQuestion`、Codex のコマンド／ファイル変更／
権限／MCP ツール承認・質問は、すべて GUI のダイアログに表示されます。
承認を省略したい場合は、**設定 → AI →「フルアクセスモード」**を明示的に有効にします（既定は無効・有効化時に確認あり）。
有効にすると、Claude Code は `bypassPermissions`、Codex は承認なし（`never`）＋
サンドボックスなし（`danger-full-access`）で実行します。AIの質問への回答と入力フォームだけは引き続き確認します。
「モデリングプロンプト」チャットには適用されません。

既定では、MCP が `readOnlyHint` を宣言したツールだけ自動承認します（設定で変更可）。

### セキュリティ

- バックエンドは `127.0.0.1` のみで待ち受け、起動ごとのランダムトークンが全リクエストに必要
- アプリ以外の Origin からのリクエストは拒否
- 添付ファイルはアプリのデータフォルダにコピーして ID で参照（任意パスの読み取り・配信はしない）
- バックエンドは Windows のジョブオブジェクトで子プロセス（AI CLI・MCP サーバー）を束ね、
  アプリ終了・異常終了時に OS がまとめて終了させます。シェル側もバックエンドを監視・終了します
- バックエンドは常にウィンドウなしのコンソールを持ち、子プロセス（Claude Code・Codex・MCP サーバー・
  AI が実行するコマンド）はそれを引き継ぐため、ターミナルウィンドウは開きません
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
アドオンを `roxy-blender-mcp update` で更新したら、Blender を再起動してください。
アプリ側はウィンドウを閉じず、**設定 → Blender MCP → MCPを再起動**（または右パネルの同名ボタン）で更新を反映できます。
会話は保持され、AI側は次の送信でMCPへ接続し直します。AIの応答生成中は再起動できません。
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
