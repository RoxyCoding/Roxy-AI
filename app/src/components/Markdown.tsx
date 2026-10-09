import { t as tr } from "../lib/i18n";
import { memo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Icon } from "./Icons";
import { useStore } from "../lib/store";

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const { state, actions } = useStore();
  const [copied, setCopied] = useState(false);
  const [handingOff, setHandingOff] = useState(false);
  const prompt = state.draftMode === "prompt" && ["", "text", "markdown"].includes(lang);
  const designPrompt = state.draftMode === "design_prompt" && ["", "text", "markdown"].includes(lang);
  const running = state.currentId ? Boolean(state.runs[state.currentId]) : false;
  return (
    <div className="codeblock">
      <div className="codeblock-head">
        <span>{designPrompt ? tr("設定画生成プロンプト") : prompt ? tr("モデリングプロンプト") : lang || "code"}</span>
        {prompt && (
          <button className="link-btn" disabled={running || handingOff} onClick={async () => {
            setHandingOff(true);
            try { await actions.usePrompt(code); } finally { setHandingOff(false); }
          }}>
            {handingOff ? <span className="spinner" /> : <Icon name="send" size={13} />} {tr("制作チャットへ渡す")}</button>
        )}
        <button
          className="link-btn"
          onClick={() => {
            void navigator.clipboard.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          <Icon name={copied ? "check" : "copy"} size={13} /> {copied ? tr("コピーしました") : tr("コピー")}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

const components: Components = {
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children }) => {
    const text = String(children ?? "");
    const lang = /language-(\S+)/.exec(className || "")?.[1] ?? "";
    if (!className && !text.includes("\n")) return <code className="inline-code">{children}</code>;
    return <CodeBlock lang={lang} code={text.replace(/\n$/, "")} />;
  },
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="table-wrap">
      <table>{children}</table>
    </div>
  ),
};

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
