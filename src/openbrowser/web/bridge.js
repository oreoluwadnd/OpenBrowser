// Talks to the chat host when this page runs inside Claude as an MCP App (JSON-RPC over postMessage).
const bridge = (() => {
  const inApp = window.parent !== window;
  let nextId = 1;
  const pending = {};
  const handlers = {};
  window.addEventListener("message", (event) => {
    const m = event.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.id !== undefined && pending[m.id] && !m.method) {
      m.error ? pending[m.id].reject(new Error(m.error.message)) : pending[m.id].resolve(m.result);
      delete pending[m.id];
    } else if (m.method === "ui/resource-teardown") {
      window.parent.postMessage({ jsonrpc: "2.0", id: m.id, result: {} }, "*");
    } else if (m.method && handlers[m.method]) {
      handlers[m.method](m.params);
    }
  });
  function request(method, params) {
    const id = nextId++;
    window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    return new Promise((resolve, reject) => (pending[id] = { resolve, reject }));
  }
  function notify(method, params) {
    window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
  }
  async function start() {
    await request("ui/initialize", {
      protocolVersion: "2026-01-26",
      appInfo: { name: "OpenBrowser", version: "0.1.0" },
      appCapabilities: {},
    });
    notify("ui/notifications/initialized", {});
    new ResizeObserver(() =>
      notify("ui/notifications/size-changed", { width: document.body.scrollWidth, height: document.body.scrollHeight })
    ).observe(document.body);
  }
  function on(method, handler) { handlers[method] = handler; }
  function callTool(name, args) { return request("tools/call", { name, arguments: args }); }
  function text(result) { return (result.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n"); }
  return { inApp, start, on, callTool, text };
})();
