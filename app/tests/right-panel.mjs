import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: "custom" });
try {
  const { RightPanel } = await server.ssrLoadModule("/src/components/RightPanel.tsx");
  const { StoreProvider } = await server.ssrLoadModule("/src/lib/store.tsx");
  const { LightboxProvider } = await server.ssrLoadModule("/src/components/Lightbox.tsx");
  function render(scene) {
    const boot = {
      settings: { default_provider: "claude", providers: {} },
      projects: [], active_project_id: "", runs: [], providers: [],
      blender: { mcp: "running", blender: "connected", endpoint: "localhost:9876",
        server: {}, error: "", addon: null, tools: [], scene, preview: null },
    };
    return renderToStaticMarkup(createElement(StoreProvider, { boot },
      createElement(LightboxProvider, null,
        createElement(RightPanel, { onClose() {}, onSettings() {} }))));
  }
  const html = render({
    text: "unparsed output", scene: "Scene", active: "  Cube | marker", selected: ["  Cube | marker"],
    selected_count: 3, mode: "OBJECT", object_counts: { mesh: 2 },
    columns: ["name", "type", "location", "size"],
    objects: [{ name: "  Cube | marker", type: "MESH", fields: { size: "size 2x2x2" } }],
    settings: { file: "(unsaved)", engine: "CYCLES", frames: [1, 250, 1], fps: 24,
      resolution: [1920, 1080], camera: null, world_hdri: null, unit_scale: 1 },
    file: null, is_dirty: true, at: 1,
  });
  assert.ok(html.includes('class="row-line">  Cube | marker | MESH | size 2x2x2</div>'));
  assert.ok(html.includes("2 mesh"));
  assert.ok(html.includes("ほか2件"));
  assert.ok(html.includes("未保存の変更あり"));
  assert.ok(html.includes("1920x1080"));

  const legacy = render({ text: "legacy", scene: "Old", selected: ["Cube"], counts: "1 mesh",
    columns: ["name", "type"], rows: ["Cube | mesh"], file_line: "old.blend", at: 1 });
  assert.ok(legacy.includes('class="row-line">Cube | mesh</div>'));
  assert.ok(legacy.includes("old.blend"));
  assert.ok(render({ text: "empty", selected: [], object_counts: {}, objects: [], at: 1 }).includes("empty"));
  console.log("RightPanel: structured names, selection count, file status and legacy output passed");
} finally {
  await server.close();
}
