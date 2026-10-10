# Roxy AI

Blender と Unreal Engine 5 のための AI チャットデスクトップアプリです。Claude・OpenAI Codex・ローカルLLM を切り替えながら、
自然言語で Blender でのアセット制作と UE5 でのゲーム開発を行えます。（Claude は Claude Code CLI を通じて利用します）

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

応答中も送信ボタンまたは Enter で追加メッセージを送れます。Codex・ローカルLLMは進行中のターンへ追加指示を渡し、
Claudeは同じセッションへ追加入力を送ります。添付にも対応し、中断ボタンは送信ボタンの隣に表示します。
AIの準備中・中断中や送信に失敗した場合は、入力欄に内容を残します。

サイドバーの「設定画生成」から、3Dモデリング用の設定画の相談・プロンプト作成・画像生成を行えます。新しい設定画チャットはCodexを選択して開きます。

裏面・底面の正投影図、厚みと断面寸法、接合部、装飾の寸法も対象に応じて補足します。情報が多い対象は全体シートと補足シートに分け、寸法線の測定範囲や図どうしの整合性も確認するよう指示します。1枚にまとめたい場合は、その指定を優先します。
正投影の四面図、寸法、細部の拡大図、接合構造、斜め3方向の補助図、素材、図どうしの整合性を整理します。参考画像の添付と制作条件の編集にも対応します。
Codexで「この条件で設定画を生成して」と送ると、利用中のアカウント・モデルで画像生成が使える場合に、生成画像をチャット内でプレビュー表示します。生成に使用したプロンプト全体もコピーできるコードブロックで表示し、画像の修正時にも今回使用したプロンプトを提示します。
Claude・ローカルLLMでは条件相談とプロンプト作成を利用でき、画像生成はCodexへ切り替えて行います。生成画像のプレビューはバックエンド終了時に消えるため、残したい画像は拡大表示の「ダウンロード」で保存してください。

### 表示言語

「設定 → 一般 → 言語」で日本語・Englishを切り替えられます。画面表示はすぐに切り替わり、設定は次回起動にも保存されます。
AIの回答には次のメッセージから選択した言語を適用します。会話履歴・プロジェクト名・ツールの出力は元の内容を保持します。

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

### Unreal Engine 5（ゲーム開発）

Blender に加えて、Unreal Editor 内で動く MCP サーバー（既定 `http://127.0.0.1:8000/mcp`、エディタの MCP プラグイン）にも対応しています。

- Claude Code / Codex / ローカルLLM に UE の MCP（名前 `unreal-mcp`、各CLIの既存設定と同名なので重複しません）を渡し、
  AI が `list_toolsets` → `describe_toolset` → `call_tool` でレベル・アクタ・ブループリント・マテリアル・PIE などを操作します
- アプリは UE MCP の `EditorToolset.EditorAppToolset` の読み取り系ツール（`GetSelectedActors` / `IsPIERunning` /
  `GetCameraTransform` / `CaptureViewport`）だけを使い、右パネルの「UE5」タブに接続状態・ビューポート・レベル・選択アクタを表示します
- エディタ未起動時は「UE5未起動」と表示し、起動すると自動で接続します
- 同じ Unreal Editor に対して同時に AI を実行できるのは 1 会話だけです（競合防止）
- Blender で作ったアセットの UE5 への持ち込みは、Blender MCP の書き出し機能を使います
- 設定 → Unreal Engine でアドレス・有効/無効・AIへの受け渡し・自動プレビューを変更できます
- 右パネル Blender タブの「UE5へ送る」：選択中のオブジェクトを、Blender MCP の `export_to_unreal`（書き出し → UE MCP でインポート →
  `verify` でサイズ確認 → レベルに配置）の手順で UE5 に持ち込むよう AI に依頼します（処理は各 MCP が行い、アプリは依頼文を送るだけ）
- Blender のポートが応答しないとき、同じポートを使う別のアプリ／Blender アドオン（Blender 公式の「MCP」拡張など）を自動で調べ、
  「ポート競合」として原因と対処を表示します

### その他の MCP（Substance 3D Painter など）

**設定 → その他のMCP** に登録した MCP サーバーも AI に渡します。Blender・UE5 と同じく、操作は AI が MCP を直接使って行います。

- このアプリの隣に `..\MCP\Roxy-Substance-3D-Painter-MCP` があれば、初回起動時に自動で登録します（削除すれば再登録しません）
- Painter 側はブリッジプラグインが必要です（MCP フォルダーの `install_plugin.ps1`）。Painter が 9877 番ポートで待ち受けると、上部に「Substance 3D Painter」が接続中と表示されます
- 承認：`get_` / `list_` / `search_` / `check_` で始まる読み取り操作とスクリーンショットは確認なしで実行し、それ以外は毎回確認します（設定で変更可）
- ほかの MCP は「MCP サーバーを追加」から、フォルダー＋起動スクリプト名、起動コマンド、または URL を指定して登録できます

### ローカルLLM（LM Studio などは不要）

上部バーの「ローカルLLM」で、PC上のモデルを使えます。外部への送信はなく、ログインも不要です。

- **アプリ内蔵（既定）**：アプリが llama.cpp の `llama-server` を起動し、GGUF モデル（`.gguf`）を直接読み込みます。
  保存先を指定した場合は、そのフォルダと明示的に追加したフォルダだけを検索します。
  保存先が未指定の場合は `%APPDATA%\RoxyBlenderAI\models` と LM Studio などの保存先から自動で見つけ、
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
UE5 の MCP は読み取り専用の宣言がないため、ツール名で判定します（`Get` / `Is` / `Search` / `Find` / `Capture` などで始まる操作、
ツールセットの一覧・説明は自動承認。選択変更やスクリプト実行は確認あり）。設定 → Unreal Engine で、読み取りの自動承認と、
UE5 のツールをすべて自動承認する「UE5 フルアクセス」を切り替えられます。

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

Blender用MCPが未導入なら、アプリ起動時または制作メッセージ送信時に、アプリのデータフォルダへ自動導入します。
Windowsでは `uv` と Python 3.10 も不足時に自動で用意します。初回はインターネット接続が必要です。
既存のMCPフォルダ・カスタム起動コマンドを優先し、Blenderアドオンは未導入の場合だけインストールします。
Blenderが起動中の場合や初回起動前の場合は、Blenderの環境設定でアドオンを有効にし、Roxy Blender MCPパネルから接続してください。
Unreal Engine用MCPは任意です。自動導入せず、必要な場合だけ設定で接続を有効にしてください。
依存関係を更新する場合は、MCP クライアントを終了して MCP フォルダーで `uv sync` を実行してください。
自分で起動コマンドを指定している場合は、その設定が優先されます。

### ブラウザでのデバッグ（Tauri なし）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-browser.ps1
# 表示された http://127.0.0.1:1420/?port=…&token=… をブラウザで開く
```

### Windows インストーラーのビルド

このチェックアウトから起動した Roxy AI を閉じてからビルドしてください。
実行中の exe は Windows が使用中としてロックするため、ビルドでは上書きできません。使用中の場合は、ビルド開始時に対象ファイルと終了の案内を表示します。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
# → app\src-tauri\target\release\bundle\nsis\Roxy AI_0.1.0_x64-setup.exe
```

バックエンドは PyInstaller で単体 exe（Tauri サイドカー）になります。Claude Agent SDK 同梱の CLI
（約 250MB）は含めず、PC にインストール済みの Claude Code CLI を使います。

#### 他の人に配布する場合

```powershell
$env:ROXY_SIGN_THUMBPRINT = "<コード署名証明書の拇印>"   # 任意（署名しない場合は省略）
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Distribution
```

- `-Distribution`：Claude の認証方式の既定を **API キー方式** にします。Anthropic は第三者アプリで claude.ai のログイン
  （サブスクリプション）を提供することを認めていないため、配布版の利用者には各自の API キーを使ってもらいます
- `ROXY_SIGN_THUMBPRINT`：Windows 証明書ストアにあるコード署名証明書の拇印を指定すると、バックエンド・アプリ本体・
  インストーラーに署名とタイムスタンプを付けます（Windows SDK の `signtool` が必要）。署名なしだと SmartScreen が警告を出します
- タイムスタンプサーバーは `ROXY_SIGN_TIMESTAMP` で変更できます（既定 `http://timestamp.digicert.com`）

#### コード署名（SignPath Foundation）

配布用インストーラーは GitHub Actions（`.github/workflows/release.yml`）でビルドし、
[SignPath Foundation](https://signpath.org) の無償コード署名で署名します。
SignPath の設定（シークレット `SIGNPATH_API_TOKEN`、変数 `SIGNPATH_ORGANIZATION_ID` / `SIGNPATH_PROJECT_SLUG` /
`SIGNPATH_SIGNING_POLICY`）が無い間は、未署名のインストーラーを成果物としてアップロードします。
`v0.1.0` のようなタグを push すると、インストーラー付きのリリース（下書き）を作成します。

### macOS 版

Apple Silicon（M1 以降）の macOS 11 以降に対応しています。

```bash
./scripts/setup.sh   # backend/.venv と npm パッケージ
./scripts/dev.sh     # アプリ起動（Tauri + Vite + バックエンド）
./scripts/build.sh --distribution   # → app/src-tauri/target/release/bundle/dmg/Roxy AI_<版>_aarch64.dmg
```

- 前提：Xcode Command Line Tools、Node.js 20+、Rust、Python 3.10+、uv（`brew install uv`）、使う AI の CLI
- データは `~/Library/Application Support/RoxyBlenderAI` に保存します
- Finder から起動しても、ログインシェル（zsh など）の PATH を読み込むので、Homebrew や npm で入れた `claude` / `codex` / `uv` / `llama-server` を使えます
- ローカルLLM は `brew install llama.cpp` の `llama-server`（Metal 対応）を使います
- GitHub Actions（`.github/workflows/release.yml` の macos ジョブ）でも .dmg をビルドします
- 配布版はアドホック署名で、Apple の公証（有料の Apple Developer 登録が必要）はしていません。初回は
  .app を右クリック →「開く」、または「システム設定 → プライバシーとセキュリティ」の「このまま開く」で起動を許可してください。
  「壊れているため開けません」と出る場合は、ターミナルで `xattr -cr "/Applications/Roxy AI.app"` を実行してください

## コード署名ポリシー

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org).

- 署名対象は、このリポジトリのソースから GitHub Actions でビルドした成果物だけです
- コミッター・レビュアー・承認者：[RoxyCoding](https://github.com/RoxyCoding)（リポジトリのオーナー）
- 署名リクエストは、リリースごとにオーナーが手動で承認します

## プライバシー

このプログラムは、利用者が設定で選んだ AI サービス（Anthropic の Claude、OpenAI の Codex）と、
利用者が指定したモデルのダウンロード元（ollama.com、Hugging Face）以外に情報を送信しません。
送信は、利用者がメッセージを送ったとき、またはモデルのダウンロードを開始したときだけ行います。
ローカルLLM を選んだ場合、会話は PC の外へ送信されません。
各サービスでのデータの扱いは、それぞれのプライバシーポリシーに従います。
会話・設定・添付ファイルは PC 内（`%APPDATA%` 配下）にだけ保存され、作者へは何も送信されません。

## ライセンス

[GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0）。ロゴとアイコン（`Asset/`、`app/public/`、
`app/src-tauri/icons/`）も同じライセンスです。改変版を配布する場合や、ネットワーク越しに利用させる場合は、
その改変版のソースコードも AGPL-3.0 で公開する必要があります。
