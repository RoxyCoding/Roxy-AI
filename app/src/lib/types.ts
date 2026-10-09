// Mirrors the backend's JSON (backend/roxy_ai).

export type ProviderId = "claude" | "codex" | string;

export type ModelingBrief = Partial<Record<"target" | "use" | "shape" | "dimensions" | "materials" | "colors", string>>;
export type ModelingStage = "shape" | "details" | "materials" | "finish";
export interface ModelingProgress {
  stage: ModelingStage | "done";
  completed: ModelingStage[];
  detail: string;
}
export interface ComposerSeed {
  text: string;
  attachments: Attachment[];
}

export interface Project {
  id: string;
  name: string;
  blend_path: string;
  work_dir: string;
  blender_host: string;
  blender_port: number;
  notes: string;
  created_at: number;
  updated_at: number;
  conversation_count?: number;
}

export interface Conversation {
  id: string;
  project_id: string;
  title: string;
  provider: ProviderId;
  model: string;
  effort: string; // thinking level, "" = model default
  mode: "modeling" | "prompt" | "design_prompt";
  brief: ModelingBrief;
  draft?: { text?: string; attachment_ids?: string[] };
  created_at: number;
  updated_at: number;
}

export interface ToolCall {
  id: string;
  name: string;
  server: string;
  tool: string;
  input: unknown;
  status: "running" | "ok" | "error" | "cancelled" | "unknown";
  output: string;
  images: string[];
  started_at: number;
  ended_at: number | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  seq: number;
  role: "user" | "assistant" | "notice";
  content: string;
  provider: ProviderId;
  model: string;
  status: "complete" | "streaming" | "error" | "cancelled";
  error: string;
  tool_calls: ToolCall[];
  attachments: string[];
  meta: { notices?: string[]; usage?: Record<string, unknown>; duration?: number; resolved_model?: string; effort?: string; progress?: ModelingProgress | null };
  created_at: number;
}

export interface Attachment {
  id: string;
  conversation_id: string | null;
  kind: "upload" | "tool_image" | "preview";
  name: string;
  mime: string;
  size: number;
  path: string;
}

export interface AiSession {
  provider: ProviderId;
  session_id: string;
  synced_seq: number;
  model: string;
}

export interface Approval {
  id: string;
  conversation_id: string;
  kind: "tool" | "command" | "file_change" | "permissions" | "mcp_elicitation" | "question";
  title: string;
  detail: string;
  tool_name: string;
  server: string;
  input: unknown;
  questions: { id: string; header: string; question: string; options: { label: string; description?: string }[]; multi: boolean }[];
  schema: { properties?: Record<string, { type?: string; title?: string; description?: string; enum?: string[]; default?: unknown }>; required?: string[] } | null;
  allow_session: boolean;
  read_only: boolean;
}

export interface RunSnapshot {
  id: string;
  conversation_id: string;
  provider: ProviderId;
  model: string;
  effort?: string;
  progress?: ModelingProgress | null;
  assistant_message_id: string;
  text: string;
  status_text: string;
  tool_calls: ToolCall[];
  notices: string[];
  started_at: number;
  approvals: Approval[];
  cancel_requested: boolean;
}

export interface ModelInfo {
  id: string;
  name: string;
  description: string;
  is_default: boolean;
  supports_images: boolean;
  efforts: string[]; // selectable thinking levels; empty = not selectable
  default_effort: string;
}

export interface ProviderStatus {
  id: ProviderId;
  name: string;
  available: boolean;
  version?: string;
  account?: string;
  auth_ok?: boolean;
  detail?: string;
  models: ModelInfo[];
  supports_images?: boolean;
  image_mimes?: string[];
}

export interface BlenderState {
  mcp: "stopped" | "starting" | "running" | "error";
  blender: "unknown" | "connected" | "disconnected" | "unresponsive" | "port_conflict";
  endpoint: string;
  error: string;
  server: { name?: string; version?: string };
  addon: { text: string } | null;
  tools: { name: string; description: string; read_only: boolean; app_only: boolean }[];
  scene: null | {
    text: string;
    scene?: string;
    counts?: string;
    object_counts?: Record<string, number>;
    active?: string | null;
    selected?: string[];
    selected_count?: number;
    mode?: string;
    file_line?: string;
    columns?: string[];
    rows?: string[];
    objects?: { name: string; type: string; fields: Record<string, string> }[];
    shown?: number;
    total?: number;
    version?: number;
    file?: string | null;
    is_dirty?: boolean;
    settings?: {
      file: string;
      engine: string;
      frames: [number, number, number];
      fps: number;
      resolution: [number, number];
      camera: string | null;
      world_hdri: string | null;
      unit_scale: number;
    };
    at: number;
  };
  preview: null | { attachment_id: string; at: number; info: Record<string, unknown> };
  updated_at: number;
}

export interface Settings {
  theme: "system" | "light" | "dark";
  default_provider: ProviderId;
  providers: {
    claude: { enabled: boolean; cli_path: string; default_model: string; default_effort: string; auth_mode: "cli" | "api_key"; permission_mode: string };
    codex: { enabled: boolean; cli_path: string; default_model: string; default_effort: string; sandbox: string; approval_policy: string };
    [k: string]: Record<string, unknown> & { enabled: boolean; default_model: string; default_effort: string };
  };
  mcp: {
    server_dir: string;
    command: string;
    args: string[];
    server_name: string;
    inject_into_ai: boolean;
    auto_preview: boolean;
    preview_max_size: number;
    monitor_enabled: boolean;
  };
  approval: { auto_approve_readonly_mcp: boolean; auto_approve_blender_mcp: boolean; full_access: boolean };
  context: { handoff_max_chars: number };
  extra_mcp?: { servers: ExtraMcpServer[]; auto_detected: string[] };
  ui: { right_panel_open: boolean; sidebar_open: boolean; language: "ja" | "en" };
  last_project_id: string | null;
}

export interface Bootstrap {
  version: string;
  settings: Settings;
  secrets: Record<string, boolean>;
  projects: Project[];
  active_project_id: string;
  providers: { id: ProviderId; name: string }[];
  blender: BlenderState;
  unreal?: UnrealState;
  extra_mcp?: ExtraMcpState;
  runs: RunSnapshot[];
  data_dir: string;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: Message[];
  attachments: Record<string, Attachment>;
  sessions: AiSession[];
  run: RunSnapshot | null;
}

export interface SearchHit {
  conversation_id: string;
  title: string;
  project_id: string;
  updated_at: number;
  snippet: string | null;
}

export type ServerEvent =
  | { type: "hello"; blender: BlenderState; unreal?: UnrealState; extra_mcp?: ExtraMcpState; runs: RunSnapshot[] }
  | { type: "unreal.state"; state: UnrealState }
  | { type: "extra_mcp.state"; state: ExtraMcpState }
  | { type: "pong" }
  | { type: "blender.state"; state: BlenderState }
  | { type: "blender.tool_result"; conversation_id: string; tool_call: ToolCall }
  | { type: "conversation.updated"; conversation: Conversation }
  | { type: "conversation.deleted"; conversation_id: string }
  | { type: "conversation.rewound"; conversation_id: string; from_seq: number }
  | { type: "message.created"; message: Message }
  | { type: "message.updated"; message: Message }
  | { type: "run.started"; run: RunSnapshot }
  | { type: "run.delta"; conversation_id: string; message_id: string; text: string }
  | { type: "run.tool"; conversation_id: string; tool_call: ToolCall; status_text: string }
  | { type: "run.status"; conversation_id: string; status_text: string }
  | { type: "run.progress"; conversation_id: string; progress: ModelingProgress }
  | { type: "run.notice"; conversation_id: string; text: string }
  | { type: "run.finished"; conversation_id: string; run_id: string; status: string; error: string }
  | { type: "approval.request"; approval: Approval }
  | { type: "approval.resolved"; id: string; conversation_id: string };

/** Another MCP server handed to the AIs (backend/roxy_ai/extra_mcp.py). */
export interface ExtraMcpServer {
  name: string;
  label?: string;
  enabled?: boolean;
  server_dir?: string;
  script?: string;
  command?: string;
  args?: string[];
  url?: string;
  probe_host?: string;
  probe_port?: number | null;
  read_only_prefixes?: string[];
  auto_approve_readonly?: boolean;
  auto_approve_all?: boolean;
  prompt?: string;
  offline_hint?: string;
}

/** Server name -> whether the app it drives answers on its port. */
export type ExtraMcpState = Record<string, "connected" | "offline">;

/** Unreal Editor MCP connection, as reported by backend/roxy_ai/unreal_monitor.py. */
export interface UnrealState {
  status: "disabled" | "waiting" | "connecting" | "connected" | "error";
  url: string;
  error: string;
  toolsets: string[];
  editor: null | { level: string; selected: string[]; selected_paths: string[]; pie: boolean; at: number };
  preview: null | { attachment_id: string; at: number };
  updated_at: number;
}
