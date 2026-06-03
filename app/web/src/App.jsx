import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import mermaid from "mermaid";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  FileDown,
  FileSearch,
  Gauge,
  History,
  Mail,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  Settings2,
  Trash2,
  X,
} from "lucide-react";
import "./styles.css";

const API =
  import.meta.env.VITE_API_BASE ||
  (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1" ? "http://127.0.0.1:8001" : "");
const THROUGHPUT_CATEGORIES = [
  ["automated", "Automated"],
  ["escalate", "Escalate"],
  ["failed", "Failed"],
  ["filed", "Filed"],
];
const API_HTML_RESPONSE_MESSAGE =
  "Dashboard API returned HTML instead of JSON; verify the Static Web App is linked to the Function App backend.";

async function readJsonResponse(res, fallbackMessage) {
  const contentType = res.headers.get("Content-Type") || "";
  const normalizedContentType = contentType.toLowerCase();
  const isJson = normalizedContentType.includes("application/json") || normalizedContentType.includes("+json");
  if (!isJson) {
    const text = await res.text();
    if (text.trimStart().startsWith("<!DOCTYPE") || normalizedContentType.includes("text/html")) {
      throw new Error(API_HTML_RESPONSE_MESSAGE);
    }
    throw new Error(fallbackMessage || res.statusText || "Dashboard API returned a non-JSON response.");
  }

  const json = await res.json();
  if (!res.ok) {
    const detail = json.detail || res.statusText;
    throw new Error(fallbackMessage && detail ? `${fallbackMessage} ${detail}` : detail || fallbackMessage || "Request failed.");
  }
  return json;
}

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
        return readJsonResponse(res);
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
  const [escalateRefresh] = useState(0);
  const summary = useApi(`/api/monitor/summary?days=${days}`, {}, [days]);
  const throughput = useApi(`/api/monitor/throughput?days=${days}`, [], [days]);
  const escalateEmails = useApi(`/api/monitor/escalate-emails?limit=25&refresh=${escalateRefresh}`, [], [escalateRefresh]);
  const recent = useApi("/api/monitor/recent-runs?limit=20", [], [days]);
  const data = summary.data;
  const maxDay = Math.max(
    1,
    ...throughput.data.map((row) => THROUGHPUT_CATEGORIES.reduce((sum, [key]) => sum + (row[key] || 0), 0)),
  );

  return (
    <main className="workspace">
      {summary.error && <Banner tone="bad" text={summary.error} />}
      <div className="kpiGrid">
        <Kpi label="Processed" value={formatNumber(data.total_processed)} sub={`${days} day window`} icon={Mail} />
        <Kpi label="Automated" value={formatOutcomeMetric(data, "AUTO")} sub="auto routed" tone="green" icon={CheckCircle2} />
        <Kpi label="Escalate" value={formatOutcomeMetric(data, "ESCALATE")} sub={`${formatNumber(data.open_escalate_count)} open escalation items`} tone="amber" icon={AlertTriangle} />
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

        <EscalateEmails rows={escalateEmails.data} openEmail={openEmail} />
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

function EscalateEmails({ rows, openEmail }) {
  return (
    <section className="panel">
      <PanelTitle title="Escalate Emails" icon={AlertTriangle} />
      <div className="escalateEmailList">
        {rows.length === 0 && <Empty text="No outstanding escalation emails." />}
        {rows.map((row) => (
          <div className="escalateEmail" key={row.escalate_id}>
            <button className="escalateEmailOpen" onClick={() => openEmail(row.email_id)}>
              <strong>{row.subject || "No subject"}</strong>
              <small>{row.sender_name || row.sender_email || "Unknown sender"}</small>
              <small>{row.reason}</small>
            </button>
            {row.office_web_link && <OpenPortalButton href={row.office_web_link} />}
          </div>
        ))}
      </div>
    </section>
  );
}

function OpenPortalButton({ href, className = "" }) {
  return (
    <a className={`portalOpenButton ${className}`.trim()} href={href} target="_blank" rel="noreferrer">
      <ExternalLink size={15} />
      <span>Open</span>
    </a>
  );
}

function EmailDetail({ selectedEmailId, setSelectedEmailId }) {
  const [query, setQuery] = useState("");
  const [searchPath, setSearchPath] = useState("/api/emails/search?limit=25");
  const results = useApi(searchPath, [], [searchPath]);
  const detail = useApi(selectedEmailId ? `/api/emails/${selectedEmailId}` : "/api/emails/search?limit=1", {}, [selectedEmailId]);
  const selected = selectedEmailId ? detail.data : null;
  const firstRun = useMemo(() => {
    const runs = selected?.audit_runs || [];
    const finalized = runs.find((run) => run.status === "completed" || run.status === "failed");
    return (finalized || runs[0])?.run_id;
  }, [selected]);
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
              {selected.email.office_web_link && <OpenPortalButton href={selected.email.office_web_link} className="emailHeaderOpen" />}
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

const OWNERSHIP_COLUMNS = [
  { key: "ownership", label: "Ownership" },
  { key: "destination", label: "Destination" },
];

const ASSET_LOOKUP_COLUMNS = [
  "Asset Name",
  "Asset Alias",
  "Address",
  "Ownership",
  "Destination Code",
  "Destination Active",
  "Asset Type",
  "Market",
  "Market Area",
];

const ASSET_CUSTOM_COLUMNS = [
  { key: "asset_name", label: "Asset Name", required: true },
  { key: "asset_alias", label: "Asset Alias" },
  { key: "address", label: "Address" },
  { key: "destination_code", label: "Routing Destination", required: true },
  { key: "comment", label: "Comment" },
];

const EMPTY_OWNERSHIP_FORM = {
  ownership: "",
  destination: "",
};

const EMPTY_ASSET_CUSTOM_FORM = {
  asset_name: "",
  asset_alias: "",
  address: "",
  destination_code: "",
  comment: "",
};

function Management() {
  const [isProcessOn, setIsProcessOn] = useState(true);
  const [ownershipRows, setOwnershipRows] = useState([]);
  const [assetCustomRows, setAssetCustomRows] = useState([]);
  const [destinations, setDestinations] = useState([]);
  const [assetLookupRows, setAssetLookupRows] = useState([]);
  const [ownershipLoading, setOwnershipLoading] = useState(true);
  const [ownershipError, setOwnershipError] = useState("");
  const [destinationLoading, setDestinationLoading] = useState(true);
  const [destinationError, setDestinationError] = useState("");
  const [assetCustomLoading, setAssetCustomLoading] = useState(true);
  const [assetCustomError, setAssetCustomError] = useState("");
  const [assetLookupLoading, setAssetLookupLoading] = useState(true);
  const [assetLookupError, setAssetLookupError] = useState("");
  const [ownershipMessage, setOwnershipMessage] = useState("");
  const [search, setSearch] = useState("");
  const [ownershipForm, setOwnershipForm] = useState(EMPTY_OWNERSHIP_FORM);
  const [selectedOwnership, setSelectedOwnership] = useState("__new__");
  const [assetCustomEditingId, setAssetCustomEditingId] = useState("");
  const [assetCustomForm, setAssetCustomForm] = useState(EMPTY_ASSET_CUSTOM_FORM);
  const [assetCustomMessage, setAssetCustomMessage] = useState("");

  async function loadOwnership() {
    setOwnershipLoading(true);
    setOwnershipError("");
    try {
      const res = await fetch(`${API}/api/workflow/ownership`);
      setOwnershipRows(await readJsonResponse(res, "Failed to load ownership."));
    } catch (err) {
      setOwnershipError(err.message);
    } finally {
      setOwnershipLoading(false);
    }
  }

  async function loadDestinations() {
    setDestinationLoading(true);
    setDestinationError("");
    try {
      const res = await fetch(`${API}/api/workflow/destinations`);
      setDestinations(await readJsonResponse(res, "Failed to load destinations."));
    } catch (err) {
      setDestinationError(err.message);
    } finally {
      setDestinationLoading(false);
    }
  }

  async function loadAssetCustom() {
    setAssetCustomLoading(true);
    setAssetCustomError("");
    try {
      const res = await fetch(`${API}/api/workflow/asset-custom`);
      setAssetCustomRows(await readJsonResponse(res, "Failed to load custom assets."));
    } catch (err) {
      setAssetCustomError(err.message);
    } finally {
      setAssetCustomLoading(false);
    }
  }

  async function loadAssetLookup() {
    setAssetLookupLoading(true);
    setAssetLookupError("");
    try {
      const res = await fetch(`${API}/api/workflow/asset-lookup`);
      setAssetLookupRows(await readJsonResponse(res, "Failed to load asset lookup."));
    } catch (err) {
      setAssetLookupError(err.message);
    } finally {
      setAssetLookupLoading(false);
    }
  }

  useEffect(() => {
    loadOwnership();
    loadDestinations();
    loadAssetCustom();
    loadAssetLookup();
  }, []);

  const distinctDestinations = useMemo(() => {
    const seen = new Set();
    return destinations
      .filter((row) => {
        const code = row.destination_code || "";
        if (!code || seen.has(code)) return false;
        seen.add(code);
        return true;
      })
      .sort((a, b) => String(a.destination_code).localeCompare(String(b.destination_code)));
  }, [destinations]);

  const ownershipOptions = useMemo(() => {
    return [...ownershipRows].sort((a, b) => String(a.ownership || "").localeCompare(String(b.ownership || "")));
  }, [ownershipRows]);

  function onOwnershipFormChange(field, value) {
    setOwnershipForm((current) => ({ ...current, [field]: value }));
  }

  function selectOwnership(value) {
    setSelectedOwnership(value);
    setOwnershipMessage("");
    if (value === "__new__") {
      setOwnershipForm(EMPTY_OWNERSHIP_FORM);
      return;
    }
    const row = ownershipRows.find((item) => item.ownership === value);
    if (!row) return;
    setOwnershipForm({
      ownership: row.ownership || "",
      destination: row.destination || "",
    });
  }

  async function onOwnershipSubmit(event) {
    event.preventDefault();
    const isNew = selectedOwnership === "__new__";
    const record = {
      ownership: ownershipForm.ownership.trim(),
      destination: ownershipForm.destination.trim(),
    };
    if (!record.ownership || !record.destination) return;
    try {
      setOwnershipError("");
      setOwnershipMessage("");
      const path = isNew
        ? "/api/workflow/ownership"
        : `/api/workflow/ownership/${encodeURIComponent(record.ownership)}`;
      const res = await fetch(`${API}${path}`, {
        method: isNew ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ destination: record.destination }),
      });
      await readJsonResponse(res, "Failed to save ownership.");
      await loadOwnership();
      if (isNew) {
        setOwnershipForm(EMPTY_OWNERSHIP_FORM);
        setSelectedOwnership("__new__");
      }
      setOwnershipMessage(isNew ? "Ownership record added." : "Ownership destination updated.");
    } catch (err) {
      setOwnershipError(err.message);
    }
  }

  function startAssetCustomAdd() {
    setAssetCustomEditingId("__new__");
    setAssetCustomForm(EMPTY_ASSET_CUSTOM_FORM);
    setAssetCustomMessage("");
  }

  function startAssetCustomEdit(row) {
    setAssetCustomEditingId(row.asset_custom_id);
    setAssetCustomForm({
      asset_name: row.asset_name || "",
      asset_alias: row.asset_alias || "",
      address: row.address || "",
      destination_code: row.destination_code || "",
      comment: row.comment || "",
    });
    setAssetCustomMessage("");
  }

  function cancelAssetCustomEdit() {
    setAssetCustomEditingId("");
    setAssetCustomForm(EMPTY_ASSET_CUSTOM_FORM);
  }

  function onAssetCustomFormChange(field, value) {
    setAssetCustomForm((current) => ({ ...current, [field]: value }));
  }

  async function saveAssetCustom() {
    const record = {
      asset_name: assetCustomForm.asset_name.trim(),
      asset_alias: assetCustomForm.asset_alias.trim(),
      address: assetCustomForm.address.trim(),
      destination_code: assetCustomForm.destination_code.trim(),
      comment: assetCustomForm.comment.trim(),
    };
    if (!record.asset_name || !record.destination_code) return;
    const isNew = assetCustomEditingId === "__new__";
    const path = isNew
      ? "/api/workflow/asset-custom"
      : `/api/workflow/asset-custom/${encodeURIComponent(assetCustomEditingId)}`;
    try {
      setAssetCustomError("");
      setAssetCustomMessage("");
      const res = await fetch(`${API}${path}`, {
        method: isNew ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(record),
      });
      await readJsonResponse(res, "Failed to save custom asset.");
      cancelAssetCustomEdit();
      await Promise.all([loadAssetCustom(), loadAssetLookup()]);
      setAssetCustomMessage(isNew ? "Custom asset added." : "Custom asset updated.");
    } catch (err) {
      setAssetCustomError(err.message);
    }
  }

  async function deleteAssetCustom(row) {
    if (!window.confirm(`Delete custom asset "${row.asset_name}"?`)) return;
    try {
      setAssetCustomError("");
      setAssetCustomMessage("");
      const res = await fetch(`${API}/api/workflow/asset-custom/${encodeURIComponent(row.asset_custom_id)}`, {
        method: "DELETE",
      });
      await readJsonResponse(res, "Failed to delete custom asset.");
      await Promise.all([loadAssetCustom(), loadAssetLookup()]);
      setAssetCustomMessage("Custom asset deleted.");
    } catch (err) {
      setAssetCustomError(err.message);
    }
  }

  function renderAssetCustomCell(column, row) {
    const editing = assetCustomEditingId === row.asset_custom_id;
    if (!editing) return row[column.key] || "-";
    if (column.key === "destination_code") {
      return (
        <select
          required
          value={assetCustomForm.destination_code}
          onChange={(event) => onAssetCustomFormChange("destination_code", event.target.value)}
          disabled={destinationLoading || Boolean(destinationError)}
        >
          <option value="">Select destination</option>
          {distinctDestinations.map((destination) => (
            <option key={destination.destination_code} value={destination.destination_code}>{destination.destination_code}</option>
          ))}
        </select>
      );
    }
    return (
      <input
        required={column.required}
        value={assetCustomForm[column.key]}
        onChange={(event) => onAssetCustomFormChange(column.key, event.target.value)}
      />
    );
  }

  const filteredAssets = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!normalized) return assetLookupRows;
    return assetLookupRows.filter((row) =>
      Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(normalized)),
    );
  }, [assetLookupRows, search]);

  return (
    <main className="workspace">
      <section className="panel processTogglePanel">
        <div className="processToggleHeader">
          <div>
            <h1>Process control</h1>
            <p>UI-only process switch for local management preview.</p>
          </div>
          <div className="processSwitch" role="group" aria-label="Process on off toggle">
            <button className={isProcessOn ? "active on" : ""} onClick={() => setIsProcessOn(true)} type="button">On</button>
            <button className={!isProcessOn ? "active off" : ""} onClick={() => setIsProcessOn(false)} type="button">Off</button>
          </div>
        </div>
        <Badge label={isProcessOn ? "ON" : "OFF"} />
      </section>

      <section className="panel propertyEditor">
        <PanelTitle title="Manage Ownership" icon={Plus} />
        {ownershipError && <Banner tone="bad" text={ownershipError} />}
        {destinationError && <Banner tone="bad" text={destinationError} />}
        {ownershipMessage && <Banner text={ownershipMessage} />}
        <form className="propertyForm" onSubmit={onOwnershipSubmit}>
          <div className="propertyFormGrid">
            {OWNERSHIP_COLUMNS.map((column) => (
              <label key={column.key}>
                {column.label}
                {column.key === "ownership" ? (
                  <>
                    <select value={selectedOwnership} onChange={(event) => selectOwnership(event.target.value)}>
                      <option value="__new__">+ New</option>
                      {ownershipOptions.map((row) => (
                        <option key={row.ownership} value={row.ownership}>{row.ownership}</option>
                      ))}
                    </select>
                    <input
                      required
                      value={ownershipForm.ownership}
                      onChange={(event) => onOwnershipFormChange("ownership", event.target.value)}
                      placeholder="New ownership"
                      disabled={selectedOwnership !== "__new__"}
                    />
                  </>
                ) : column.key === "destination" ? (
                  <select
                    required
                    value={ownershipForm.destination}
                    onChange={(event) => onOwnershipFormChange("destination", event.target.value)}
                    disabled={destinationLoading || Boolean(destinationError)}
                  >
                    <option value="">Select destination</option>
                    {distinctDestinations.map((row) => (
                      <option key={row.destination_code} value={row.destination_code}>{row.destination_code}</option>
                    ))}
                  </select>
                ) : (
                  <input required value={ownershipForm[column.key]} onChange={(event) => onOwnershipFormChange(column.key, event.target.value)} />
                )}
              </label>
            ))}
          </div>
          <div className="propertyFormActions">
            <button type="submit" className="primaryAction" disabled={destinationLoading || Boolean(destinationError)}>
              <Plus size={16} />
              {selectedOwnership === "__new__" ? "Add Ownership" : "Save Destination"}
            </button>
          </div>
        </form>

        <div className="propertyTableWrap">
          <table className="propertyTable ownershipTable">
            <thead>
              <tr>
                {OWNERSHIP_COLUMNS.map((column) => (
                  <th key={column.key}>{column.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ownershipLoading && (
                <tr><td colSpan={OWNERSHIP_COLUMNS.length}><Empty text="Loading ownership..." /></td></tr>
              )}
              {!ownershipLoading && ownershipRows.length === 0 && (
                <tr><td colSpan={OWNERSHIP_COLUMNS.length}><Empty text="No ownership records found." /></td></tr>
              )}
              {ownershipRows.map((row) => (
                <tr key={row.ownership}>
                  {OWNERSHIP_COLUMNS.map((column) => (
                    <td key={column.key}>{row[column.key] || "-"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel propertyTablePanel">
        <div className="propertyTableHeader">
          <PanelTitle title="Asset Custom" icon={Plus} />
          <button type="button" className="primaryAction compactAction" onClick={startAssetCustomAdd} disabled={assetCustomEditingId === "__new__"}>
            <Plus size={16} />
            Add
          </button>
        </div>
        {assetCustomError && <Banner tone="bad" text={assetCustomError} />}
        {destinationError && <Banner tone="bad" text={destinationError} />}
        {assetCustomMessage && <Banner text={assetCustomMessage} />}
        <div className="propertyTableWrap">
          <table className="propertyTable assetCustomTable">
            <thead>
              <tr>
                {ASSET_CUSTOM_COLUMNS.map((column) => (
                  <th key={column.key}>{column.label}</th>
                ))}
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {assetCustomLoading && (
                <tr><td colSpan={ASSET_CUSTOM_COLUMNS.length + 1}><Empty text="Loading custom assets..." /></td></tr>
              )}
              {!assetCustomLoading && assetCustomEditingId !== "__new__" && assetCustomRows.length === 0 && (
                <tr><td colSpan={ASSET_CUSTOM_COLUMNS.length + 1}><Empty text="No custom assets found." /></td></tr>
              )}
              {assetCustomEditingId === "__new__" && (
                <tr>
                  {ASSET_CUSTOM_COLUMNS.map((column) => (
                    <td key={column.key}>{renderAssetCustomCell(column, { asset_custom_id: "__new__" })}</td>
                  ))}
                  <td>
                    <div className="rowActions">
                      <button type="button" onClick={saveAssetCustom} title="Save" disabled={destinationLoading || Boolean(destinationError)}><Save size={15} />Save</button>
                      <button type="button" onClick={cancelAssetCustomEdit} title="Cancel"><X size={15} />Cancel</button>
                    </div>
                  </td>
                </tr>
              )}
              {assetCustomRows.map((row) => {
                const editing = assetCustomEditingId === row.asset_custom_id;
                return (
                  <tr key={row.asset_custom_id}>
                    {ASSET_CUSTOM_COLUMNS.map((column) => (
                      <td key={column.key}>{renderAssetCustomCell(column, row)}</td>
                    ))}
                    <td>
                      <div className="rowActions">
                        {editing ? (
                          <>
                            <button type="button" onClick={saveAssetCustom} title="Save" disabled={destinationLoading || Boolean(destinationError)}><Save size={15} />Save</button>
                            <button type="button" onClick={cancelAssetCustomEdit} title="Cancel"><X size={15} />Cancel</button>
                          </>
                        ) : (
                          <>
                            <button type="button" onClick={() => startAssetCustomEdit(row)} title="Edit"><Pencil size={15} />Edit</button>
                            <button type="button" className="danger" onClick={() => deleteAssetCustom(row)} title="Delete"><Trash2 size={15} />Delete</button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel propertyTablePanel">
        <div className="propertyTableHeader">
          <PanelTitle title="Asset Lookup" />
          <div className="search">
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search assets" />
          </div>
        </div>

        {assetLookupError && <Banner tone="bad" text={assetLookupError} />}
        <div className="propertyTableWrap">
          <table className="propertyTable">
            <thead>
              <tr>
                {ASSET_LOOKUP_COLUMNS.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {assetLookupLoading && (
                <tr><td colSpan={ASSET_LOOKUP_COLUMNS.length}><Empty text="Loading asset lookup..." /></td></tr>
              )}
              {!assetLookupLoading && filteredAssets.length === 0 && (
                <tr><td colSpan={ASSET_LOOKUP_COLUMNS.length}><Empty text="No assets found." /></td></tr>
              )}
              {filteredAssets.map((row, index) => (
                <tr key={`${row["Asset Alias"] || row["Asset Name"] || "asset"}-${index}`}>
                  {ASSET_LOOKUP_COLUMNS.map((column) => (
                    <td key={column}>{typeof row[column] === "boolean" ? (row[column] ? "Yes" : "No") : row[column] || "-"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
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
