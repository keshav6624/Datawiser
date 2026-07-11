import React, { useEffect, useRef, useState } from "react";
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import {
  getSources, deleteSource, uploadFiles, connectDb, getSchema, chat, getHistory, getDashboards, saveDashboard,
} from "./api.js";

const EXAMPLE_QUESTIONS = [
  "Show revenue by region",
  "Top 10 customers by revenue",
  "Monthly revenue trend",
  "Revenue last 30 days",
  "Compare revenue this month vs last month",
  "Total count of orders by month",
];

export default function App() {
  const [sources, setSources] = useState([]);
  const [activeSource, setActiveSource] = useState(null);
  const [schema, setSchema] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const [tab, setTab] = useState("chat");
  const [dashboards, setDashboards] = useState([]);
  const [schemaError, setSchemaError] = useState("");
  const [themeMode, setThemeMode] = useState(() => localStorage.getItem("datawhisper-theme") || "system");
  const [view, setView] = useState("main"); // main | login | connect
  const scrollerRef = useRef(null);

  useEffect(() => { refreshSources(); refreshDashboards(); }, []);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      const resolved = themeMode === "system" ? (media.matches ? "dark" : "light") : themeMode;
      document.documentElement.dataset.theme = resolved;
      document.documentElement.style.colorScheme = resolved;
      localStorage.setItem("datawhisper-theme", themeMode);
    };
    applyTheme();
    if (themeMode !== "system") return undefined;
    media.addEventListener?.("change", applyTheme);
    return () => media.removeEventListener?.("change", applyTheme);
  }, [themeMode]);

  async function refreshSources() {
    try {
      const data = await getSources();
      const nextSources = data.sources || [];
      setSources(nextSources);
      if (!activeSource && nextSources.length) {
        for (const source of nextSources) {
          const opened = await selectSource(source);
          if (opened) return;
        }
        setActiveSource(nextSources[0]);
      }
      return nextSources;
    } catch (e) { console.error(e); }
    return [];
  }

  async function refreshDashboards() {
    try { const d = await getDashboards(); setDashboards(d.dashboards || []); } catch (e) { console.error(e); }
  }

  async function selectSource(s) {
    setActiveSource(s);
    setSchema(null);
    setSchemaError("");
    setMessages([]);
    setConversationId(null);
    if (!s) return false;
    try {
      const data = await getSchema(s.id);
      setSchema(data);
      return true;
    } catch (e) {
      console.error(e);
      setSchemaError(e?.message || "Failed to load schema");
      return false;
    }
  }

  async function handleUpload(files) {
    const name = files[0]?.name?.replace(/\.(csv|xlsx|xls)$/i, "") || "Upload";
    setLoading(true);
    try {
      const res = await uploadFiles(name, files);
      if (res.status !== "active") {
        alert("Upload failed: " + (res.error || "Unknown error"));
        return;
      }
      await refreshSources();
      setView("main");
    } catch (e) { alert("Upload failed: " + e.message); }
    setLoading(false);
  }
      const nextSources = await refreshSources();
      const newlyUploaded = nextSources.find((source) => source.id === res.source_id) || nextSources[0];
      if (newlyUploaded) {
        setActiveSource(newlyUploaded);
        setSchema(res.tables ? { source_id: res.source_id, tables: res.tables } : null);
        setSchemaError("");
        setMessages([]);
        setConversationId(null);
        if (!res.tables) await selectSource(newlyUploaded);
      }
      setTab("chat");
      setView("main");
    } catch (e) { alert("Upload failed: " + e.message); }
    finally { setLoading(false); }
  }

  async function handleConnect(payload) {
    setLoading(true);
    try {
      await connectDb(payload);
      await refreshSources();
      setView("main");
    } catch (e) { alert("Connect failed: " + e.message); }
    finally { setLoading(false); }
  }

  async function handleDeleteSource(sourceId) {
    const source = sources.find((item) => item.id === sourceId);
    if (!source) return;
    const confirmDelete = window.confirm(`Delete ${source.name}? This removes the uploaded source and its schema cache.`);
    if (!confirmDelete) return;
    try {
      await deleteSource(sourceId);
      const nextSources = await refreshSources();
      if (activeSource?.id === sourceId) {
        if (nextSources.length) {
          await selectSource(nextSources[0]);
        } else {
          setActiveSource(null);
          setSchema(null);
          setSchemaError("");
          setMessages([]);
          setConversationId(null);
        }
      }
    } catch (e) {
      alert("Delete failed: " + e.message);
    }
  }

  async function ask(question) {
    const q = (question ?? input).trim();
    if (!q || !activeSource || loading) return;
    setInput("");
    const userMsg = { role: "user", content: q };
    setMessages((m) => [...m, userMsg]);
    setLoading(true);
    const placeholder = { role: "assistant", loading: true, content: "" };
    setMessages((m) => [...m, placeholder]);
    try {
      const res = await chat(activeSource.id, q, conversationId);
      if (res.conversation_id) setConversationId(res.conversation_id);
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { role: "assistant", content: res.answer, question: q };
        return copy;
      });
    } catch (e) {
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { role: "assistant", content: { error: e.message } };
        return copy;
      });
    }
    setLoading(false);
  }

  async function saveCurrentToDashboard() {
    const insightMsg = [...messages].reverse().find((m) => m.role === "assistant" && m.content?.chart);
    if (!insightMsg) return alert("No chart to save yet.");
    const title = prompt("Dashboard title:", insightMsg.question || "New dashboard");
    if (!title) return;
    await saveDashboard(title, { chart: insightMsg.content.chart, insight: insightMsg.content.insight });
    await refreshDashboards();
    setTab("dashboards");
  }

  useEffect(() => {
    if (scrollerRef.current) scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
  }, [messages]);

  return (
    <div className="app-shell min-h-screen flex text-slate-900">
      <div className="app-backdrop" aria-hidden="true">
        <span className="backdrop-orb orb-one" />
        <span className="backdrop-orb orb-two" />
      </div>

      <aside className="sidebar-panel w-72 h-screen sticky top-0 flex flex-col">
        <div className="px-5 py-5 border-b border-white/60">
          <div className="flex items-center gap-3">
            <Logo />
            <div className="min-w-0 flex-1">
              <div className="font-semibold text-slate-900 leading-tight">DataWhisper</div>
              <div className="text-[11px] text-slate-500">Ask your data anything</div>
            </div>
            <label className="sr-only" htmlFor="theme-mode">Theme mode</label>
            <select
              id="theme-mode"
              value={themeMode}
              onChange={(e) => setThemeMode(e.target.value)}
              className="rounded-full border border-slate-200 bg-white/85 px-3 py-1.5 text-[11px] font-medium text-slate-600 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </div>
          <div className="mt-4 rounded-2xl border border-white/70 bg-white/75 px-4 py-3 shadow-card">
            <div className="text-[11px] uppercase tracking-[0.24em] text-slate-400">Workspace</div>
            <div className="mt-1 flex items-center justify-between text-sm">
              <span className="font-medium text-slate-800">Analytics-ready</span>
              <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">Live</span>
            </div>
          </div>
        </div>

        <div className="p-4 space-y-2">
          <button onClick={() => setView("upload")} className="action-button action-primary w-full text-left px-4 py-3 rounded-2xl text-sm font-medium text-white shadow-button">
            + Upload CSV
          </button>
          <button onClick={() => setView("connect")} className="action-button action-secondary w-full text-left px-4 py-3 rounded-2xl text-sm font-medium text-slate-800">
            + Connect Database
          </button>
        </div>

        <div className="px-4 pb-4 flex-1 min-h-0 overflow-y-auto scrollbar-thin">
          <div className="flex items-center justify-between px-1 mb-2">
            <div className="text-xs font-semibold text-slate-400 uppercase tracking-[0.22em]">Data Sources</div>
            <div className="text-[11px] text-slate-400">{sources.length}</div>
          </div>
          {sources.length === 0 && (
            <div className="rounded-2xl border border-dashed border-slate-200 bg-white/70 px-3 py-4 text-xs text-slate-400">
              No sources yet. Upload a CSV or connect a database to start exploring.
            </div>
          )}
          {sources.map((s) => (
            <div
              key={s.id}
              className={`source-chip relative mb-2 rounded-2xl text-sm transition ${activeSource?.id === s.id ? "is-active" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => selectSource(s)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") selectSource(s); }}
            >
              <div className="flex items-center justify-between gap-3 px-3 py-3 pr-12">
                <div className="min-w-0">
                  <div className="truncate font-medium text-slate-800">{s.name}</div>
                  <div className="text-[11px] text-slate-400 uppercase tracking-wider">{s.type}</div>
                </div>
                <div className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${activeSource?.id === s.id ? "bg-brand-50 text-brand-700" : "bg-slate-100 text-slate-500"}`}>{activeSource?.id === s.id ? "Active" : "Open"}</div>
              </div>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); handleDeleteSource(s.id); }}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                title="Delete source"
                aria-label={`Delete ${s.name}`}
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        <div className="border-t border-white/70 bg-white/65 p-3 flex gap-2 shrink-0 sticky bottom-0 backdrop-blur-xl">
          <TabButton active={tab === "chat"} onClick={() => setTab("chat")}>Chat</TabButton>
          <TabButton active={tab === "history"} onClick={() => setTab("history")}>History</TabButton>
          <TabButton active={tab === "dashboards"} onClick={() => setTab("dashboards")}>Boards</TabButton>
        </div>
      </aside>

      <main className="main-panel flex-1 h-screen min-h-0 flex flex-col min-w-0">
        {view === "upload" && <UploadModal onClose={() => setView("main")} onUpload={handleUpload} loading={loading} />}
        {view === "connect" && <ConnectModal onClose={() => setView("main")} onConnect={handleConnect} loading={loading} />}

        {view === "main" && tab === "chat" && (
          <>
            <header className="px-6 py-5 border-b border-white/70 bg-white/60 glass sticky top-0 z-10 backdrop-blur-xl">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="inline-flex items-center gap-2 rounded-full border border-white/70 bg-white/70 px-3 py-1 text-[11px] font-medium text-slate-500 shadow-sm">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" />
                    AI query workspace
                  </div>
                  <h1 className="mt-2 text-2xl font-semibold text-slate-900 tracking-tight">
                    {activeSource ? activeSource.name : "Welcome to DataWhisper"}
                  </h1>
                  <p className="mt-1 text-sm text-slate-500">
                    {activeSource ? `${schema?.tables?.length || 0} tables detected` : "Upload a CSV or connect a database to begin"}
                  </p>
                  {schemaError && (
                    <div className="mt-2 inline-flex items-center gap-2 rounded-full border border-rose-200 bg-rose-50 px-3 py-1 text-[11px] font-medium text-rose-700">
                      <span className="h-2 w-2 rounded-full bg-rose-500" />
                      Schema unavailable: {schemaError}
                    </div>
                  )}
                </div>
                <button onClick={saveCurrentToDashboard} className="action-button action-secondary text-xs px-4 py-2 rounded-full shadow-sm">
                  Save to dashboard
                </button>
              </div>
            </header>

            {!activeSource ? (
              <EmptyState onUpload={() => setView("upload")} onConnect={() => setView("connect")} />
            ) : (
              <>
                {schema && schema.tables?.length > 0 && (
                  <SchemaStrip tables={schema.tables} />
                )}

                <div ref={scrollerRef} className="flex-1 overflow-y-auto scrollbar-thin px-6 py-5 space-y-5">
                  {messages.length === 0 && (
                    <div className="max-w-3xl mx-auto pt-6">
                      <div className="hero-panel text-center mb-6">
                        <div className="mx-auto mb-4 inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-900 text-white shadow-lg shadow-slate-900/20">
                          ?
                        </div>
                        <h2 className="text-2xl font-semibold text-slate-900 tracking-tight">Ask a question about your data</h2>
                        <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-slate-500">DataWhisper understands your schema, plans the analysis, writes SQL, validates it, and explains the result in plain English.</p>
                      </div>
                      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                        {EXAMPLE_QUESTIONS.map((q) => (
                          <button key={q} onClick={() => ask(q)} className="question-chip text-left px-4 py-3 rounded-2xl border border-white/80 bg-white/80 text-sm text-slate-700 shadow-card hover:border-brand-200">
                            {q}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {messages.map((m, i) => (
                    <MessageBubble key={i} m={m} onFollowup={ask} />
                  ))}
                </div>

                <div className="border-t border-slate-200 bg-white/90 px-6 py-3 backdrop-blur-sm">
                  <form
                    onSubmit={(e) => { e.preventDefault(); ask(); }}
                    className="composer-shell max-w-4xl mx-auto flex gap-3 rounded-3xl border border-slate-200 bg-white p-3 shadow-card"
                  >
                    <input
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      placeholder={`Ask about ${activeSource?.name || "your data"}...`}
                      className="flex-1 border-0 bg-transparent px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none"
                    />
                    <button type="submit" disabled={loading} className="action-button action-primary px-5 py-3 rounded-2xl text-sm font-medium text-white disabled:opacity-50">
                      {loading ? "Thinking..." : "Ask"}
                    </button>
                  </form>
                </div>
              </>
            )}
          </>
        )}

        {view === "main" && tab === "history" && <HistoryPanel />}
        {view === "main" && tab === "dashboards" && <DashboardsPanel dashboards={dashboards} />}
      </main>
    </div>
  );
}

function TabButton({ active, onClick, children }) {
  return (
    <button onClick={onClick} className={`flex-1 text-xs px-3 py-2.5 rounded-2xl transition ${active ? "tab-active font-medium" : "tab-inactive"}`}>
      {children}
    </button>
  );
}

function Logo() {
  return (
    <div className="logo-mark w-10 h-10 rounded-2xl bg-slate-900 flex items-center justify-center text-white font-bold shadow-lg shadow-slate-900/20">
      DW
    </div>
  );
}

function EmptyState({ onUpload, onConnect }) {
  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="empty-state-card max-w-xl text-center rounded-[2rem] border border-white/70 bg-white/75 p-10 shadow-soft">
        <div className="mx-auto mb-5 flex h-18 w-18 items-center justify-center rounded-[1.6rem] bg-slate-900 text-2xl font-bold text-white shadow-lg shadow-slate-900/20">
          ?
        </div>
        <h2 className="text-3xl font-semibold text-slate-900 tracking-tight mb-3">The ChatGPT for Business Analytics</h2>
        <p className="mx-auto max-w-lg text-slate-500 text-sm leading-6 mb-8">
          Upload a CSV or connect a database. Ask questions in plain English. DataWhisper understands the schema, plans the analysis, writes SQL, validates it, and explains the results.
        </p>
        <div className="flex flex-wrap gap-3 justify-center">
          <button onClick={onUpload} className="action-button action-primary px-6 py-3 rounded-2xl text-sm font-medium text-white">Upload CSV</button>
          <button onClick={onConnect} className="action-button action-secondary px-6 py-3 rounded-2xl text-sm font-medium text-slate-800">Connect Database</button>
        </div>
      </div>
    </div>
  );
}

function SchemaStrip({ tables }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="px-6 py-3 border-b border-white/70 bg-white/55 backdrop-blur-sm">
      <button onClick={() => setOpen(!open)} className="text-xs font-medium text-slate-500 hover:text-slate-700">
        {open ? "▼" : "▶"} Schema ({tables.length} tables)
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          {tables.map((t) => (
            <div key={t.table} className="rounded-2xl border border-white/80 bg-white/90 p-4 shadow-card">
              <div className="font-mono text-xs font-semibold text-slate-900">{t.table} <span className="text-slate-400">({t.row_count?.toLocaleString()} rows)</span></div>
              <div className="flex flex-wrap gap-1.5 mt-2">
                {t.columns.map((c) => (
                  <span key={c.name} className={`text-[10px] px-2.5 py-1 rounded-full ${kindColor(c.kind)} ${c.is_pk ? "ring-1 ring-amber-300" : ""} ${c.is_fk ? "ring-1 ring-emerald-300" : ""}`}>
                    {c.name} <span className="opacity-60">{c.kind}</span>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function kindColor(kind) {
  return {
    numeric: "bg-blue-50 text-blue-700",
    categorical: "bg-violet-50 text-violet-700",
    temporal: "bg-amber-50 text-amber-700",
    boolean: "bg-emerald-50 text-emerald-700",
  }[kind] || "bg-slate-50 text-slate-600";
}

function MessageBubble({ m, onFollowup }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="message-user max-w-2xl px-4 py-3 rounded-[1.4rem] text-sm text-white shadow-button">
          {m.content}
        </div>
      </div>
    );
  }
  if (m.loading) {
    return (
      <div className="flex justify-start">
        <div className="max-w-2xl px-4 py-3 rounded-[1.4rem] bg-white/80 border border-white/80 text-sm text-slate-500 shadow-card">
          <span className="inline-flex gap-1">
            <span className="animate-bounce">·</span>
            <span className="animate-bounce" style={{ animationDelay: "0.1s" }}>·</span>
            <span className="animate-bounce" style={{ animationDelay: "0.2s" }}>·</span>
          </span>
          <span className="ml-2">Analyzing...</span>
        </div>
      </div>
    );
  }
  const a = m.content || {};
  return (
    <div className="flex justify-start">
      <div className="max-w-3xl w-full bg-white/85 border border-white/80 rounded-[1.7rem] p-5 space-y-4 shadow-card">
        {a.error ? (
          <div className="text-sm text-red-600">
            <div className="font-medium flex items-center gap-2">
              <span className="inline-flex h-2 w-2 rounded-full bg-red-500" />
              Something went wrong
            </div>
            <div className="text-xs text-red-500 mt-1">{a.error}</div>
            {a.sql && <pre className="mt-3 text-[11px] bg-slate-950 text-slate-100 p-3 rounded-2xl overflow-x-auto">{a.sql}</pre>}
          </div>
        ) : (
          <>
            {a.insight && (
              <div className="text-sm text-slate-700 leading-relaxed">
                <div className="text-[11px] uppercase tracking-[0.22em] text-brand-700 font-semibold mb-2">Insight</div>
                {a.insight}
              </div>
            )}
            {a.chart && <ChartView chart={a.chart} />}
            {a.sql && (
              <details className="text-xs">
                <summary className="cursor-pointer text-slate-500 hover:text-slate-700">SQL ({a.execution_time?.toFixed ? a.execution_time.toFixed(2) : a.execution_time}s{a.retries ? `, ${a.retries} retries` : ""})</summary>
                <pre className="mt-3 text-[11px] bg-slate-950 text-slate-100 p-4 rounded-2xl overflow-x-auto shadow-inner">{a.sql}</pre>
              </details>
            )}
            {a.rows && a.rows.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-slate-500 hover:text-slate-700">Rows ({a.rows.length})</summary>
                <div className="mt-3 overflow-x-auto rounded-2xl border border-slate-200/80">
                  <table className="w-full text-[11px]">
                    <thead className="bg-slate-50/90 text-slate-500">
                      <tr>
                        {Object.keys(a.rows[0]).map((k) => <th key={k} className="px-2 py-1 text-left font-medium">{k}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {a.rows.slice(0, 20).map((r, i) => (
                        <tr key={i} className="border-t border-slate-100">
                          {Object.values(r).map((v, j) => <td key={j} className="px-2 py-1 font-mono">{String(v ?? "")}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
            {a.plan && (
              <details className="text-xs">
                <summary className="cursor-pointer text-slate-500 hover:text-slate-700">Plan ({a.plan.length} steps)</summary>
                <ol className="mt-3 space-y-2 list-decimal list-inside text-slate-600">
                  {a.plan.map((s) => <li key={s.id}>{s.action}: <span className="text-slate-400">{s.description}</span></li>)}
                </ol>
              </details>
            )}
            {a.followups && a.followups.length > 0 && (
              <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-100">
                {a.followups.map((f, i) => (
                  <button key={i} onClick={() => onFollowup(f)} className="text-xs px-3 py-1.5 rounded-full bg-brand-50 text-brand-700 hover:bg-brand-100 transition">
                    {f}
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function ChartView({ chart }) {
  const { type, data, config, label_col, value_col } = chart || {};
  if (!data || data.length === 0) return null;
  if (type === "line") {
    return (
      <div className="chart-shell h-72 w-full rounded-3xl border border-slate-200/80 bg-gradient-to-br from-white to-slate-50 p-3 shadow-card">
        <ResponsiveContainer>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="4 4" stroke="#e2e8f0" />
            <XAxis dataKey="x" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip contentStyle={{ borderRadius: 16, border: "1px solid #e2e8f0", boxShadow: "0 12px 32px rgba(15, 23, 42, 0.08)" }} />
            <Line type="monotone" dataKey="y" stroke="#2563eb" strokeWidth={3} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  }
  // bar / grouped_bar
  return (
    <div className="chart-shell h-72 w-full rounded-3xl border border-slate-200/80 bg-gradient-to-br from-white to-slate-50 p-3 shadow-card">
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical">
          <CartesianGrid strokeDasharray="4 4" stroke="#e2e8f0" />
          <XAxis type="number" tick={{ fontSize: 11 }} />
          <YAxis type="category" dataKey="label" tick={{ fontSize: 11 }} width={120} />
          <Tooltip contentStyle={{ borderRadius: 16, border: "1px solid #e2e8f0", boxShadow: "0 12px 32px rgba(15, 23, 42, 0.08)" }} />
          <Bar dataKey="value" fill="#2563eb" radius={[0, 12, 12, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function UploadModal({ onClose, onUpload, loading }) {
  const [files, setFiles] = useState([]);
  const inputRef = useRef(null);
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center p-4 modal-backdrop">
      <div className="modal-card bg-white rounded-[1.75rem] max-w-md w-full p-6 shadow-2xl">
        <div className="flex justify-between items-center mb-4">
          <h3 className="font-semibold text-slate-900">Upload CSV / Excel</h3>
          <button onClick={onClose} className="rounded-full p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
        </div>
        <div
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); setFiles(Array.from(e.dataTransfer.files)); }}
          className="border-2 border-dashed border-slate-200 rounded-3xl p-8 text-center cursor-pointer bg-slate-50/70 transition hover:border-brand-300 hover:bg-brand-50/40"
        >
          <input ref={inputRef} type="file" multiple accept=".csv,.xlsx,.xls" className="hidden" onChange={(e) => setFiles(Array.from(e.target.files))} />
          {files.length ? (
            <div className="text-sm text-slate-700">{files.map((f) => f.name).join(", ")}</div>
          ) : (
            <div className="text-sm text-slate-500">Click or drop files here (.csv, .xlsx)</div>
          )}
        </div>
        <button
          disabled={!files.length || loading}
          onClick={() => onUpload(files)}
          className="action-button action-primary mt-4 w-full py-3 rounded-2xl text-white text-sm font-medium disabled:opacity-50"
        >
          {loading ? "Uploading..." : "Upload and analyze"}
        </button>
      </div>
    </div>
  );
}

function ConnectModal({ onClose, onConnect, loading }) {
  const [type, setType] = useState("postgresql");
  const [name, setName] = useState("");
  const [conn, setConn] = useState("");
  const [path, setPath] = useState("");
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center p-4 modal-backdrop">
      <div className="modal-card bg-white rounded-[1.75rem] max-w-lg w-full p-6 shadow-2xl">
        <div className="flex justify-between items-center mb-4">
          <h3 className="font-semibold text-slate-900">Connect a database</h3>
          <button onClick={onClose} className="rounded-full p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-slate-500">Type</label>
            <select value={type} onChange={(e) => setType(e.target.value)} className="w-full mt-1 px-3 py-3 rounded-2xl border border-slate-200 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500">
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
              <option value="snowflake">Snowflake</option>
              <option value="bigquery">BigQuery</option>
              <option value="sqlite">SQLite (file path)</option>
              <option value="duckdb">DuckDB (file path)</option>
            </select>
          </div>
          <Input label="Name" value={name} onChange={setName} placeholder="Production DB" />
          {["sqlite", "duckdb"].includes(type) ? (
            <Input label="Database file path" value={path} onChange={setPath} placeholder="C:\path\to\db.sqlite" />
          ) : (
            <Input label="Connection string" value={conn} onChange={setConn} placeholder="postgresql+psycopg2://user:pass@host:5432/db" />
          )}
          <button
            disabled={loading}
            onClick={() => onConnect({
              type, name: name || type,
              ...(type === "sqlite" || type === "duckdb" ? { path } : { connection_string: conn }),
            })}
            className="action-button action-primary w-full py-3 rounded-2xl text-white text-sm font-medium disabled:opacity-50"
          >
            {loading ? "Connecting..." : "Connect"}
          </button>
          <div className="text-[11px] text-slate-400">
            Connections are read-only. Credentials are encrypted at rest in the application database.
          </div>
        </div>
      </div>
    </div>
  );
}

function Input({ label, value, onChange, placeholder }) {
  return (
    <div>
      <label className="text-xs font-medium text-slate-500">{label}</label>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full mt-1 px-3 py-3 rounded-2xl border border-slate-200 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
      />
    </div>
  );
}

function HistoryPanel() {
  const [items, setItems] = useState(null);
  useEffect(() => { (async () => { try { const d = await getHistory(); setItems(d.history || []); } catch (e) { setItems([]); } })(); }, []);
  if (items === null) return <div className="p-8 text-slate-400 text-sm">Loading history...</div>;
  if (items.length === 0) return <div className="p-8 text-slate-400 text-sm">No queries yet.</div>;
  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin p-6">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900">Query History</h2>
        <span className="text-xs text-slate-400">{items.length} items</span>
      </div>
      <div className="space-y-3 max-w-4xl">
        {items.map((h) => (
          <div key={h.id} className="bg-white/85 border border-white/80 rounded-[1.5rem] p-4 shadow-card">
            <div className="flex justify-between items-start">
              <div>
                <div className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${h.status === "success" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>{h.status}</div>
                <div className="font-medium text-slate-800 mt-2">{h.question}</div>
              </div>
              <div className="text-[11px] text-slate-400">{h.execution_time?.toFixed ? h.execution_time.toFixed(2) : h.execution_time}s</div>
            </div>
            {h.sql && <pre className="mt-3 text-[11px] bg-slate-950 text-slate-100 p-3 rounded-2xl overflow-x-auto">{h.sql}</pre>}
            {h.answer?.insight && <div className="mt-2 text-sm text-slate-600">{h.answer.insight}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

function DashboardsPanel({ dashboards }) {
  if (dashboards.length === 0) return <div className="p-8 text-slate-400 text-sm">No saved dashboards yet. From the Chat tab, click "Save to dashboard".</div>;
  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900">Saved dashboards</h2>
        <span className="text-xs text-slate-400">{dashboards.length} saved</span>
      </div>
      {dashboards.map((d) => (
        <div key={d.id} className="bg-white/85 border border-white/80 rounded-[1.5rem] p-4 shadow-card">
          <div className="flex justify-between items-start mb-3">
            <div>
              <div className="font-semibold text-slate-900">{d.title}</div>
              <div className="text-[11px] text-slate-400">Saved {new Date((d.updated_at || 0) * 1000).toLocaleString()}</div>
            </div>
          </div>
          {d.layout?.insight && <div className="text-sm text-slate-600 mb-2">{d.layout.insight}</div>}
          {d.layout?.chart && <ChartView chart={d.layout.chart} />}
        </div>
      ))}
    </div>
  );
}
