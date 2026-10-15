import { useCallback, useEffect, useRef, useState } from "react";
import { adminFetch, subscribeAdminEvents } from "./adminApi.js";
import { describeRiskSignal } from "./riskSignals.js";
import "./LiveDashboard.css";

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return isNaN(date) ? value : date.toLocaleString();
}

function currency(value) {
  return `GHS ${Number(value ?? 0).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}`;
}

const RISK_COLORS = { HIGH: "#dc2626", MEDIUM: "#d97706", LOW: "#16a34a" };

function DonutChart({ distribution }) {
  const keys = ["LOW", "MEDIUM", "HIGH"];
  const total = keys.reduce((sum, key) => sum + (distribution[key] || 0), 0);
  if (!total) {
    return (
      <div className="donut-placeholder">
        <strong>—</strong>
        <span>No assessments yet</span>
      </div>
    );
  }

  let acc = 0;
  const stops = keys.flatMap((key) => {
    const start = (acc / total) * 100;
    acc += distribution[key] || 0;
    const end = (acc / total) * 100;
    return [
      { color: RISK_COLORS[key], stop: `${start}%` },
      { color: RISK_COLORS[key], stop: `${Math.min(100, end)}%` },
    ];
  });

  const gradient = stops.map((s) => `${s.color} ${s.stop}`).join(", ");

  return (
    <div className="donut-wrap">
      <div
        className="donut"
        style={{ background: `conic-gradient(${gradient})` }}
      >
        <div className="donut-hole">
          <strong>{total}</strong>
          <span>assessments</span>
        </div>
      </div>
      <ul className="donut-legend">
        {keys.map((key) => (
          <li key={key}>
            <span className="dot" style={{ background: RISK_COLORS[key] }} />
            {key} — {distribution[key] || 0}
          </li>
        ))}
      </ul>
    </div>
  );
}

function HBarChart({ data, color = "#00678f" }) {
  if (!data.length) return <p className="chart-empty">No data yet.</p>;
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div className="hbar-list">
      {data.map((item) => (
        <div className="hbar-row" key={item.label}>
          <span className="hbar-label" title={item.label}>
            {item.label}
          </span>
          <div className="hbar-track">
            <div
              className="hbar-fill"
              style={{ width: `${(item.value / max) * 100}%`, background: color }}
            />
          </div>
          <span className="hbar-value">{item.value}</span>
        </div>
      ))}
    </div>
  );
}

function SignalChart({ data = [] }) {
  if (!data.length) return <p className="chart-empty">No rule-engine signals yet.</p>;
  const max = Math.max(1, ...data.map((item) => item.count));

  return (
    <div className="signal-chart">
      <p className="signal-chart-note">
        Occurrences across risk assessments. Multiple signals can appear on one transaction.
      </p>
      {data.map((item, index) => (
        <article className="signal-row" key={item.reason}>
          <span className="signal-rank" aria-hidden="true">
            {String(index + 1).padStart(2, "0")}
          </span>
          <div className="signal-content">
            <div className="signal-heading">
              <strong title={item.reason}>{item.reason}</strong>
              <span>{item.count}</span>
            </div>
            <p>{describeRiskSignal(item.reason)}</p>
            <div
              className="signal-track"
              role="img"
              aria-label={`${item.reason}: ${item.count} occurrences`}
            >
              <span style={{ width: `${(item.count / max) * 100}%` }} />
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function TrendChart({ data }) {
  const chartContainerRef = useRef(null);
  const [chartWidth, setChartWidth] = useState(640);
  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container) return;

    const observer = new ResizeObserver(([entry]) => {
      setChartWidth(Math.max(320, Math.round(entry.contentRect.width)));
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  if (!data.length) {
    return (
      <div className="trend-chart-wrap" ref={chartContainerRef}>
        <p className="chart-empty">No transaction trend yet.</p>
      </div>
    );
  }
  const values = data.map((d) => d.count);
  const max = Math.max(1, ...values);
  const width = chartWidth;
  const height = 340;
  const plot = { left: 58, right: 22, top: 24, bottom: 68 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const tickStep = Math.max(1, Math.ceil(max / 4));
  const yMax = Math.max(tickStep, Math.ceil(max / tickStep) * tickStep);
  const yTicks = [];
  for (let value = 0; value <= yMax; value += tickStep) yTicks.push(value);
  const xTickInterval = Math.ceil(data.length / 7);
  const points = data.map((d, index) => {
    const x =
      data.length === 1
        ? plot.left + plotWidth / 2
        : plot.left + (index / (data.length - 1)) * plotWidth;
    const y = plot.top + plotHeight - (d.count / yMax) * plotHeight;
    return [x, y];
  });

  const line = points.map((p) => p.join(",")).join(" ");
  const baseline = plot.top + plotHeight;
  const area = `M ${points[0][0]} ${baseline} L ${line} L ${
    points[points.length - 1][0]
  } ${baseline} Z`;

  return (
    <div className="trend-chart-wrap" ref={chartContainerRef}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="trend-svg"
        role="img"
        aria-label={`Daily transaction volume chart, ${data.length} days`}
      >
        <defs>
          <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#0086a8" stopOpacity="0.24" />
            <stop offset="100%" stopColor="#0086a8" stopOpacity="0.015" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#trendFill)" />
        {yTicks.map((value) => {
          const y = plot.top + plotHeight - (value / yMax) * plotHeight;
          return (
            <g key={value}>
              <line
                x1={plot.left}
                x2={width - plot.right}
                y1={y}
                y2={y}
                className="trend-gridline"
              />
              <text x={plot.left - 10} y={y + 3} textAnchor="end" className="trend-axis-tick">
                {value}
              </text>
            </g>
          );
        })}
        {points.map(([x], index) =>
          index % xTickInterval === 0 || index === points.length - 1 ? (
            <line
              key={`x-grid-${data[index].day || index}`}
              x1={x}
              x2={x}
              y1={plot.top}
              y2={baseline}
              className="trend-gridline trend-gridline-vertical"
            />
          ) : null
        )}
        <line
          x1={plot.left}
          x2={plot.left}
          y1={plot.top}
          y2={baseline}
          className="trend-axis-line"
        />
        <line
          x1={plot.left}
          x2={width - plot.right}
          y1={baseline}
          y2={baseline}
          className="trend-axis-line"
        />
        <polyline
          points={line}
          fill="none"
          stroke="#087994"
          strokeWidth="3"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {data.map((d, index) => (
          <g key={d.day || index}>
            <circle cx={points[index][0]} cy={points[index][1]} r="4" fill="#087994">
              <title>{`${d.day}: ${d.count} transactions`}</title>
            </circle>
            {(index % xTickInterval === 0 || index === data.length - 1) && (
              <text
                x={points[index][0]}
                y={baseline + 17}
                textAnchor="middle"
                className="trend-axis-tick"
              >
                {(d.day || "").slice(5)}
              </text>
            )}
          </g>
        ))}
        <text
          x={(plot.left + width - plot.right) / 2}
          y={height - 5}
          textAnchor="middle"
          className="trend-axis-title"
        >
          Date
        </text>
        <text
          x="14"
          y={plot.top + plotHeight / 2}
          textAnchor="middle"
          transform={`rotate(-90 14 ${plot.top + plotHeight / 2})`}
          className="trend-axis-title"
        >
          Transactions
        </text>
      </svg>
    </div>
  );
}

export default function LiveDashboard() {
  const [analytics, setAnalytics] = useState(null);
  const [error, setError] = useState("");
  const [live, setLive] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const refreshTimer = useRef(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await adminFetch("/api/admin/analytics");
      if (!response.ok) throw new Error("Failed to load analytics.");
      const data = await response.json();
      setAnalytics(data);
      setError("");
    } catch (err) {
      setError(err.message || "Could not load dashboard data.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();

    const REFRESH_EVENTS = new Set(["stats", "transaction", "alert", "case"]);
    const unsubscribe = subscribeAdminEvents(
      (payload) => {
        if (REFRESH_EVENTS.has(payload.event)) {
          clearTimeout(refreshTimer.current);
          refreshTimer.current = setTimeout(load, 250);
        }
      },
      setLive
    );

    return () => {
      clearTimeout(refreshTimer.current);
      unsubscribe();
    };
  }, [load]);

  if (!analytics) {
    return (
      <section className="live-dashboard">
        <p className="dashboard-loading">
          {error ? `⚠️ ${error}` : "Loading live dashboard…"}
        </p>
      </section>
    );
  }

  const totals = analytics.totals || {};
  const recent = analytics.recent_transactions || [];

  const kpiCards = [
    {
      label: "Total Transactions",
      value: Number(totals.transactions || 0).toLocaleString(),
      tone: "blue",
    },
    {
      label: "High-Risk",
      value: Number(analytics.risk_distribution?.HIGH || 0),
      tone: "red",
    },
    {
      label: "Fraud Alerts",
      value: Number(totals.alerts || 0),
      tone: "amber",
    },
    {
      label: "Active Cases",
      value: Number(totals.open_cases || 0),
      tone: "violet",
    },
    {
      label: "Resolved Cases",
      value: Number(totals.resolved_cases || 0),
      tone: "green",
    },
    {
      label: "Blocked Transactions",
      value: Number(totals.blocked || 0),
      tone: "slate",
    },
    {
      label: "Admin-Frozen",
      value: Number(totals.admin_frozen_accounts || 0),
      tone: "rose",
    },
    {
      label: "Customer-Frozen",
      value: Number(totals.customer_frozen_accounts || 0),
      tone: "rose",
    },
    {
      label: "Avg Risk Score",
      value: Number(totals.avg_risk_score || 0).toFixed(3),
      tone: "green",
    },
  ];

  return (
    <section className="live-dashboard">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">OPERATIONS / FRAUD MONITORING</p>
          <h1>Fraud monitoring</h1>
          <p className="subtitle">
            A live view of transaction activity, risk signals, and cases that
            need a closer look.
          </p>
        </div>
        <div className="dashboard-header-actions">
          <div className={`live-badge ${live ? "on" : ""}`}>
            <span className="live-dot" />
            {live ? "LIVE" : "OFFLINE"}
          </div>
          <button
            type="button"
            className="analytics-refresh-button"
            onClick={load}
            disabled={refreshing}
          >
            {refreshing ? "Refreshing..." : "Refresh metrics"}
          </button>
        </div>
      </header>

      <div className="kpi-grid">
        {kpiCards.map((card) => (
          <article className={`kpi-card ${card.tone}`} key={card.label}>
            <p>{card.label}</p>
            <strong>{card.value}</strong>
          </article>
        ))}
      </div>

      {error && <p className="dashboard-error">{error}</p>}

      <div className="charts-grid">
        <article className="chart-card">
          <h2>Risk distribution</h2>
          <DonutChart distribution={analytics.risk_distribution || {}} />
        </article>

        <article className="chart-card">
          <h2>Transactions by type</h2>
          <HBarChart
            data={Object.entries(analytics.type_distribution || {}).map(
              ([label, value]) => ({ label, value })
            )}
            color="#00678f"
          />
        </article>

        <article className="chart-card span-2">
          <h2>Daily transaction volume</h2>
          <p className="chart-description">Count of recorded transactions by day</p>
          <TrendChart data={analytics.daily_trend || []} />
        </article>

        <article className="chart-card span-2 signal-summary-card">
          <h2>Most common risk signals</h2>
          <SignalChart data={analytics.top_reasons || []} />
        </article>
      </div>
      <div className="recent-panel">
        <div className="panel-heading">
          <h2>Recent transactions</h2>
          <p>Live feed — includes IP &amp; device used for each transaction.</p>
        </div>

        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Reference</th>
                <th>Type</th>
                <th>Amount</th>
                <th>Risk</th>
                <th>Status</th>
                <th>IP address</th>
                <th>Device</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 ? (
                <tr>
                  <td colSpan="8" className="empty-cell">No transactions yet.</td>
                </tr>
              ) : (
                recent.map((row) => (
                  <tr key={row.id}>
                    <td className="mono">{formatTime(row.created_at)}</td>
                    <td className="mono">{row.transaction_reference}</td>
                    <td>{row.transaction_type}</td>
                    <td>{currency(row.amount)}</td>
                    <td>
                      <span className={`badge badge-${(row.risk_level || "low").toLowerCase()}`}>
                        {row.risk_level || "—"}
                        {row.risk_score != null ? ` · ${Number(row.risk_score).toFixed(2)}` : ""}
                      </span>
                    </td>
                    <td>
                      <span className={`status-pill ${(row.status || "SCORED").toLowerCase()}`}>
                        {row.status}
                      </span>
                    </td>
                    <td className="mono">{row.ip_address || "—"}</td>
                    <td className="mono">{row.device_id || "—"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}