"""Chat orchestration: runs, streaming, approvals, locking, hand-off, export."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .adapters.base import (
    AdapterUnavailable,
    AIAdapter,
    ApprovalDecision,
    ApprovalRequest,
    InputPart,
    RunRequest,
    RunResult,
    SessionInfo,
    SessionLostError,
    StatusUpdate,
    TextDelta,
    ToolEnd,
    ToolStart,
    Usage,
    mcp_tool_parts,
)
from .attachments import AttachmentStore
from .config import Paths, SettingsStore
from .context import PROVIDER_NAMES, plan_context, render_handoff
from .db import Database, new_id
from .logs import op
from .mcp_monitor import BlenderMonitor, mcp_server_command
from . import extra_mcp
from .unreal_monitor import unreal_call_is_read_only
from .workflow import ModelingBrief, ModelingProgress, REPORTS, visible_text

logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT = 20000
PERSIST_EVERY = 3.0


def _model_info(models: list, model: str):
    """The ModelInfo for `model`; an empty or "default" model means the provider default."""
    for m in models:
        if m.id == model:
            return m
    if not model or model == "default":
        return next((m for m in models if m.is_default), None)
    return None


class Conflict(Exception):
    """Another run blocks this one. Message is user-facing."""


@dataclass
class Run:
    id: str
    conversation_id: str
    project_id: str
    provider: str
    model: str
    user_message_id: str
    assistant_message_id: str
    lock_key: str
    effort: str = ""
    mode: str = "modeling"
    progress: dict | None = None
    report_cursor: int = 0
    text: str = ""
    status_text: str = "準備中…"
    tool_calls: list[dict] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    session_id: str | None = None
    started_at: float = field(default_factory=time.time)
    approvals: dict[str, tuple[asyncio.Future, dict]] = field(default_factory=dict)
    cancel_requested: bool = False
    task: asyncio.Task | None = None
    last_persist: float = 0.0
    latest_input_seq: int = 0

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
            "progress": self.progress,
            "assistant_message_id": self.assistant_message_id,
            "text": self.text,
            "status_text": self.status_text,
            "tool_calls": self.tool_calls,
            "notices": self.notices,
            "started_at": self.started_at,
            "approvals": [req for _, req in self.approvals.values()],
            "cancel_requested": self.cancel_requested,
        }


class ChatService:
    def __init__(self, db: Database, settings: SettingsStore, paths: Paths, adapters: dict[str, AIAdapter],
                 attachments: AttachmentStore, monitor: BlenderMonitor,
                 publish: Callable[[dict], Awaitable[None]]):
        self.db = db
        self.settings = settings
        self.paths = paths
        self.adapters = adapters
        self.attachments = attachments
        self.monitor = monitor
        self.publish = publish
        self.runs: dict[str, Run] = {}  # conversation id -> active run
        self.locks: dict[str, str] = {}  # blender endpoint -> conversation id
        self.unreal = None  # UnrealMonitor, set by the API
        self.extra = None  # ExtraMcpMonitor, set by the API
        self._ue_preview_task: asyncio.Task | None = None
        self.session_allow: dict[str, set[str]] = {}
        self._preview_task: asyncio.Task | None = None
        self.mcp_restarting = False

    # Helpers -----------------------------------------------------------------

    def adapter(self, provider: str) -> AIAdapter:
        a = self.adapters.get(provider)
        if a is None:
            raise AdapterUnavailable(f"不明なAI: {provider}")
        if not self.settings.get()["providers"].get(provider, {}).get("enabled", True):
            raise AdapterUnavailable(f"{a.name} は設定で無効になっています")
        return a

    @staticmethod
    def endpoint(project: dict) -> str:
        return f"{project['blender_host']}:{project['blender_port']}"

    def work_dir(self, project: dict) -> str:
        if project.get("work_dir") and Path(project["work_dir"]).is_dir():
            return project["work_dir"]
        d = self.paths.workspaces / project["id"]
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def _unreal_prompt_lines(self) -> list[str]:
        ue = self.settings.get().get("unreal") or {}
        if not (ue.get("enabled", True) and ue.get("inject_into_ai", True)):
            return []
        name = ue.get("server_name") or "unreal-mcp"
        lines = [
            f"Unreal Engine 5 のゲーム開発（レベル、アクタ、ブループリント、マテリアル、PIE など）は、MCP サーバー「{name}」"
            "（Unreal Editor 内で動作）のツールで行ってください。list_toolsets / describe_toolset で使えるツールを確認してから"
            "call_tool で実行し、そのサーバーの指示に従ってください。",
            "Blender で作ったアセットを UE5 に持ち込む場合は、Blender MCP 側の書き出し機能を使ってください。",
        ]
        if self.unreal is not None and self.unreal.state.get("status") != "connected":
            lines.append("現在 Unreal Editor には接続されていません。UE の操作が必要な場合は、エディタの起動をユーザーに依頼してください。")
        return lines

    def _extra_prompt_lines(self) -> list[str]:
        lines = []
        for entry in extra_mcp.enabled_servers(self.settings.get()):
            if entry.get("prompt"):
                lines.append(entry["prompt"])
            elif entry.get("label"):
                lines.append(f"{entry['label']} の操作は MCP サーバー「{entry['name']}」のツールで行ってください。")
            if (entry.get("offline_hint") and self.extra is not None
                    and self.extra.status(entry["name"]) == "offline"):
                lines.append(entry["offline_hint"])
        return lines

    def _system_append(self, project: dict, mode: str = "modeling") -> str:
        language = "English" if self.settings.get()["ui"].get("language") == "en" else "Japanese"
        language_instruction = f"Respond in {language}, including questions and the final prompt, unless the user explicitly requests another language.\n"
        if mode in ("prompt", "design_prompt"):
            brief_instruction = (
                '確定した条件が変わるたび、変更した項目だけを```roxy-briefのJSONブロックで報告してください。'
                '使用できるキーはtarget（作るもの）、use（用途）、shape（形・構造）、dimensions（寸法）、materials（素材）、colors（色）です。'
                '値は文字列、取り消した条件は空文字にしてください。未確定の候補を確定条件として登録しないでください。'
                'このブロックは画面の条件一覧を更新するためのもので、本文で形式を説明しないでください。\n'
            )
        if mode == "design_prompt":
            return (
                language_instruction +
                "あなたはRoxyの設定画制作アシスタントです。3Dモデリングで形状・比率・構造を読み取れる設定画（デザインシート）の条件相談、プロンプト作成、画像生成を行ってください。\n"
                "対象、用途、デザイン、基本寸法、形状・構造、素材・色・質感、必要な細部を具体化してください。"
                "質問は一度に2〜3個までに絞り、候補から選んでもらうときは質問ツール（AskUserQuestion / request_user_input）を使ってください。"
                "未指定の条件は推奨案として示し、ユーザーが決めた条件を優先してください。\n"
                "参考画像があれば特徴を読み取り、複数の参考画像や図に矛盾があれば、どれを基準にするか確認してください。"
                "見えない部分を確定した事実として扱わず、補完案として示してください。\n"
                "設定画の画像生成とプロンプトには次の内容を必ず含めてください。\n"
                "1. 正面・背面・側面・上面の四面図。遠近感のない正投影で、共通の縮尺・向き・基準線に揃える。"
                "小口・留め具などのある反対側面や底面・天地が必要な対象では、その図も追加する。\n"
                "2. 少なくとも1つの基本寸法と単位、全体の幅・高さ・奥行きの比率。寸法が未定なら推奨値を仮寸法と明記する。"
                "数値を決められない場合は、手や人物などの大きさの基準を別枠に添える。\n"
                "3. 形が読み取りにくい細部の拡大図。留め具、蝶結び、角飾り、チャーム、薔薇などから対象に必要な部分を選ぶ。\n"
                "4. 接合構造が分かる図と注記。何が何にどう付くか、固定方法、重なり順、厚み、可動部分を示す。"
                "実物の写真が添付されていれば構造の参考に使い、架空の写真を参考資料として挙げない。\n"
                "5. 複雑な立体部分は斜め3方向からの補助図で奥行きを示す。計測用の正投影図とは別枠に配置する。\n"
                "6. 部位ごとの素材・色・質感のメモ（真鍮、白い革、青いエナメルなど）。\n"
                "7. 図どうしの整合性。同一の対象として輪郭、比率、厚み、装飾の位置・数、留め具、接合方法、配色を全図で統一する。"
                "正面を基準図とし、背面などの固有の違いは明記する。拡大図にも全体図と同じ構造を描く。\n"
                "8. 高解像度で細部を読み取れる、余白のある整理されたシート。各図に短いラベルを付け、図や寸法線を重ねない。"
                "強いパース、形を隠す影や背景、小さく潰れた細部を避ける。\n" +
                "モデリングで形状を決めるため、対象に必要な次の補足も画像生成とプロンプトに含めてください。"
                "裏面・底面の正投影図で機構、取付板、ボルト位置と間隔を示す。"
                "主要部の厚み・断面寸法、支柱の直径、張り出し量を示す。"
                "部品どうしの接合部を拡大し、接続位置、固定方法、重なりを示す。"
                "装飾の幅・高さ・中心位置を示し、刺繍や模様は必要に応じて単独の平面図も添える。"
                "各寸法線は測定の始点・終点を明確にし、寸法値・単位・ラベルの文字崩れと図どうしの矛盾を確認する。"
                "未確認の構造や寸法は提案・仮寸法として明記する。\n"
                "情報が多い対象は、全体シートと補足シートを分けて生成してください。"
                "全体シートは全体図・基本寸法・素材、補足シートは裏面・断面・接合部・装飾の詳細を中心とし、"
                "全シートで同じデザイン・縮尺基準・寸法を保つ。ユーザーが1枚を指定した場合はその指定を優先する。\n" +
                brief_instruction +
                "ユーザーが画像の生成を依頼したら、条件を整理してimage_generationツールで実際に設定画を生成してください。"
                "ネイティブのimage_generationを直接使用し、画像生成の準備としてSKILL.mdを読んだり、MCP経由のコマンドを実行したりしないでください。"
                "画像生成の依頼をプロンプトの提示だけで終えないでください。未指定の条件は推奨値を使い、仮寸法などの仮定を短く添えてください。"
                "生成結果は画像コンテンツとして返し、チャット内でプレビュー表示してください。"
                "画像生成と同時に、画像生成ツールに渡したプロンプト全体をコピーできる```textのコードブロックで必ず提示してください。"
                "ツールが修正済みプロンプトを返した場合は、送信したプロンプトと区別して示してください。"
                "修正を依頼されたら、元の設定画と確定条件を保って画像を修正し、今回の生成に使用したプロンプト全体も提示してください。\n"
                "設定画を生成・修正したら、その後に必ず、その設定画と確定条件をもとにしたBlender向けのモデリングプロンプトを"
                "```modelingのコードブロックで提示してください。画像生成用のプロンプトとは別のブロックにしてください。"
                "モデリングプロンプトは、添付される設定画を参考画像として使う前提で、別のチャットに貼るだけで制作できる内容にしてください。"
                "対象と用途、全体寸法と単位、部品構成、各部品の形状・寸法・厚み、接合方法と位置、素材・色・質感、"
                "ポリゴン数などの品質の目安、仕上げを含め、仮寸法や推測した構造はその旨を明記してください。\n"
                "プロンプトの作成だけを依頼された場合は、コピーできる設定画生成プロンプト全体を```textのコードブロックにまとめてください。"
                "画像生成ツールが利用できない場合は、生成したと偽らず、Codexへの切り替えや利用条件の確認を案内してください。\n"
                "このチャットでは設定画の相談・画像生成・プロンプト作成を行います。Blender操作やコマンド実行は行わないでください。"
                "ユーザーが保存を明示的に依頼しない限り、生成画像を指定フォルダへ保存する作業は行わないでください。\n"
                f"現在のプロジェクト: {project['name']}"
            )
        if mode == "prompt":
            return (
                language_instruction +
                "あなたはRoxyのモデリングプロンプト相談役です。作るものの検討からBlender向けの制作指示まで相談に乗ってください。\n"
                "作るものが未定なら、用途・好み・難易度を確認し、具体的な候補を2〜3案提案してください。"
                "確認する質問は一度に2〜3個までに絞ってください。参考画像や三面図があれば形状や特徴を読み取ってください。\n"
                "候補から選んでもらうときは、質問ツール（AskUserQuestion / request_user_input）を使って選択肢を表示してください。\n"
                "対象、用途、形状・構造、寸法・比率、材質・色・質感、必要な細部、品質、仕上げを相談して具体化してください。"
                "未指定の条件は推奨案として示し、ユーザーが決めた条件を優先してください。\n"
                + brief_instruction +
                "条件がまとまったら、別のチャットに貼るだけで使えるモデリングプロンプトを```textのコードブロックにまとめてください。"
                "その後の修正相談には、修正済みのプロンプト全体を提示してください。\n"
                "このチャットでは相談とプロンプト作成だけを行います。Blender操作、画像生成、コマンド実行、ファイルの保存・変更は行わないでください。\n"
                f"現在のプロジェクト: {project['name']}"
            )
        name = self.settings.get()["mcp"].get("server_name") or "blender"
        lines = [
            "あなたはデスクトップアプリ「Roxy AI」から利用されています。",
            f"Blenderの操作・確認は MCP サーバー「{name}」(Roxy-BlenderMCP-v2) のツールで行い、"
            "そのサーバーが示す指示・ガイド・ルールに従ってください。",
            *self._unreal_prompt_lines(),
            *self._extra_prompt_lines(),
            language_instruction,
            "現在の制作条件が添えられている場合は、その条件を制作の基準にしてください。"
            "引き継いだプロンプトや参考画像と食い違う場合も、制作条件を優先してください。"
            "ユーザーが今回のメッセージで明示的に条件の変更を依頼した場合は、その変更を優先してください。",
            "画像生成の結果は画像コンテンツとして返し、プレビュー表示してください。"
            "ユーザーが明示的に保存を依頼しない限り、生成画像をファイルに保存しないでください。",
            "画像生成後は、保存指定がないことや、保存・追加編集をしていないことを毎回説明しないでください。"
            "画像の説明は必要な場合だけ短く添えてください。",
            'モデリング制作では、工程の開始・完了時に```roxy-progressのJSONブロックで進捗を報告してください。'
            '形式は{"stage":"shape","completed":[],"detail":"形状を作成中"}です。'
            'stageはshape（形状）、details（細部）、materials（素材）、finish（仕上げ）、done（制作完了）。'
            'completedは実際に完了した工程のキーの配列です。作業を省略した場合は完了扱いにせず、detailに記してください。'
            '実行結果を確認できた工程だけを完了にし、制作が完了するまでdoneを報告しないでください。'
            '相談やシーン確認だけの依頼では制作の進捗を報告しないでください。このブロックの形式は本文で説明しないでください。',
            f"現在のプロジェクト: {project['name']}",
        ]
        if project.get("blend_path"):
            lines.append(f"関連付けられた .blend ファイル: {project['blend_path']}")
        return "\n".join(lines)

    def _mcp_servers(self, project: dict) -> dict[str, dict]:
        s = self.settings.get()
        servers = {}
        if s["mcp"].get("inject_into_ai", True):
            servers[s["mcp"].get("server_name") or "blender"] = mcp_server_command(
                s, project["blender_host"], int(project["blender_port"]))
        ue = s.get("unreal") or {}
        if ue.get("enabled", True) and ue.get("inject_into_ai", True) and ue.get("url"):
            # The UE MCP is an HTTP server inside Unreal Editor; AIs connect to it directly.
            servers[ue.get("server_name") or "unreal-mcp"] = {"url": ue["url"]}
        for entry in extra_mcp.enabled_servers(s):
            spec = extra_mcp.server_spec(entry)
            if spec and entry["name"] not in servers:
                servers[entry["name"]] = spec
        return servers

    def active_runs(self) -> list[dict]:
        return [r.snapshot() for r in self.runs.values()]

    # Starting runs -------------------------------------------------------------

    def _check_free(self, conversation_id: str, project: dict) -> str:
        if self.mcp_restarting:
            raise Conflict("MCPを再起動中です。完了してから送信してください。")
        if conversation_id in self.runs:
            raise Conflict("この会話は応答を生成中です。完了または中断してから送信してください。")
        mode = (self.db.get_conversation(conversation_id) or {}).get("mode")
        if mode in ("prompt", "design_prompt"):
            return f"{mode}:{conversation_id}"
        keys = [self.endpoint(project)]
        ue = self.settings.get().get("unreal") or {}
        if ue.get("enabled", True) and ue.get("inject_into_ai", True) and ue.get("url"):
            keys.append(f"unreal:{ue['url']}")  # one AI at a time per Unreal Editor too
        labels = {}
        for entry in extra_mcp.enabled_servers(self.settings.get()):
            if extra_mcp.server_spec(entry):  # and per app driven by an extra server
                labels[f"extra:{entry['name']}"] = entry.get("label") or entry["name"]
        keys += labels
        for key in keys:
            holder = self.locks.get(key)
            if holder and holder != conversation_id:
                conv = self.db.get_conversation(holder) or {}
                target = ("Unreal Editor" if key.startswith("unreal:")
                          else labels.get(key) or f"Blender（{key}）")
                raise Conflict(
                    f"同じ{target}に対して別の会話「{conv.get('title') or '無題'}」がAIを実行中です。"
                    "競合を防ぐため、完了または中断してから送信してください。"
                )
        return "|".join(keys)

    async def restart_mcp(self, project: dict) -> dict:
        if self.mcp_restarting:
            raise Conflict("MCPを再起動中です。")
        if self.runs:
            raise Conflict("AIの応答生成中はMCPを再起動できません。完了または中断してから再起動してください。")
        self.mcp_restarting = True
        try:
            for adapter in self.adapters.values():
                await adapter.shutdown()
            await self.monitor.start(project["blender_host"], project["blender_port"])
            op("mcp.restart", project=project["id"])
            return self.monitor.public_state()
        finally:
            self.mcp_restarting = False

    def _latest_generated_images(self, conversation_id: str) -> list[tuple[bytes, str]]:
        """Images of the newest image generation in this chat, while their previews are still in memory."""
        for message in reversed(self.db.list_messages(conversation_id)):
            if message["role"] != "assistant":
                continue
            for tc in reversed(message.get("tool_calls") or []):
                found = [self.attachments.previews[i] for i in tc.get("images") or [] if i in self.attachments.previews]
                if tc.get("name") == "image_generation" and found:
                    return found
        return []

    async def handoff_prompt(self, conversation_id: str, text: str) -> dict:
        if not text.strip():
            raise ValueError("引き継ぐプロンプトが空です。")
        source = self.db.get_conversation(conversation_id)
        if not source:
            raise KeyError(conversation_id)
        if source["mode"] not in ("prompt", "design_prompt"):
            raise ValueError("相談チャットから制作チャットへ引き継いでください。")
        if conversation_id in self.runs:
            raise Conflict("相談の応答が完了してから引き継いでください。")
        self.adapter(source["provider"])
        ids = list(dict.fromkeys(aid for m in self.db.list_messages(conversation_id) if m["role"] == "user" for aid in m["attachments"]))
        attachments = self.db.get_attachments(ids)
        if len(attachments) != len(ids):
            raise ValueError("相談に添付されたファイルが見つかりません。添付し直してから引き継いでください。")
        images = [a for a in attachments if a["mime"].startswith("image/")]
        generated = self._latest_generated_images(conversation_id) if source["mode"] == "design_prompt" else []
        conv = self.db.create_conversation(source["project_id"], source["provider"], source["model"], effort=source["effort"])
        copied = []
        try:
            for image in images:
                path = self.attachments.file_for(image)
                if path is None:
                    raise ValueError(f"参考画像「{image['name']}」が見つかりません。添付し直してから引き継いでください。")
                # Each chat owns its copy, so deleting either chat preserves the other.
                data = await asyncio.to_thread(path.read_bytes)
                copied.append(await asyncio.to_thread(self.attachments.save_bytes, data, image["name"], image["mime"], "upload", conv["id"]))
            for i, (data, mime) in enumerate(generated, 1):  # the design sheet the prompt describes
                copied.append(await asyncio.to_thread(self.attachments.save_bytes, data, f"設定画{i}.png", mime, "upload", conv["id"]))
            conv = self.db.update_conversation(conv["id"], {"brief": source["brief"], "draft": {
                "text": text, "attachment_ids": [a["id"] for a in copied],
            }})
        except Exception:
            for path in self.db.delete_conversation(conv["id"]):
                self.attachments.delete_files(path)
            raise
        await self.publish({"type": "conversation.updated", "conversation": conv})
        return {"conversation": conv, "attachments": copied}

    async def send(self, conversation_id: str, text: str, attachment_ids: list[str],
                   provider: str, model: str, effort: str = "") -> dict:
        conv = self.db.get_conversation(conversation_id)
        if not conv:
            raise KeyError(conversation_id)
        project = self.db.get_project(conv["project_id"])
        if conversation_id in self.runs:
            return await self._steer(self.runs[conversation_id], text, attachment_ids, provider, model)
        lock_key = self._check_free(conversation_id, project)
        self.adapter(provider)
        if not text.strip() and not attachment_ids:
            raise ValueError("メッセージが空です")

        self.db.assign_attachments(attachment_ids, conversation_id)
        user_msg = self.db.add_message(conversation_id, "user", text, provider=provider, model=model,
                                       attachments=attachment_ids)
        fields: dict[str, Any] = {"provider": provider, "model": model, "effort": effort, "draft": {}}
        if not conv["title"]:
            first = text.strip().splitlines()[0] if text.strip() else "画像の相談"
            fields["title"] = first[:40]
        conv = self.db.update_conversation(conversation_id, fields)
        assistant = self.db.add_message(conversation_id, "assistant", "", provider=provider, model=model,
                                        status="streaming")
        run = Run(new_id(), conversation_id, project["id"], provider, model, user_msg["id"], assistant["id"], lock_key,
                  effort=effort, mode=conv["mode"])
        self.runs[conversation_id] = run
        for key in lock_key.split("|"):
            self.locks[key] = conversation_id
        self.db.touch_project(project["id"])
        await self.publish({"type": "conversation.updated", "conversation": conv})
        await self.publish({"type": "message.created", "message": user_msg})
        await self.publish({"type": "message.created", "message": assistant})
        await self.publish({"type": "run.started", "run": run.snapshot()})
        op("run.start", conversation=conversation_id, provider=provider, model=model, effort=effort, run=run.id)
        run.task = asyncio.create_task(self._execute(run, user_msg, project))
        return {"run": run.snapshot(), "user_message": user_msg, "assistant_message": assistant}

    async def _steer(self, run: Run, text: str, attachment_ids: list[str], provider: str, model: str) -> dict:
        if run.cancel_requested:
            raise Conflict("中断中です。完了してから送信してください。")
        if provider != run.provider or model != run.model:
            raise Conflict("実行中の追加指示は、同じAIとモデルで送信してください。")
        if not text.strip() and not attachment_ids:
            raise ValueError("メッセージが空です")
        attachments = self.db.get_attachments(attachment_ids)
        if len(attachments) != len(set(attachment_ids)):
            raise ValueError("添付ファイルが見つかりません。添付し直してください。")
        if any(a.get("conversation_id") not in (None, run.conversation_id) for a in attachments):
            raise ValueError("別の会話の添付ファイルは送信できません。")
        adapter = self.adapter(run.provider)
        status = await adapter.status()
        supports_images = status.supports_images
        if hasattr(adapter, "model_supports_images"):
            supports_images = adapter.model_supports_images(run.model)
        prepared = self.attachments.prepare(attachments, supports_images=supports_images,
            mimes=adapter.image_mimes(), max_edge=adapter.max_image_edge(), provider_name=adapter.name)
        language = "English" if self.settings.get()["ui"].get("language") == "en" else "Japanese"
        parts = ([InputPart("text", text=text)] if text.strip() else []) + prepared.parts
        if not parts:
            raise ValueError("送信できる添付ファイルがありません。添付し直してください。")
        parts.insert(0, InputPart("text", text=f"The user selected {language} for replies. Respond in {language} unless explicitly asked otherwise."))
        await adapter.steer(run.conversation_id, parts)
        self.db.assign_attachments(attachment_ids, run.conversation_id)
        msg = self.db.add_message(run.conversation_id, "user", text, provider=run.provider,
                                  model=run.model, attachments=attachment_ids)
        run.latest_input_seq = max(run.latest_input_seq, msg["seq"])
        if self.runs.get(run.conversation_id) is not run:
            session = self.db.get_ai_session(run.conversation_id, run.provider)
            if session:
                self.db.set_ai_session(run.conversation_id, run.provider, session["session_id"],
                                       max(session["synced_seq"], msg["seq"]), run.model)
        conv = self.db.update_conversation(run.conversation_id, {"draft": {}})
        await self.publish({"type": "message.created", "message": msg})
        await self.publish({"type": "conversation.updated", "conversation": conv})
        for notice in prepared.notices:
            await self._notice(run, notice)
        return {"run": run.snapshot(), "user_message": msg}

    async def edit_and_resend(self, conversation_id: str, message_id: str, text: str,
                              attachment_ids: list[str] | None, provider: str, model: str,
                              effort: str = "") -> dict:
        msg = self.db.get_message(message_id)
        if not msg or msg["conversation_id"] != conversation_id or msg["role"] != "user":
            raise KeyError(message_id)
        conv = self.db.get_conversation(conversation_id)
        self._check_free(conversation_id, self.db.get_project(conv["project_id"]))
        ids = msg["attachments"] if attachment_ids is None else attachment_ids
        await self._rewind(conversation_id, msg["seq"])
        return await self.send(conversation_id, text, ids, provider, model, effort)

    async def retry(self, conversation_id: str, provider: str, model: str, effort: str = "") -> dict:
        msgs = self.db.list_messages(conversation_id)
        last_user = next((m for m in reversed(msgs) if m["role"] == "user"), None)
        if not last_user:
            raise ValueError("再送信できるメッセージがありません")
        return await self.edit_and_resend(conversation_id, last_user["id"], last_user["content"],
                                          last_user["attachments"], provider, model, effort)

    async def _rewind(self, conversation_id: str, seq: int) -> None:
        """Drop messages from `seq` on. The AIs' internal sessions contain the old
        branch, so they are dropped too and the next turn hands over the text."""
        self.db.delete_messages_from(conversation_id, seq)
        conv = self.db.get_conversation(conversation_id)
        if conv["mode"] in ("prompt", "design_prompt"):
            previous = next((m["meta"]["brief"] for m in reversed(self.db.list_messages(conversation_id)) if "brief" in m["meta"]), {})
            conv = self.db.update_conversation(conversation_id, {"brief": previous})
            await self.publish({"type": "conversation.updated", "conversation": conv})
        self.db.clear_ai_sessions(conversation_id)
        for a in self.adapters.values():
            await a.forget(conversation_id)
        await self.publish({"type": "conversation.rewound", "conversation_id": conversation_id, "from_seq": seq})

    # Executing ---------------------------------------------------------------------

    async def _execute(self, run: Run, user_msg: dict, project: dict) -> None:
        final_status, error = "error", ""
        result: RunResult | None = None
        try:
            result = await self._run_with_context(run, user_msg, project, fresh=False)
            final_status, error = result.status, result.error
        except AdapterUnavailable as e:
            error = str(e)
        except asyncio.CancelledError:
            final_status, error = "cancelled", ""
        except Exception as e:
            logger.exception("run failed")
            error = f"予期しないエラー: {e}"
        finally:
            for fut, _ in run.approvals.values():
                if not fut.done():
                    fut.set_result(ApprovalDecision(allow=False, cancel_turn=True, message="実行が終了しました"))
            if run.cancel_requested and final_status != "completed":
                final_status, error = "cancelled", ""
            for tc in run.tool_calls:
                if tc["status"] == "running":
                    tc["status"] = "cancelled" if final_status == "cancelled" else "unknown"
            assistant = self.db.get_message(run.assistant_message_id)
            if assistant is not None:
                meta = {"notices": run.notices, "usage": run.usage, "duration": time.time() - run.started_at,
                        "effort": run.effort, "progress": run.progress}
                if run.mode in ("prompt", "design_prompt"):
                    meta["brief"] = self.db.get_conversation(run.conversation_id)["brief"]
                if result and result.model:
                    meta["resolved_model"] = result.model
                msg = self.db.update_message(run.assistant_message_id, {
                    "content": visible_text(run.text), "status": "complete" if final_status == "completed" else final_status,
                    "error": error,
                    "tool_calls": run.tool_calls, "meta": meta,
                })
                if run.session_id:
                    self.db.set_ai_session(run.conversation_id, run.provider, run.session_id,
                                           max(msg["seq"], run.latest_input_seq), run.model)
                await self.publish({"type": "message.updated", "message": msg})
            self.runs.pop(run.conversation_id, None)
            for key in run.lock_key.split("|"):
                if self.locks.get(key) == run.conversation_id:
                    self.locks.pop(key, None)
            await self.publish({"type": "run.finished", "conversation_id": run.conversation_id, "run_id": run.id,
                                "status": final_status, "error": error})
            op("run.end", conversation=run.conversation_id, run=run.id, status=final_status, error=error,
               tools=len(run.tool_calls), seconds=round(time.time() - run.started_at, 1))

    async def _run_with_context(self, run: Run, user_msg: dict, project: dict, fresh: bool) -> RunResult:
        adapter = self.adapter(run.provider)
        settings = self.settings.get()
        if run.mode == "modeling" and settings["mcp"].get("inject_into_ai", True):
            await self._on_event(run, StatusUpdate("Blender MCPの準備を確認しています…"))
            try:
                await self.monitor.ensure_installed()
            except RuntimeError as e:
                raise AdapterUnavailable(str(e)) from e
            settings = self.settings.get()
        messages = self.db.list_messages(run.conversation_id)
        session = None if fresh else self.db.get_ai_session(run.conversation_id, run.provider)
        session_id, ctx = plan_context(messages, session, user_msg["seq"])
        handoff = render_handoff(ctx, fresh=session_id is None,
                                 max_chars=int(settings["context"].get("handoff_max_chars", 40000)))
        if ctx:
            others = sorted({PROVIDER_NAMES.get(m.get("provider"), m.get("provider")) for m in ctx if m["role"] == "assistant"})
            if session_id is None:
                await self._notice(run, f"これまでの会話内容（{len(ctx)}件）を {adapter.name} に引き継ぎました。"
                                        "AI内部のセッション・ツール状態は新規です。")
            elif others:
                await self._notice(run, f"{adapter.name} の既存セッションを再開し、その間の {'/'.join(others)} とのやり取りを共有しました。")

        status = await adapter.status()
        effort = run.effort
        if effort:
            info = _model_info(status.models, run.model)
            if info is None or effort not in info.efforts:
                await self._notice(run, f"選択中のモデルは思考レベル「{effort}」に対応していないため、モデルの既定で実行します。")
                effort = ""
                run.effort = ""  # record what was actually used
        supports_images = status.supports_images
        if hasattr(adapter, "model_supports_images"):
            supports_images = adapter.model_supports_images(run.model)  # type: ignore[attr-defined]
        prepared = self.attachments.prepare(
            self.db.get_attachments(user_msg["attachments"]),
            supports_images=supports_images, mimes=adapter.image_mimes(),
            max_edge=adapter.max_image_edge(), provider_name=adapter.name,
        )
        for n in prepared.notices:
            await self._notice(run, n)
        language = "English" if settings["ui"].get("language") == "en" else "Japanese"
        parts: list[InputPart] = [InputPart("text", text=f"The user selected {language} for replies. Respond in {language} unless explicitly asked otherwise.")]
        if run.mode == "design_prompt" and run.provider != "codex":
            parts.append(InputPart("text", text="現在選択中のAIでは、このチャットで画像生成ツールを利用できません。条件相談とプロンプト作成には対応し、画像生成を依頼された場合はCodexへの切り替えを案内してください。"))
        if handoff:
            parts.append(InputPart("text", text=handoff))
        brief = self.db.get_conversation(run.conversation_id).get("brief") or {}
        if brief:
            parts.append(InputPart("text", text="現在の制作条件（ユーザーの明示的な変更指示があれば更新してください）:\n" + json.dumps(brief, ensure_ascii=False)))
        if user_msg["content"]:
            parts.append(InputPart("text", text=user_msg["content"]))
        parts.extend(prepared.parts)

        req = RunRequest(
            conversation_id=run.conversation_id,
            parts=parts,
            cwd=self.work_dir(project),
            model=run.model,
            effort=effort,
            session_id=session_id,
            system_append=self._system_append(project, run.mode),
            mcp_servers=self._mcp_servers(project) if run.mode == "modeling" else {},
            extra_dirs=[str(self.paths.attachments)],
            prompt_only=run.mode in ("prompt", "design_prompt"),
            image_generation=run.mode == "design_prompt" and run.provider == "codex",
            full_access=self.full_access_for(run),
        )
        async def emit(ev):
            await self._on_event(run, ev)

        async def approve(req: ApprovalRequest) -> ApprovalDecision:
            return await self._approve(run, req)

        try:
            return await adapter.run(req, emit, approve)
        except SessionLostError as e:
            if fresh:
                raise AdapterUnavailable(f"{adapter.name} のセッションを開始できませんでした: {e}")
            logger.info("Session lost for %s/%s: %s", run.conversation_id, run.provider, e)
            self.db.clear_ai_sessions(run.conversation_id, run.provider)
            await adapter.forget(run.conversation_id)
            await self._notice(run, f"{adapter.name} の以前のセッションが見つからなかったため、会話内容を引き継いで新しいセッションで続行します。")
            return await self._run_with_context(run, user_msg, project, fresh=True)

    async def _notice(self, run: Run, text: str) -> None:
        run.notices.append(text)
        await self.publish({"type": "run.notice", "conversation_id": run.conversation_id, "text": text})

    async def _persist_partial(self, run: Run) -> None:
        now = time.time()
        if now - run.last_persist > PERSIST_EVERY:
            run.last_persist = now
            meta = {"progress": run.progress}
            if run.mode in ("prompt", "design_prompt"):
                meta["brief"] = self.db.get_conversation(run.conversation_id)["brief"]
            self.db.update_message(run.assistant_message_id, {"content": visible_text(run.text), "tool_calls": run.tool_calls,
                                                            "meta": meta})

    async def _update_reports(self, run: Run) -> None:
        for match in REPORTS.finditer(run.text, run.report_cursor):
            run.report_cursor = match.end()
            try:
                if match[1] == "brief" and run.mode in ("prompt", "design_prompt"):
                    patch = ModelingBrief.model_validate_json(match[2]).model_dump(exclude_unset=True)
                    conv = self.db.get_conversation(run.conversation_id)
                    if patch and conv:
                        conv = self.db.update_conversation(run.conversation_id, {"brief": {**conv["brief"], **patch}})
                        await self.publish({"type": "conversation.updated", "conversation": conv})
                elif match[1] == "progress" and run.mode == "modeling":
                    run.progress = ModelingProgress.model_validate_json(match[2]).model_dump()
                    await self.publish({"type": "run.progress", "conversation_id": run.conversation_id, "progress": run.progress})
            except ValueError:
                logger.debug("Ignoring invalid %s report", match[1])

    async def _on_event(self, run: Run, ev: Any) -> None:
        cid = run.conversation_id
        if isinstance(ev, TextDelta):
            run.text += ev.text
            await self._update_reports(run)
            run.status_text = "応答中…"
            await self.publish({"type": "run.delta", "conversation_id": cid, "message_id": run.assistant_message_id,
                                "text": ev.text})
            await self._persist_partial(run)
        elif isinstance(ev, ToolStart):
            tc = {"id": ev.call_id, "name": ev.name, "server": ev.server, "tool": ev.tool, "input": ev.input,
                  "status": "running", "output": "", "images": [], "started_at": time.time(), "ended_at": None}
            run.tool_calls.append(tc)
            run.status_text = f"ツール実行中: {ev.name}"
            await self.publish({"type": "run.tool", "conversation_id": cid, "tool_call": tc, "status_text": run.status_text})
            op("tool.start", conversation=cid, run=run.id, tool=ev.name)
        elif isinstance(ev, ToolEnd):
            tc = next((t for t in run.tool_calls if t["id"] == ev.call_id), None)
            if tc is None:
                return
            output = ev.output if len(ev.output) <= MAX_TOOL_OUTPUT else ev.output[:MAX_TOOL_OUTPUT] + "\n…（省略）"
            images = []
            for mime, data in ev.images:
                images.append(self.attachments.preview_image(data, mime))
            tc.update(status="ok" if ev.ok else "error", output=output, images=images, ended_at=time.time())
            run.status_text = "考え中…"
            await self.publish({"type": "run.tool", "conversation_id": cid, "tool_call": tc, "status_text": run.status_text})
            op("tool.end", conversation=cid, run=run.id, tool=tc["name"], ok=ev.ok)
            blender = self.settings.get()["mcp"].get("server_name") or "blender"
            if tc["server"] == blender:
                await self.publish({"type": "blender.tool_result", "conversation_id": cid, "tool_call": tc})
                if ev.ok and not self.monitor.is_readonly_tool(tc["tool"]):
                    self._schedule_preview()
            elif tc["server"] == ((self.settings.get().get("unreal") or {}).get("server_name") or "unreal-mcp"):
                # Shown in the same "MCPの結果" list; the UE preview follows changes made by the AI.
                await self.publish({"type": "blender.tool_result", "conversation_id": cid, "tool_call": tc})
                if ev.ok and tc["tool"] == "call_tool":
                    self._schedule_unreal_preview()
            elif extra_mcp.find(self.settings.get(), tc["server"]):
                await self.publish({"type": "blender.tool_result", "conversation_id": cid, "tool_call": tc})
            await self._persist_partial(run)
        elif isinstance(ev, StatusUpdate):
            run.status_text = ev.text
            if ev.level == "warning":
                await self._notice(run, ev.text)
            await self.publish({"type": "run.status", "conversation_id": cid, "status_text": ev.text})
        elif isinstance(ev, SessionInfo):
            run.session_id = ev.session_id or run.session_id
        elif isinstance(ev, Usage):
            run.usage = ev.data

    def _schedule_unreal_preview(self) -> None:
        if self.unreal is None or not (self.settings.get().get("unreal") or {}).get("auto_preview", True):
            return
        if self._ue_preview_task and not self._ue_preview_task.done():
            self._ue_preview_task.cancel()

        async def later():
            await asyncio.sleep(2.0)
            try:
                await self.unreal.refresh(preview=True)
            except Exception as e:
                logger.debug("UE auto preview failed: %s", e)

        self._ue_preview_task = asyncio.create_task(later())

    def _schedule_preview(self) -> None:
        """After the AI changed the scene, refresh preview + scene facts (debounced)."""
        if not self.settings.get()["mcp"].get("auto_preview", True):
            return
        if self._preview_task and not self._preview_task.done():
            self._preview_task.cancel()

        async def later():
            await asyncio.sleep(2.0)
            try:
                await self.monitor.refresh(preview=True)
            except Exception as e:
                logger.debug("auto preview failed: %s", e)

        self._preview_task = asyncio.create_task(later())

    # Approvals ---------------------------------------------------------------------

    def full_access_for(self, run: Run) -> bool:
        """Full access never applies to the read-only prompt chats."""
        return run.mode not in ("prompt", "design_prompt") and bool(self.settings.get()["approval"].get("full_access"))

    async def _approve(self, run: Run, req: ApprovalRequest) -> ApprovalDecision:
        if run.mode == "design_prompt" and req.kind == "tool" and req.tool_name == "image_generation" and not req.server:
            return ApprovalDecision(allow=True)
        if run.mode in ("prompt", "design_prompt") and req.kind != "question":
            message = "このチャットでは設定画の相談・画像生成・プロンプト作成だけを行ってください。" if run.mode == "design_prompt" else "このチャットでは相談とプロンプト作成だけを行ってください。"
            return ApprovalDecision(allow=False, message=message)
        if self.full_access_for(run) and req.kind != "question" and not (req.schema or {}).get("properties"):
            # Questions and input forms still need the user's answer; everything else runs.
            op("approval.full_access", conversation=run.conversation_id, kind=req.kind, title=req.title)
            return ApprovalDecision(allow=True)
        s = self.settings.get()
        blender = s["mcp"].get("server_name") or "blender"
        allowed = self.session_allow.setdefault(run.conversation_id, set())
        key = req.tool_name or f"{req.kind}:{req.server}"
        if req.kind == "tool":
            server, tool = mcp_tool_parts(req.tool_name)
            req.read_only = server == blender and self.monitor.is_readonly_tool(tool)
            if key in allowed:
                return ApprovalDecision(allow=True)
            if server == blender and (s["approval"].get("auto_approve_blender_mcp")
                                      or (req.read_only and s["approval"].get("auto_approve_readonly_mcp", True))):
                op("approval.auto", conversation=run.conversation_id, tool=req.tool_name)
                return ApprovalDecision(allow=True)
            ue_name = (s.get("unreal") or {}).get("server_name") or "unreal-mcp"
            if server == ue_name:
                req.read_only = unreal_call_is_read_only(tool, req.input)
                if s["approval"].get("auto_approve_unreal_mcp") or (
                        req.read_only and s["approval"].get("auto_approve_unreal_readonly", True)):
                    op("approval.auto", conversation=run.conversation_id, tool=req.tool_name,
                       inner=(req.input or {}).get("tool_name") if isinstance(req.input, dict) else None)
                    return ApprovalDecision(allow=True)
            entry = extra_mcp.find(s, server)
            if entry:
                req.read_only = extra_mcp.is_read_only(entry, tool)
                if entry.get("auto_approve_all") or (req.read_only and entry.get("auto_approve_readonly", True)):
                    op("approval.auto", conversation=run.conversation_id, tool=req.tool_name)
                    return ApprovalDecision(allow=True)
        elif req.kind == "mcp_elicitation" and key in allowed and not req.schema:
            return ApprovalDecision(allow=True)

        aid = new_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        payload = {"id": aid, "conversation_id": run.conversation_id, **dataclasses.asdict(req)}
        run.approvals[aid] = (fut, payload)
        prev_status = run.status_text
        run.status_text = "承認待ち"
        await self.publish({"type": "approval.request", "approval": payload})
        op("approval.request", conversation=run.conversation_id, kind=req.kind, title=req.title)
        try:
            decision: ApprovalDecision = await fut
        finally:
            run.approvals.pop(aid, None)
            run.status_text = prev_status
            await self.publish({"type": "approval.resolved", "id": aid, "conversation_id": run.conversation_id})
        if decision.allow and decision.remember and req.allow_session:
            allowed.add(key)
        op("approval.decision", conversation=run.conversation_id, kind=req.kind, title=req.title,
           allow=decision.allow, remember=decision.remember, cancel=decision.cancel_turn)
        return decision

    def resolve_approval(self, approval_id: str, decision: ApprovalDecision) -> bool:
        for run in self.runs.values():
            item = run.approvals.get(approval_id)
            if item:
                fut, _ = item
                if not fut.done():
                    fut.set_result(decision)
                return True
        return False

    # Cancel --------------------------------------------------------------------------

    async def cancel(self, conversation_id: str) -> bool:
        run = self.runs.get(conversation_id)
        if not run:
            return False
        run.cancel_requested = True
        run.status_text = "中断しています…"
        await self.publish({"type": "run.status", "conversation_id": conversation_id, "status_text": run.status_text})
        for fut, _ in list(run.approvals.values()):
            if not fut.done():
                fut.set_result(ApprovalDecision(allow=False, cancel_turn=True, message="ユーザーが中断しました"))
        op("run.cancel", conversation=conversation_id, run=run.id)
        try:
            await asyncio.wait_for(self.adapter(run.provider).interrupt(conversation_id), 15)
        except Exception as e:
            logger.warning("interrupt failed: %s", e)

        async def hard_stop():
            await asyncio.sleep(20)
            if run.task and not run.task.done():
                logger.warning("Run %s did not stop after interrupt; cancelling", run.id)
                await self.adapter(run.provider).forget(conversation_id)
                run.task.cancel()

        asyncio.create_task(hard_stop())
        return True

    async def shutdown(self) -> None:
        for cid in list(self.runs):
            await self.cancel(cid)
        for run in list(self.runs.values()):
            if run.task:
                run.task.cancel()

    # Conversations ---------------------------------------------------------------------

    async def delete_conversation(self, conversation_id: str) -> None:
        if conversation_id in self.runs:
            await self.cancel(conversation_id)
            run = self.runs.get(conversation_id)
            if run and run.task:
                try:
                    await asyncio.wait_for(asyncio.shield(run.task), 25)
                except Exception:
                    pass
        for a in self.adapters.values():
            await a.forget(conversation_id)
        for path in self.db.delete_conversation(conversation_id):
            self.attachments.delete_files(path)
        self.session_allow.pop(conversation_id, None)
        op("conversation.delete", conversation=conversation_id)

    def export(self, conversation_id: str, fmt: str) -> Path:
        conv = self.db.get_conversation(conversation_id)
        if not conv:
            raise KeyError(conversation_id)
        project = self.db.get_project(conv["project_id"]) or {}
        messages = self.db.list_messages(conversation_id)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe_title = "".join(c for c in (conv["title"] or "chat") if c not in '<>:"/\\|?*').strip()[:40] or "chat"
        if fmt == "json":
            path = self.paths.exports / f"{safe_title}-{stamp}.json"
            data = {"conversation": conv, "project": project, "messages": messages}
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return path
        lines = [f"# {conv['title'] or '無題'}", "", f"- プロジェクト: {project.get('name', '')}",
                 f"- 作成: {time.strftime('%Y-%m-%d %H:%M', time.localtime(conv['created_at']))}", ""]
        for m in messages:
            if m["role"] == "user":
                lines.append("## ユーザー")
            elif m["role"] == "assistant":
                lines.append(f"## {PROVIDER_NAMES.get(m['provider'], m['provider'])}" + (f"（{m['model']}）" if m["model"] else ""))
            else:
                lines.append("## お知らせ")
            lines += ["", m["content"] or "", ""]
            for tc in m.get("tool_calls") or []:
                lines.append(f"> ツール `{tc.get('name')}`: {tc.get('status')}")
            atts = self.db.get_attachments(m.get("attachments") or [])
            for a in atts:
                lines.append(f"> 添付: {a['name']}")
            if m.get("error"):
                lines.append(f"> エラー: {m['error']}")
            lines.append("")
        path = self.paths.exports / f"{safe_title}-{stamp}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
