import { useEffect, useState } from "react";
import { post } from "../lib/backend";
import { useStore } from "../lib/store";
import type { Approval } from "../lib/types";
import { pretty } from "../lib/format";
import { Icon } from "./Icons";

const KIND_LABEL: Record<Approval["kind"], string> = {
  tool: "ツールの実行",
  command: "コマンドの実行",
  file_change: "ファイルの変更",
  permissions: "追加の権限",
  mcp_elicitation: "MCPからの確認",
  question: "AIからの質問",
};

function Questions({ a, onDone }: { a: Approval; onDone: (answers: Record<string, string[]> | null) => void }) {
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [other, setOther] = useState<Record<string, string>>({});
  const toggle = (qid: string, label: string, multi: boolean) =>
    setAnswers((prev) => {
      const cur = prev[qid] ?? [];
      if (!multi) return { ...prev, [qid]: [label] };
      return { ...prev, [qid]: cur.includes(label) ? cur.filter((l) => l !== label) : [...cur, label] };
    });
  const final = () =>
    Object.fromEntries(
      a.questions.map((q) => {
        const picked = (answers[q.id] ?? []).filter((l) => l !== "__other__");
        if ((answers[q.id] ?? []).includes("__other__") && other[q.id]?.trim()) picked.push(other[q.id].trim());
        return [q.id, picked];
      }),
    );
  return (
    <>
      {a.questions.map((q) => (
        <fieldset key={q.id} className="question">
          {q.header && <legend className="chip">{q.header}</legend>}
          <p className="strong">{q.question}</p>
          {q.options.map((o) => (
            <label key={o.label} className="option">
              <input
                type={q.multi ? "checkbox" : "radio"}
                name={q.id}
                checked={(answers[q.id] ?? []).includes(o.label)}
                onChange={() => toggle(q.id, o.label, q.multi)}
              />
              <span>
                {o.label}
                {o.description && <span className="muted small"> — {o.description}</span>}
              </span>
            </label>
          ))}
          <label className="option">
            <input type={q.multi ? "checkbox" : "radio"} name={q.id} checked={(answers[q.id] ?? []).includes("__other__")}
                   onChange={() => toggle(q.id, "__other__", q.multi)} />
            <input className="input" placeholder="その他（自由入力）" value={other[q.id] ?? ""}
                   onFocus={() => !(answers[q.id] ?? []).includes("__other__") && toggle(q.id, "__other__", q.multi)}
                   onChange={(e) => setOther((p) => ({ ...p, [q.id]: e.target.value }))} />
          </label>
        </fieldset>
      ))}
      <div className="dialog-actions">
        <button className="btn" onClick={() => onDone(null)}>回答しない</button>
        <button className="btn primary" onClick={() => onDone(final())}>回答を送信</button>
      </div>
    </>
  );
}

function ElicitationForm({ a, onSubmit }: { a: Approval; onSubmit: (content: Record<string, unknown>) => void }) {
  const props = a.schema?.properties ?? {};
  const [values, setValues] = useState<Record<string, unknown>>(
    Object.fromEntries(Object.entries(props).map(([k, p]) => [k, p.default ?? (p.type === "boolean" ? false : "")])),
  );
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        const out: Record<string, unknown> = {};
        for (const [k, p] of Object.entries(props)) {
          const v = values[k];
          out[k] = p.type === "number" || p.type === "integer" ? Number(v) : v;
        }
        onSubmit(out);
      }}
    >
      {Object.entries(props).map(([k, p]) => (
        <label key={k} className="field">
          <span>{p.title || k}{a.schema?.required?.includes(k) ? " *" : ""}</span>
          {p.enum ? (
            <select className="select" value={String(values[k] ?? "")} onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))}>
              {p.enum.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : p.type === "boolean" ? (
            <input type="checkbox" checked={Boolean(values[k])} onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.checked }))} />
          ) : (
            <input className="input" type={p.type === "number" || p.type === "integer" ? "number" : "text"}
                   value={String(values[k] ?? "")} required={a.schema?.required?.includes(k)}
                   onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))} />
          )}
          {p.description && <span className="muted small">{p.description}</span>}
        </label>
      ))}
      <button id="elicit-submit" type="submit" hidden />
    </form>
  );
}

export function ApprovalDialog() {
  const { state, actions } = useStore();
  const a = state.approvals.find((x) => x.conversation_id === state.currentId) ?? state.approvals[0];
  const [sending, setSending] = useState(false);
  useEffect(() => setSending(false), [a?.id]);
  if (!a) return null;
  const conv = state.conversations.find((c) => c.id === a.conversation_id);
  const hasForm = a.kind === "mcp_elicitation" && a.schema?.properties && Object.keys(a.schema.properties).length > 0;

  const respond = async (body: { allow: boolean; remember?: boolean; cancel_turn?: boolean; answers?: Record<string, string[]>; content?: Record<string, unknown>; message?: string }) => {
    setSending(true);
    await actions.guard(() => post(`/api/approvals/${a.id}`, body));
  };

  const input = pretty(a.input);
  return (
    <div className="modal-scrim">
      <div className="dialog approval" role="dialog" aria-modal="true">
        <div className="dialog-head">
          <span className="chip">{KIND_LABEL[a.kind]}</span>
          {a.conversation_id !== state.currentId && conv && <span className="muted small">会話「{conv.title || "新しいチャット"}」</span>}
          {state.approvals.length > 1 && <span className="muted small">ほか {state.approvals.length - 1} 件</span>}
        </div>
        <h2 className="dialog-title">
          {a.kind === "question" ? <Icon name="chevron" /> : <Icon name="tool" />} {a.title}
        </h2>
        {a.server && a.kind !== "question" && (
          <p className="muted small">
            MCPサーバー: <code>{a.server}</code>
            {a.read_only && "（読み取り専用ツール）"}
          </p>
        )}
        {a.detail && <p className="detail">{a.detail}</p>}
        {a.kind === "question" ? (
          <Questions a={a} onDone={(answers) => void respond(answers ? { allow: true, answers } : { allow: false })} />
        ) : (
          <>
            {hasForm ? (
              <ElicitationForm a={a} onSubmit={(content) => void respond({ allow: true, content })} />
            ) : (
              input && input !== "{}" && input !== "null" && <pre className="tool-pre tall">{input}</pre>
            )}
            <div className="dialog-actions">
              <button className="btn ghost danger" disabled={sending} onClick={() => void respond({ allow: false, cancel_turn: true })} title="拒否して応答生成も中断します">
                中断
              </button>
              <span className="grow" />
              <button className="btn" disabled={sending} onClick={() => void respond({ allow: false, message: "ユーザーが拒否しました" })}>
                拒否
              </button>
              {a.allow_session && !hasForm && (
                <button className="btn" disabled={sending} onClick={() => void respond({ allow: true, remember: true })} title="この会話の間、同じ操作は確認せずに許可します">
                  この会話では常に許可
                </button>
              )}
              <button
                className="btn primary"
                disabled={sending}
                onClick={() => (hasForm ? document.getElementById("elicit-submit")?.click() : void respond({ allow: true }))}
              >
                {hasForm ? "送信" : "許可"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
