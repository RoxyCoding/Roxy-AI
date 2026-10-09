import { t as tr } from "./i18n";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useReducer, useRef, type ReactNode } from "react";
import { setLanguage } from "./i18n";
import { EventSocket, del, get, patch, post } from "./backend";
import type {
  Approval,
  BlenderState,
  UnrealState,
  ExtraMcpState,
  Bootstrap,
  Conversation,
  ConversationDetail,
  ComposerSeed,
  Attachment,
  ModelingBrief,
  Message,
  Project,
  ProviderId,
  ProviderStatus,
  RunSnapshot,
  ServerEvent,
  Settings,
  ToolCall,
} from "./types";

export interface Toast {
  id: number;
  kind: "info" | "error" | "success";
  text: string;
  action?: { label: string; run: () => void };
}

export interface State {
  boot: Bootstrap;
  settings: Settings;
  projects: Project[];
  activeProjectId: string;
  conversations: Conversation[];
  currentId: string | null;
  detail: ConversationDetail | null;
  runs: Record<string, RunSnapshot>;
  approvals: Approval[];
  blender: BlenderState;
  unreal: UnrealState;
  extraMcp: ExtraMcpState;
  providers: ProviderStatus[];
  providersLoading: boolean;
  mcpResults: (ToolCall & { conversation_id: string })[];
  draftProvider: ProviderId;
  draftModel: string;
  draftEffort: string;
  draftMode: Conversation["mode"];
  composerSeed: ComposerSeed | null;
  wsConnected: boolean;
  toasts: Toast[];
}

type Action =
  | { type: "settings"; settings: Settings }
  | { type: "projects"; projects: Project[]; activeProjectId?: string }
  | { type: "conversations"; conversations: Conversation[] }
  | { type: "open"; id: string | null; detail: ConversationDetail | null }
  | { type: "providers"; providers: ProviderStatus[] | null; loading: boolean }
  | { type: "draft"; provider?: ProviderId; model?: string; effort?: string; mode?: Conversation["mode"] }
  | { type: "composerSeed"; seed: ComposerSeed | null }
  | { type: "ws"; connected: boolean }
  | { type: "toast"; toast: Toast }
  | { type: "untoast"; id: number }
  | { type: "event"; ev: ServerEvent }
  | { type: "removeMessagesFrom"; seq: number };

function upsert<T extends { id: string }>(list: T[], item: T, front = false): T[] {
  const i = list.findIndex((x) => x.id === item.id);
  if (i < 0) return front ? [item, ...list] : [...list, item];
  const copy = list.slice();
  copy[i] = item;
  return copy;
}

function onEvent(s: State, ev: ServerEvent): State {
  switch (ev.type) {
    case "hello":
      return {
        ...s,
        blender: ev.blender,
        unreal: ev.unreal ?? s.unreal,
        extraMcp: ev.extra_mcp ?? s.extraMcp,
        runs: Object.fromEntries(ev.runs.map((r) => [r.conversation_id, r])),
        approvals: ev.runs.flatMap((r) => r.approvals),
      };
    case "blender.state":
      return { ...s, blender: ev.state };
    case "unreal.state":
      return { ...s, unreal: ev.state };
    case "extra_mcp.state":
      return { ...s, extraMcp: ev.state };
    case "blender.tool_result":
      return { ...s, mcpResults: [{ ...ev.tool_call, conversation_id: ev.conversation_id }, ...s.mcpResults].slice(0, 30) };
    case "conversation.updated": {
      const c = ev.conversation;
      const detail = s.detail && s.detail.conversation.id === c.id ? { ...s.detail, conversation: c } : s.detail;
      if (c.project_id !== s.activeProjectId) {
        return { ...s, detail, conversations: s.conversations.filter((x) => x.id !== c.id) };
      }
      const list = upsert(s.conversations, c, true).sort((a, b) => b.updated_at - a.updated_at);
      return { ...s, detail, conversations: list };
    }
    case "conversation.deleted":
      return {
        ...s,
        conversations: s.conversations.filter((c) => c.id !== ev.conversation_id),
        ...(s.currentId === ev.conversation_id ? { currentId: null, detail: null } : {}),
      };
    case "conversation.rewound":
      if (s.detail?.conversation.id !== ev.conversation_id) return s;
      return { ...s, detail: { ...s.detail, messages: s.detail.messages.filter((m) => m.seq < ev.from_seq), sessions: [] } };
    case "message.created":
    case "message.updated": {
      const m: Message = ev.message;
      if (s.detail?.conversation.id !== m.conversation_id) return s;
      const messages = upsert(s.detail.messages, m).sort((a, b) => a.seq - b.seq);
      return { ...s, detail: { ...s.detail, messages } };
    }
    case "run.started":
      return { ...s, runs: { ...s.runs, [ev.run.conversation_id]: ev.run } };
    case "run.delta": {
      const r = s.runs[ev.conversation_id];
      if (!r) return s;
      return { ...s, runs: { ...s.runs, [ev.conversation_id]: { ...r, text: r.text + ev.text, status_text: tr("応答中…") } } };
    }
    case "run.tool": {
      const r = s.runs[ev.conversation_id];
      if (!r) return s;
      return {
        ...s,
        runs: { ...s.runs, [ev.conversation_id]: { ...r, tool_calls: upsert(r.tool_calls, ev.tool_call), status_text: ev.status_text } },
      };
    }
    case "run.status": {
      const r = s.runs[ev.conversation_id];
      if (!r) return s;
      return { ...s, runs: { ...s.runs, [ev.conversation_id]: { ...r, status_text: ev.status_text } } };
    }
    case "run.progress": {
      const r = s.runs[ev.conversation_id];
      if (!r) return s;
      return { ...s, runs: { ...s.runs, [ev.conversation_id]: { ...r, progress: ev.progress } } };
    }
    case "run.notice": {
      const r = s.runs[ev.conversation_id];
      if (!r) return s;
      return { ...s, runs: { ...s.runs, [ev.conversation_id]: { ...r, notices: [...r.notices, ev.text] } } };
    }
    case "run.finished": {
      const runs = { ...s.runs };
      delete runs[ev.conversation_id];
      return { ...s, runs, approvals: s.approvals.filter((a) => a.conversation_id !== ev.conversation_id) };
    }
    case "approval.request":
      return { ...s, approvals: upsert(s.approvals, ev.approval) };
    case "approval.resolved":
      return { ...s, approvals: s.approvals.filter((a) => a.id !== ev.id) };
    default:
      return s;
  }
}

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "settings":
      return { ...s, settings: a.settings };
    case "projects":
      return { ...s, projects: a.projects, activeProjectId: a.activeProjectId ?? s.activeProjectId };
    case "conversations":
      return { ...s, conversations: a.conversations };
    case "open":
      return {
        ...s,
        currentId: a.id,
        detail: a.detail,
        draftMode: a.detail?.conversation.mode ?? "modeling",
        composerSeed: a.detail?.conversation.draft?.text ? {
          text: a.detail.conversation.draft.text,
          attachments: (a.detail.conversation.draft.attachment_ids ?? []).map((id) => a.detail!.attachments[id]).filter(Boolean),
        } : null,
        runs: a.detail?.run ? { ...s.runs, [a.detail.run.conversation_id]: a.detail.run } : s.runs,
        ...(a.detail
          ? {
              draftProvider: a.detail.conversation.provider,
              draftModel: a.detail.conversation.model,
              draftEffort: a.detail.conversation.effort ?? "",
            }
          : {}),
      };
    case "providers":
      return { ...s, providers: a.providers ?? s.providers, providersLoading: a.loading };
    case "draft":
      return {
        ...s,
        draftProvider: a.provider ?? s.draftProvider,
        draftModel: a.model ?? s.draftModel,
        draftEffort: a.effort ?? s.draftEffort,
        draftMode: a.mode ?? s.draftMode,
      };
    case "composerSeed":
      return { ...s, composerSeed: a.seed };
    case "ws":
      return { ...s, wsConnected: a.connected };
    case "toast":
      return { ...s, toasts: [...s.toasts, a.toast].slice(-4) };
    case "untoast":
      return { ...s, toasts: s.toasts.filter((t) => t.id !== a.id) };
    case "event":
      return onEvent(s, a.ev);
    case "removeMessagesFrom":
      if (!s.detail) return s;
      return { ...s, detail: { ...s.detail, messages: s.detail.messages.filter((m) => m.seq < a.seq) } };
  }
}

function initial(boot: Bootstrap): State {
  const dp = boot.settings.default_provider || "claude";
  return {
    boot,
    settings: boot.settings,
    projects: boot.projects,
    activeProjectId: boot.active_project_id,
    conversations: [],
    currentId: null,
    detail: null,
    runs: Object.fromEntries(boot.runs.map((r) => [r.conversation_id, r])),
    approvals: boot.runs.flatMap((r) => r.approvals),
    blender: boot.blender,
    unreal: boot.unreal ?? { status: "disabled", url: "", error: "", toolsets: [], editor: null, preview: null, updated_at: 0 },
    extraMcp: boot.extra_mcp ?? {},
    providers: [],
    providersLoading: false,
    mcpResults: [],
    draftProvider: dp,
    draftModel: boot.settings.providers[dp]?.default_model ?? "",
    draftEffort: boot.settings.providers[dp]?.default_effort ?? "",
    draftMode: "modeling",
    composerSeed: null,
    wsConnected: true, // the banner is for losing an established connection
    toasts: [],
  };
}

let toastSeq = 1;

function useActions(state: State, dispatch: React.Dispatch<Action>) {
  const stateRef = useRef(state);
  stateRef.current = state;

  const toast = useCallback(
    (text: string, kind: Toast["kind"] = "info", action?: Toast["action"]) => {
      const id = toastSeq++;
      dispatch({ type: "toast", toast: { id, kind, text, action } });
      setTimeout(() => dispatch({ type: "untoast", id }), kind === "error" ? 9000 : 5000);
    },
    [dispatch],
  );

  const guard = useCallback(
    async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
      try {
        return await fn();
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e), "error");
        return undefined;
      }
    },
    [toast],
  );

  const loadConversations = useCallback(
    async (projectId?: string) => {
      const pid = projectId ?? stateRef.current.activeProjectId;
      const list = await get<Conversation[]>(`/api/conversations?project_id=${pid}`);
      dispatch({ type: "conversations", conversations: list });
    },
    [dispatch],
  );

  const openConversation = useCallback(
    async (id: string | null, mode: Conversation["mode"] = "modeling") => {
      if (!id) {
        const s = stateRef.current;
        dispatch({ type: "open", id: null, detail: null });
        const provider = mode === "design_prompt" && s.settings.providers.codex?.enabled !== false ? "codex" : s.draftProvider;
        const conf = s.settings.providers[provider];
        dispatch({ type: "draft", provider, model: provider === s.draftProvider ? s.draftModel : conf?.default_model ?? "",
          effort: provider === s.draftProvider ? s.draftEffort : conf?.default_effort ?? "", mode });
        return;
      }
      const detail = await guard(() => get<ConversationDetail>(`/api/conversations/${id}`));
      if (detail) dispatch({ type: "open", id, detail });
    },
    [dispatch, guard],
  );

  const refreshProviders = useCallback(
    async (refresh = false) => {
      dispatch({ type: "providers", providers: null, loading: true });
      const list = await guard(() => get<ProviderStatus[]>(`/api/providers${refresh ? "?refresh=1" : ""}`));
      dispatch({ type: "providers", providers: list ?? null, loading: false });
    },
    [dispatch, guard],
  );

  const refreshProjects = useCallback(async () => {
    const projects = await get<Project[]>("/api/projects");
    dispatch({ type: "projects", projects });
  }, [dispatch]);

  const activateProject = useCallback(
    async (pid: string) => {
      await guard(() => post(`/api/projects/${pid}/activate`));
      dispatch({ type: "projects", projects: stateRef.current.projects, activeProjectId: pid });
      dispatch({ type: "open", id: null, detail: null });
      await loadConversations(pid);
    },
    [dispatch, guard, loadConversations],
  );

  const setDraft = useCallback(
    async (provider: ProviderId, model: string, effort: string) => {
      dispatch({ type: "draft", provider, model, effort });
      const cur = stateRef.current.detail?.conversation;
      if (cur && (cur.provider !== provider || cur.model !== model || (cur.effort ?? "") !== effort)) {
        await guard(() => patch(`/api/conversations/${cur.id}`, { provider, model, effort }));
      }
    },
    [dispatch, guard],
  );

  const send = useCallback(
    async (text: string, attachmentIds: string[]) => {
      const s = stateRef.current;
      let id = s.currentId;
      if (!id) {
        const conv = await guard(() =>
          post<Conversation>("/api/conversations", {
            project_id: s.activeProjectId,
            provider: s.draftProvider,
            model: s.draftModel,
            effort: s.draftEffort,
            mode: s.draftMode,
          }),
        );
        if (!conv) return false;
        id = conv.id;
        dispatch({ type: "event", ev: { type: "conversation.updated", conversation: conv } });
        dispatch({ type: "open", id, detail: { conversation: conv, messages: [], attachments: {}, sessions: [], run: null } });
      }
      const res = await guard(() =>
        post(`/api/conversations/${id}/messages`, {
          text,
          attachment_ids: attachmentIds,
          provider: s.draftProvider,
          model: s.draftModel,
          effort: s.draftEffort,
        }),
      );
      if (res && attachmentIds.length) await openConversation(id);
      return Boolean(res);
    },
    [dispatch, guard, openConversation],
  );

  const editMessage = useCallback(
    async (m: Message, text: string) => {
      const s = stateRef.current;
      const res = await guard(() =>
        post(`/api/conversations/${m.conversation_id}/messages/${m.id}/edit`, {
          text,
          provider: s.draftProvider,
          model: s.draftModel,
          effort: s.draftEffort,
        }),
      );
      if (res) await openConversation(m.conversation_id);
      return Boolean(res);
    },
    [guard, openConversation],
  );

  const retry = useCallback(
    async (conversationId: string) => {
      const s = stateRef.current;
      const res = await guard(() => post(`/api/conversations/${conversationId}/retry`, { provider: s.draftProvider, model: s.draftModel, effort: s.draftEffort }));
      if (res) await openConversation(conversationId);
    },
    [guard, openConversation],
  );

  const cancel = useCallback((conversationId: string) => guard(() => post(`/api/conversations/${conversationId}/cancel`)), [guard]);

  const deleteConversation = useCallback(
    async (id: string) => {
      await guard(() => del(`/api/conversations/${id}`));
    },
    [guard],
  );

  const renameConversation = useCallback(
    (id: string, title: string) => guard(() => patch(`/api/conversations/${id}`, { title })),
    [guard],
  );

  const saveSettings = useCallback(
    async (p: Partial<Settings> | Record<string, unknown>) => {
      const s = await guard(() => patch<Settings>("/api/settings", p));
      if (s) dispatch({ type: "settings", settings: s });
      return s;
    },
    [dispatch, guard],
  );

  const exportConversation = useCallback(
    async (id: string, format: "md" | "json") => {
      const res = await guard(() => post<{ path: string }>(`/api/conversations/${id}/export`, { format }));
      if (res)
        toast(tr`エクスポートしました: ${res.path}`, "success", {
          label: tr("フォルダを開く"),
          run: () => void guard(() => post("/api/system/reveal", { path: res.path })),
        });
    },
    [guard, toast],
  );

  const usePrompt = useCallback(async (text: string) => {
    const source = stateRef.current.currentId;
    if (!source) return;
    const result = await guard(() => post<{ conversation: Conversation; attachments: Attachment[] }>(`/api/conversations/${source}/handoff`, { text }));
    if (!result) return;
    dispatch({ type: "event", ev: { type: "conversation.updated", conversation: result.conversation } });
    dispatch({ type: "open", id: result.conversation.id, detail: {
      conversation: result.conversation, messages: [], attachments: Object.fromEntries(result.attachments.map((a) => [a.id, a])), sessions: [], run: null,
    } });
    dispatch({ type: "composerSeed", seed: { text, attachments: result.attachments } });
  }, [guard, dispatch]);

  const saveBrief = useCallback(async (brief: ModelingBrief) => {
    const id = stateRef.current.currentId;
    if (!id) return false;
    const conv = await guard(() => patch<Conversation>(`/api/conversations/${id}`, { brief }));
    if (conv) dispatch({ type: "event", ev: { type: "conversation.updated", conversation: conv } });
    return Boolean(conv);
  }, [guard, dispatch]);

  return {
    toast,
    guard,
    loadConversations,
    openConversation,
    usePrompt,
    saveBrief,
    refreshProviders,
    refreshProjects,
    activateProject,
    setDraft,
    send,
    editMessage,
    retry,
    cancel,
    deleteConversation,
    renameConversation,
    saveSettings,
    exportConversation,
  };
}

type Ctx = { state: State; dispatch: React.Dispatch<Action>; actions: ReturnType<typeof useActions> };
const StoreContext = createContext<Ctx | null>(null);

export function StoreProvider({ boot, children }: { boot: Bootstrap; children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, boot, initial);
  useLayoutEffect(() => setLanguage(state.settings.ui.language ?? "ja"), [state.settings.ui.language]);
  const actions = useActions(state, dispatch);
  const currentRef = useRef<string | null>(null);
  currentRef.current = state.currentId;

  useEffect(() => {
    const socket = new EventSocket();
    const off1 = socket.on((ev) => {
      dispatch({ type: "event", ev });
      if (ev.type === "run.finished" && ev.status === "error" && ev.conversation_id !== currentRef.current) {
        actions.toast(tr`別の会話でエラーが発生しました: ${ev.error}`, "error");
      }
    });
    let wasConnected = false;
    const off2 = socket.onConnection((connected) => {
      dispatch({ type: "ws", connected });
      // Resync anything missed while disconnected.
      if (connected && wasConnected) {
        void actions.loadConversations();
        if (currentRef.current) void actions.openConversation(currentRef.current);
      }
      if (connected) wasConnected = true;
    });
    socket.connect();
    void actions.loadConversations();
    void actions.refreshProviders();
    return () => {
      off1();
      off2();
      socket.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <StoreContext.Provider value={{ state, dispatch, actions }}>{children}</StoreContext.Provider>;
}

export function useStore(): Ctx {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("StoreProvider missing");
  return ctx;
}

export function providerName(state: State, id: ProviderId): string {
  return tr(state.boot.providers.find((p) => p.id === id)?.name ?? id);
}
