import { useState } from "react";
import { post } from "../lib/backend";
import { useStore } from "../lib/store";
import { Icon } from "./Icons";

export function McpRestartButton() {
  const { state, actions } = useStore();
  const [busy, setBusy] = useState(false);
  const running = Object.keys(state.runs).length > 0;
  return (
    <button className="btn small" disabled={busy || running}
            title={running ? "AIの応答生成が終わってから再起動してください" : "ウィンドウを閉じずにMCPの更新を反映します"}
            onClick={async () => {
              setBusy(true);
              try {
                const result = await actions.guard(() => post("/api/blender/restart"));
                if (result) actions.toast("MCPの再起動を開始しました。AI側は次の送信で接続し直します。", "success");
              } finally {
                setBusy(false);
              }
            }}>
      {busy ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
      {busy ? "再起動中…" : "MCPを再起動"}
    </button>
  );
}
