const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const ts = require("typescript");

function translator() {
  const storage = new Map();
  const document = { documentElement: { lang: "ja" } };
  const modules = {};
  function load(name) {
    if (modules[name]) return modules[name];
    const file = path.join(__dirname, "../src/lib", `${name}.ts`);
    const code = ts.transpileModule(fs.readFileSync(file, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const exports = {};
    vm.runInNewContext(code, {
      exports, document,
      localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) },
      require: (id) => id === "react" ? { useSyncExternalStore: (_, snapshot) => snapshot() } : load(id.slice(2)),
    });
    modules[name] = exports;
    return exports;
  }
  return { ...load("i18n"), dictionary: load("en").ENGLISH, storage, document };
}

test("switches labels, interpolation, document language and remembered preference", () => {
  const { t, setLanguage, locale, storage, document } = translator();
  assert.equal(t("設定"), "設定");
  setLanguage("en");
  assert.equal(t("設定"), "Settings");
  assert.equal(t("設定画生成プロンプト"), "Design sheet prompt");
  assert.equal(t("設定画生成"), "Design sheet generation");
  assert.equal(t("設定画"), "Design sheet");
  assert.equal(t`添付は1回につき${10}件までです`, "You can attach up to 10 files per message");
  assert.equal(t("ツール実行中: blender.look"), "Running tool: blender.look");
  assert.equal(t`「${"日本語の名前 ${9}"}」を UE5 に送る`, "Send “日本語の名前 ${9}” to UE5");
  assert.equal(t("user-written text"), "user-written text");
  assert.equal(document.documentElement.lang, "en");
  assert.equal(storage.get("roxy-language"), "en");
  assert.equal(locale(), "en-US");
  setLanguage("ja");
  assert.equal(t`添付は1回につき${10}件までです`, "添付は1回につき10件までです");
  assert.equal(locale(), "ja-JP");
});

test("English templates retain every interpolation placeholder", () => {
  const { dictionary } = translator();
  for (const [japanese, english] of Object.entries(dictionary)) {
    assert.deepEqual((english.match(/\$\{\d+\}/g) ?? []).sort(),
      (japanese.match(/\$\{\d+\}/g) ?? []).sort(), japanese);
  }
});
