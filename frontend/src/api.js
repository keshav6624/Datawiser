const BASE = "/api";

async function http(path, opts = {}) {
  const res = await fetch(BASE + path, {
    headers: { "x-org-id": "org_default" },
    ...opts,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      msg = j.detail || JSON.stringify(j).slice(0, 200);
    } catch (_) {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

export async function getSources() {
  return http("/sources");
}

export async function deleteSource(id) {
  return http(`/sources/${id}`, { method: "DELETE" });
}

export async function uploadFiles(name, files) {
  const fd = new FormData();
  fd.append("name", name);
  for (const f of files) fd.append("files", f);
  return http("/upload", { method: "POST", body: fd, headers: { "x-org-id": "org_default" } });
}

export async function connectDb(payload) {
  return http("/connect-db", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-org-id": "org_default" },
    body: JSON.stringify(payload),
  });
}

export async function getSchema(sourceId) {
  const data = await http(`/schema?source_id=${encodeURIComponent(sourceId)}`);
  return data;
}

export async function chat(sourceId, question, conversationId) {
  return http("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-org-id": "org_default" },
    body: JSON.stringify({ source_id: sourceId, question, conversation_id: conversationId || null }),
  });
}

export async function getHistory() {
  return http("/history");
}

export async function getDashboards() {
  return http("/dashboards");
}

export async function saveDashboard(title, layout, dashboardId) {
  return http("/save-dashboard", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-org-id": "org_default" },
    body: JSON.stringify({ title, layout, dashboard_id: dashboardId || null }),
  });
}
