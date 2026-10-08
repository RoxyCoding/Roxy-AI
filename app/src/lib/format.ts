export function relativeTime(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "たった今";
  if (diff < 3600) return `${Math.floor(diff / 60)}分前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}時間前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}日前`;
  return new Date(ts * 1000).toLocaleDateString("ja-JP");
}

export function clockTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" });
}

export function fileSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function pretty(v: unknown): string {
  if (v === undefined || v === null) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

export function seconds(from: number, to?: number | null): string {
  const s = Math.max(0, (to ?? Date.now() / 1000) - from);
  return s < 60 ? `${s.toFixed(s < 10 ? 1 : 0)}秒` : `${Math.floor(s / 60)}分${Math.floor(s % 60)}秒`;
}

export function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}

const EFFORT_LABELS: Record<string, string> = {
  none: "なし",
  minimal: "最小",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "超高",
  max: "最大",
  ultra: "ウルトラ",
};

/** Display name for a thinking/reasoning level reported by the AI CLI. */
export function effortLabel(effort: string): string {
  return EFFORT_LABELS[effort] ?? effort;
}

/** The model entry for `model`; "" or "default" means the provider's default model. */
export function findModel<T extends { id: string; is_default: boolean }>(models: T[], model: string): T | undefined {
  return models.find((m) => m.id === model) ?? (!model || model === "default" ? models.find((m) => m.is_default) : undefined);
}
export function visibleAssistantText(text: string): string {
  const cleaned = text.replace(/^```roxy-(?:brief|progress)[ \t]*\r?\n.*?(?:^```[ \t]*(?:\r?\n|$)|$(?![\s\S]))/gms, "");
  return cleaned === text ? text : cleaned.trim();
}
