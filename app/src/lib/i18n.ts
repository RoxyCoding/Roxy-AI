import { useSyncExternalStore } from "react";
import { ENGLISH } from "./en";

export type Language = "ja" | "en";
const listeners = new Set<() => void>();
const templates = Object.entries(ENGLISH).filter(([key]) => key.includes("${")).map(([key, translated]) => ({
  pattern: new RegExp("^" + key.split(/(\$\{\d+\})/).map((part) =>
    /^\$\{\d+\}$/.test(part) ? "(.*?)" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("") + "$"),
  translated,
}));
let language: Language = "ja";
try {
  language = localStorage.getItem("roxy-language") === "en" ? "en" : "ja";
} catch { /* The backend settings remain authoritative when storage is unavailable. */ }

export function setLanguage(next: Language): void {
  const selected = next === "en" ? "en" : "ja";
  if (typeof document !== "undefined") document.documentElement.lang = selected;
  try { localStorage.setItem("roxy-language", selected); } catch { /* Storage is optional. */ }
  if (selected === language) return;
  language = selected;
  for (const listener of listeners) listener();
}

export function useLanguage(): Language {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }, () => language);
}

export function locale(): string {
  return language === "en" ? "en-US" : "ja-JP";
}

export function t(text: string | undefined): string;
export function t(strings: TemplateStringsArray, ...values: unknown[]): string;
export function t(input: string | undefined | TemplateStringsArray, ...values: unknown[]): string {
  if (input === undefined) return "";
  const text = typeof input === "string" ? input : input.reduce((out, part, i) =>
    out + (i ? `\${${i - 1}}` : "") + part, "");
  const key = text.replace(/\s+/g, " ").trim();
  let translated = language === "en" ? ENGLISH[key] ?? text : text;
  if (language === "en" && translated === text && typeof input === "string") {
    for (const template of templates) {
      const match = template.pattern.exec(key);
      if (match) {
        translated = template.translated.replace(/\$\{(\d+)\}/g, (_, index: string) => match[Number(index) + 1]);
        break;
      }
    }
  }
  return typeof input === "string" ? translated : translated.replace(/\$\{(\d+)\}/g,
    (_, index: string) => String(values[Number(index)] ?? ""));
}
