import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import mermaid from "mermaid";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  FileDown,
  FileSearch,
  Gauge,
  History,
  Mail,
  RefreshCw,
  Save,
  Search,
  Settings2,
  SlidersHorizontal,
} from "lucide-react";
import "./styles.css";

const API = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8001";
const THROUGHPUT_CATEGORIES = [
  ["automated", "Automated"],
  ["review", "Review"],
  ["failed", "Failed"],
  ["filed", "Filed"],
];

function useApi(path, fallback, deps = []) {
  const [data, setData] = useState(fallback);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    fetch(`${API}${path}`)
      .then(async (res) => {
        if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
        return res.json();
      })
      .then((json) => alive && setData(json))
      .catch((err) => alive && setError(err.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, deps);

  return { data, loading, error, setData };
}

function formatNumber(value) {
  if (value === null || value === undefined) return "-";
  return Number(value).toLocaleString();
}

function formatDateTime(value) {
  if (!value) return "-";
  return String(value).replace("T", " ").slice(0, 19);
}

function formatOutcomeMetric(data, outcome) {
  const count = data.outcomes?.[outcome] || 0;
  const rate = data.rates?.[outcome] ?? 0;
  return `${formatNumber(count)} (${rate}%)`;
}

function formatRunMetric(data, status) {
  const count = data.runs?.[status] || 0;
  const total = Object.values(data.runs || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  const rate = total ? Math.round((count / total) * 1000) / 10 : 0;
  return `${formatNumber(count)} (${rate}%)`;
}

function displaySender(email) {
  const senderName = email?.metadata?.sender_name;
  const senderEmail = email?.sender_email;
  if (senderName && senderEmail) return `${senderName} <${senderEmail}>`;
  return senderEmail || senderName || "-";
}

function ToggleGroup({ page, setPage }) {
  const pages = [
    ["monitor", "Monitor", Gauge],
    ["detail", "Email Detail", FileSearch],
    ["management", "Management", Settings2],
  ];
  return (
    <div className="toggleGroup" role="tablist" aria-label="Dashboard pages">
      {pages.map(([key, label, Icon]) => (
        <button key={key} className={page === key ? "active" : ""} onClick={() => setPage(key)}>
          <Icon size={16} />
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}

function RangeSelector({ days, setDays }) {
  return (
    <div className="range" aria-label="Monitor date range">
      {[7, 30, 90].map((value) => (
        <button key={value} className={days === value ? "active" : ""} onClick={() => setDays(value)}>
          {value}d
        </button>
      ))}
    </div>
  );
}

function Kpi({ label, value, sub, tone = "blue", icon: Icon = Activity }) {
  return (
    <section className={`kpi ${tone}`}>
      <div className="kpiTop">
        <span>{label}</span>
        <Icon size={18} />
      </div>
      <strong>{value}</strong>
      <small>{sub}</small>
    </section>
  );
}

function Monitor({ days, openEmail }) {
  const [reviewRefresh, setReviewRefresh] = useState(0);
  const summary = useApi(`/api/monitor/summary?days=${days}`, {}, [days]);
  const throughput = useApi(`/api/monitor/throughput?days=${days}`, [], [days]);
  const reasons = useApi(`/api/monitor/review-reasons?days=${days}`, [], [days]);
  const destinations = useApi(`/api/monitor/destinations?days=${days}`, [], [days]);
  const reviewEmails = useApi(`/api/monitor/review-emails?limit=25&refresh=${reviewRefresh}`, [], [reviewRefresh]);
  const recent = useApi("/api/monitor/recent-runs?limit=20", [], [days]);
  const data = summary.data;
  const maxDay = Math.max(
    1,
    ...throughput.data.map((row) => THROUGHPUT_CATEGORIES.reduce((sum, [key]) => sum + (row[key] || 0), 0)),
  );

  async function completeReview(reviewId) {
    const res = await fetch(`${API}/api/review-queue/${reviewId}/complete`, { method: "PATCH" });
    if (!res.ok) throw new Error((await res.json()).detail || "Review completion failed.");
    setReviewRefresh((value) => value + 1);
  }

  return (
    <main className="workspace">
      {summary.error && <Banner tone="bad" text={summary.error} />}
      <div className="kpiGrid">
        <Kpi label="Processed" value={formatNumber(data.total_processed)} sub={`${days} day window`} icon={Mail} />
        <Kpi label="Automated" value={formatOutcomeMetric(data, "AUTO")} sub="auto routed" tone="green" icon={CheckCircle2} />
        <Kpi label="Review" value={formatOutcomeMetric(data, "REVIEW")} sub={`${formatNumber(data.open_review_count)} open review items`} tone="amber" icon={AlertTriangle} />
        <Kpi label="Filed" value={formatOutcomeMetric(data, "FILE")} sub="filed locally" tone="blue" icon={History} />
        <Kpi label="Flagged" value={formatOutcomeMetric(data, "FLAG")} sub="critical or misdirected" tone="red" icon={AlertTriangle} />
        <Kpi label="Discarded" value={formatOutcomeMetric(data, "DISCARD")} sub="logged no-action emails" tone="blue" icon={Mail} />
        <Kpi label="Failed runs" value={formatRunMetric(data, "failed")} sub="failed attempts" tone="red" icon={Activity} />
        <Kpi label="Avg processing" value={data.avg_processing_seconds ? `${data.avg_processing_seconds}s` : "-"} sub="completed runs only" tone="violet" icon={RefreshCw} />
      </div>

      <div className="split">
        <section className="panel wide">
          <PanelTitle title="Daily throughput" />
          <div className="trendLegend">
            {THROUGHPUT_CATEGORIES.map(([key, label]) => (
              <span key={key}><i className={key} />{label}</span>
            ))}
          </div>
          <div className="trendChart">
            {throughput.data.length === 0 && <Empty text="No decisions in this range." />}
            {throughput.data.map((row) => {
              const total = THROUGHPUT_CATEGORIES.reduce((sum, [key]) => sum + (row[key] || 0), 0);
              return (
                <div className="trendColumn" key={row.day}>
                  <div className="trendBar" style={{ height: `${Math.max(3, (total / maxDay) * 100)}%` }} title={`${row.day}: ${total}`}>
                    {THROUGHPUT_CATEGORIES.map(([key, label]) => (
                      <i key={key} className={key} style={{ flexGrow: row[key] || 0 }} title={`${label}: ${row[key] || 0}`} />
                    ))}
                  </div>
                  <b>{total}</b>
                  <span>{row.day.slice(5)}</span>
                </div>
              );
            })}
          </div>
        </section>

        <TopList title="Top destinations" rows={destinations.data} labelKey="display_name" />
      </div>

      <div className="split">
        <ReviewEmails rows={reviewEmails.data} openEmail={openEmail} completeReview={completeReview} />
        <TopList title="Review reasons" rows={reasons.data} labelKey="reason" />
      </div>

      <section className="panel recentRuns">
        <PanelTitle title="Recent processing runs" />
        <div className="table">
          <div className="thead">
            <span>Status</span><span>Subject</span><span>Outcome</span><span>Reason</span><span>Started</span>
          </div>
          {recent.data.map((row) => (
            <button className="tr" key={row.run_id} onClick={() => row.email_id && openEmail(row.email_id)}>
              <span><Badge label={row.status} /></span>
              <span>{row.subject || "No subject"}</span>
              <span>{row.final_outcome || "-"}</span>
              <span>{row.reason || "-"}</span>
              <span>{row.started_at?.replace("T", " ").slice(0, 19)}</span>
            </button>
          ))}
        </div>
      </section>
    </main>
  );
}

function ReviewEmails({ rows, openEmail, completeReview }) {
  const [error, setError] = useState("");

  async function onComplete(event, reviewId) {
    event.stopPropagation();
    try {
      setError("");
      await completeReview(reviewId);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section className="panel">
      <PanelTitle title="Review Emails" icon={AlertTriangle} />
      {error && <Banner tone="bad" text={error} />}
      <div className="reviewEmailList">
        {rows.length === 0 && <Empty text="No outstanding review emails." />}
        {rows.map((row) => (
          <div className="reviewEmail" key={row.review_id}>
            <button className="reviewEmailOpen" onClick={() => openEmail(row.email_id)}>
              <strong>{row.subject || "No subject"}</strong>
              <small>{row.sender_name || row.sender_email || "Unknown sender"}</small>
              <small>{row.reason}</small>
            </button>
            <button className="inlineAction" onClick={(event) => onComplete(event, row.review_id)}>
              Complete
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function TopList({ title, rows, labelKey }) {
  const max = Math.max(1, ...rows.map((row) => row.count || 0));
  const className = title === "Review reasons" ? "panel reviewReasons" : "panel";
  return (
    <section className={className}>
      <PanelTitle title={title} />
      <div className="topList">
        {rows.length === 0 && <Empty text="No data yet." />}
        {rows.map((row) => (
          <div className="topItem" key={`${row[labelKey]}-${row.count}`}>
            <span>{row[labelKey] || "Unknown"}</span>
            <div><i style={{ width: `${((row.count || 0) / max) * 100}%` }} /></div>
            <b>{row.count}</b>
          </div>
        ))}
      </div>
    </section>
  );
}

function EmailDetail({ selectedEmailId, setSelectedEmailId }) {
  const [query, setQuery] = useState("");
  const [searchPath, setSearchPath] = useState("/api/emails/search?limit=25");
  const results = useApi(searchPath, [], [searchPath]);
  const detail = useApi(selectedEmailId ? `/api/emails/${selectedEmailId}` : "/api/emails/search?limit=1", {}, [selectedEmailId]);
  const selected = selectedEmailId ? detail.data : null;
  const firstRun = selected?.audit_runs?.[0]?.run_id;
  const latestDecision = selected?.decisions?.[0];

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const text = query.trim();
      setSearchPath(text ? `/api/emails/search?q=${encodeURIComponent(text)}&limit=50` : "/api/emails/search?limit=25");
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  return (
    <main className="workspace detailGrid">
      <section className="panel searchPanel">
        <PanelTitle title="Search email" icon={Search} />
        <div className="search">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Subject, sender, vendor, invoice, property, reason" />
        </div>
        <div className="resultList">
          {results.data.map((row) => (
            <button key={row.email_id} className={selectedEmailId === row.email_id ? "selected" : ""} onClick={() => setSelectedEmailId(row.email_id)}>
              <strong>{row.subject || "No subject"}</strong>
              <span>{row.sender_email || "Unknown sender"}</span>
              <small>{formatDateTime(row.received_at)} - {row.vendor_name || row.reason || row.email_id}</small>
            </button>
          ))}
        </div>
      </section>

      <section className="detailMain">
        {!selectedEmailId && <EmptyPanel text="Search for or select an email to inspect." />}
        {selectedEmailId && detail.error && <Banner tone="bad" text={detail.error} />}
        {selected && selected.email && (
          <>
            <section className="panel emailHeader">
              <PanelTitle title="Email" icon={Mail} />
              <div className="emailSummary">
                <div className="emailSummaryMain">
                  <strong>{selected.email.subject || "No subject"}</strong>
                  <span>From {displaySender(selected.email)}</span>
                </div>
                <div className="emailSummaryDecision">
                  {latestDecision ? <Badge label={latestDecision.outcome} /> : <Badge label="No decision" />}
                  <span>{latestDecision?.reason || "No decision reason recorded."}</span>
                </div>
              </div>
            </section>

            <div className="emailContentGrid">
              <section className="panel emailViewerPanel">
                {selected.html_available ? (
                  <iframe className="emailFrame" title="Email preview" src={`${API}/api/emails/${selected.email.email_id}/html`} />
                ) : (
                  <Empty text="No sanitized HTML preview exists for this email yet." />
                )}
                {selected.attachments?.length > 0 && (
                  <div className="attachmentList">
                    {selected.attachments.map((item) => (
                      <a key={item.attachment_id} href={`${API}/api/attachments/${item.attachment_id}/download`}>
                        <FileDown size={16} />
                        <span>{item.file_name}</span>
                        <small>{formatNumber(item.file_size_bytes)} bytes</small>
                      </a>
                    ))}
                  </div>
                )}
              </section>

              <div className="auditColumn">
                {firstRun ? <AuditTrace runId={firstRun} /> : <EmptyPanel text="No audit run recorded." />}
              </div>
            </div>
          </>
        )}
      </section>
    </main>
  );
}

function AuditTrace({ runId }) {
  const run = useApi(`/api/audit-runs/${runId}`, {}, [runId]);
  const [diagram, setDiagram] = useState("");
  const [renderError, setRenderError] = useState("");

  useEffect(() => {
    mermaid.initialize({ startOnLoad: false, theme: "dark" });
  }, []);

  useEffect(() => {
    async function render() {
      const path = run.data?.run?.trace_artifact_path;
      if (!path) {
        setDiagram("");
        return;
      }
      try {
        setRenderError("");
        const text = await fetch(`${API}/api/artifacts?path=${encodeURIComponent(path)}`).then((res) => res.text());
        const rendered = await mermaid.render(`trace-${runId}`, text);
        setDiagram(rendered.svg);
      } catch (err) {
        setRenderError(err.message);
      }
    }
    render();
  }, [run.data, runId]);

  return (
    <section className="panel auditPanel">
      <PanelTitle title="Audit trace" icon={History} />
      {renderError && <Banner tone="bad" text={renderError} />}
      {diagram ? <div className="mermaidBox" dangerouslySetInnerHTML={{ __html: diagram }} /> : <Empty text="No Mermaid trace artifact available." />}
      <div className="steps">
        {run.data?.steps?.map((step) => (
          <div key={step.step_id}>
            <Badge label={step.step_type} />
            <span>{step.reason || step.error || "Recorded"}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Management() {
  const [refresh, setRefresh] = useState(0);
  const rules = useApi("/api/workflow/rules", [], [refresh]);
  const destinations = useApi("/api/workflow/destinations", [], [refresh]);
  const config = useApi("/api/workflow/runtime-config", [], [refresh]);
  const audit = useApi("/api/workflow/audit-events", [], [refresh]);
  const [editing, setEditing] = useState(null);
  const [message, setMessage] = useState("");

  async function saveRule() {
    setMessage("");
    const body = {
      enabled: editing.enabled,
      priority: Number(editing.priority),
      reason_template: editing.reason_template,
      change_reason: "Local dashboard edit",
    };
    const res = await fetch(`${API}/api/workflow/rules/${editing.rule_code}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      setMessage((await res.json()).detail || "Save failed.");
      return;
    }
    setEditing(null);
    setRefresh((value) => value + 1);
  }

  return (
    <main className="workspace">
      <div className="toolbar">
        <div>
          <h1>Workflow management</h1>
          <p>Local table-driven rules, destinations, runtime config, and management audit history.</p>
        </div>
      </div>
      {message && <Banner tone="bad" text={message} />}
      <div className="managementGrid">
        <section className="panel wide">
          <PanelTitle title="Workflow rules" icon={SlidersHorizontal} />
          <div className="ruleList">
            {rules.data.map((rule) => (
              <button key={rule.rule_code} className="ruleRow" onClick={() => setEditing(rule)}>
                <span><Badge label={rule.enabled ? "enabled" : "disabled"} /> {rule.priority}</span>
                <strong>{rule.rule_name}</strong>
                <small>{rule.rule_code} · v{rule.version} · {rule.outcome}</small>
              </button>
            ))}
          </div>
        </section>
        <section className="panel">
          <PanelTitle title="Edit rule" icon={Save} />
          {editing ? (
            <div className="editForm">
              <label>Enabled<input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} /></label>
              <label>Priority<input value={editing.priority} onChange={(e) => setEditing({ ...editing, priority: e.target.value })} /></label>
              <label>Reason template<textarea value={editing.reason_template} onChange={(e) => setEditing({ ...editing, reason_template: e.target.value })} /></label>
              <button onClick={saveRule}><Save size={16} />Save audited version</button>
            </div>
          ) : <Empty text="Select a rule to edit." />}
        </section>
      </div>

      <div className="split">
        <section className="panel">
          <PanelTitle title="Destinations" />
          <div className="compactList">
            {destinations.data.map((row) => <span key={row.destination_code}>{row.destination_code}<small>{row.display_name}</small></span>)}
          </div>
        </section>
        <section className="panel">
          <PanelTitle title="Runtime config" />
          <div className="compactList">
            {config.data.map((row) => <span key={row.config_key}>{row.config_key}<small>{JSON.stringify(row.config_value)}</small></span>)}
          </div>
        </section>
      </div>

      <section className="panel">
        <PanelTitle title="Management audit history" />
        <div className="compactList">
          {audit.data.map((row) => <span key={row.management_audit_event_id}>{row.changed_table} · {row.changed_key}<small>{row.change_type} · {row.changed_at}</small></span>)}
        </div>
      </section>
    </main>
  );
}

function PanelTitle({ title, icon: Icon = Activity }) {
  return <h2 className="panelTitle"><Icon size={16} />{title}</h2>;
}

function Badge({ label }) {
  return <span className={`badge ${String(label).toLowerCase()}`}>{label}</span>;
}

function Fact({ label, value }) {
  return <div><span>{label}</span><strong>{value || "-"}</strong></div>;
}

function Empty({ text }) {
  return <div className="empty">{text}</div>;
}

function EmptyPanel({ text }) {
  return <section className="panel emptyPanel">{text}</section>;
}

function Banner({ text, tone = "blue" }) {
  return <div className={`banner ${tone}`}>{text}</div>;
}

function App() {
  const [page, setPage] = useState("monitor");
  const [monitorDays, setMonitorDays] = useState(7);
  const [selectedEmailId, setSelectedEmailId] = useState("");
  const openEmail = (emailId) => {
    setSelectedEmailId(emailId);
    setPage("detail");
  };

  return (
    <div className="appShell">
      <header className="appHeader">
        <div className="brand">
          <div className="brandMark">H</div>
          <div>
            <strong>AP Automation</strong>
            <span>LOCAL · DRY RUN</span>
          </div>
        </div>
        <ToggleGroup page={page} setPage={setPage} />
        <div className="headerActions">
          {page === "monitor" && <RangeSelector days={monitorDays} setDays={setMonitorDays} />}
        </div>
      </header>
      {page === "monitor" && <Monitor days={monitorDays} openEmail={openEmail} />}
      {page === "detail" && <EmailDetail selectedEmailId={selectedEmailId} setSelectedEmailId={setSelectedEmailId} />}
      {page === "management" && <Management />}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
