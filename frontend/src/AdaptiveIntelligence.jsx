import { useCallback, useEffect, useRef, useState } from "react";
import { adminFetch, subscribeAdminEvents } from "./adminApi.js";
import "./AdaptiveIntelligence.css";

function formatUpdated(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function DriftBars({ drift }) {
  const max = Math.max(0.01, ...drift.map((item) => item.psi ?? 0));
  return (
    <div className="afi-bars">
      {drift.map((item) => (
        <div className="afi-bar-row" key={item.feature}>
          <span className="afi-bar-label">{item.feature}</span>
          <div className="afi-bar-track">
            <div
              className={`afi-bar-fill ${item.status}`}
              style={{ width: `${((item.psi ?? 0) / max) * 100}%` }}
            />
          </div>
          <span className={`afi-status ${item.status}`}>
            {item.status.replace("_", " ")}
          </span>
          <span className="afi-psi">
            {item.psi == null ? "—" : item.psi.toFixed(4)}
          </span>
        </div>
      ))}
    </div>
  );
}

function TrendSpark({ data, stroke = "#00678f", label }) {
  const chartRef = useRef(null);
  const [chartWidth, setChartWidth] = useState(640);
  useEffect(() => {
    const container = chartRef.current;
    if (!container) return undefined;

    const observer = new ResizeObserver(([entry]) => {
      setChartWidth(Math.max(320, Math.round(entry.contentRect.width)));
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  if (!data?.length) return <p className="afi-empty">No data yet.</p>;

  const width = chartWidth;
  const height = 240;
  const plot = { left: 64, right: 18, top: 18, bottom: 54 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const xTickInterval = Math.max(1, Math.ceil((data.length - 1) / 6));
  const yTicks = [0, 0.25, 0.5, 0.75, 1];

  const points = data.map((d, index) => {
    const value = Math.min(1, Math.max(0, d.value ?? d.rate ?? 0));
    const x =
      data.length === 1
        ? plot.left + plotWidth / 2
        : plot.left + (index / (data.length - 1)) * plotWidth;
    const y = plot.top + plotHeight - value * plotHeight;
    return [x, y];
  });

  const line = points.map((p) => p.join(",")).join(" ");
  const baseline = plot.top + plotHeight;
  const area = `M ${points[0][0]} ${baseline} L ${line} L ${
    points[points.length - 1][0]
  } ${baseline} Z`;
  const gradientId = `afi-gradient-${label.toLowerCase().replaceAll(" ", "-")}`;

  return (
    <div className="afi-trend-chart" ref={chartRef}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="trend-svg afi-trend-svg"
        role="img"
        aria-label={`${label} over time with a 0 to 1 vertical scale`}
      >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0.025" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradientId})`} />
      {yTicks.map((value) => {
        const y = plot.top + plotHeight - value * plotHeight;
        return (
          <g key={value}>
            <line
              x1={plot.left}
              x2={width - plot.right}
              y1={y}
              y2={y}
              className="afi-gridline"
            />
            <text
              x={plot.left - 10}
              y={y + 4}
              textAnchor="end"
              className="afi-axis-tick"
            >
              {value.toFixed(2)}
            </text>
          </g>
        );
      })}
      {points.map(([x], index) =>
        index % xTickInterval === 0 || index === points.length - 1 ? (
          <g key={`date-grid-${data[index]?.day || index}`}>
            <line
              x1={x}
              x2={x}
              y1={plot.top}
              y2={baseline}
              className="afi-gridline afi-gridline-vertical"
            />
            <text
              x={x}
              y={baseline + 17}
              textAnchor="middle"
              className="afi-axis-tick"
            >
              {(data[index]?.day || "").slice(5)}
            </text>
          </g>
        ) : null
      )}
      <line
        x1={plot.left}
        x2={plot.left}
        y1={plot.top}
        y2={baseline}
        className="afi-axis-line"
      />
      <line
        x1={plot.left}
        x2={width - plot.right}
        y1={baseline}
        y2={baseline}
        className="afi-axis-line"
      />
      {points.map(([x, y], index) => (
        <g key={data[index]?.day || index}>
          <circle cx={x} cy={y} r="3.5" fill={stroke}>
            <title>
              {`${data[index]?.day || "Unknown date"}: ${(
                data[index]?.value ??
                data[index]?.rate ??
                0
              ).toFixed(3)}`}
            </title>
          </circle>
        </g>
      ))}
      <polyline
        points={line}
        fill="none"
        stroke={stroke}
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <text
        x={plot.left + plotWidth / 2}
        y={height - 8}
        textAnchor="middle"
        className="afi-axis-title"
      >
        Date
      </text>
      <text
        x="14"
        y={plot.top + plotHeight / 2}
        textAnchor="middle"
        transform={`rotate(-90 14 ${plot.top + plotHeight / 2})`}
        className="afi-axis-title"
      >
        Rate / score (0-1)
      </text>
      </svg>
    </div>
  );
}

function simulateThresholdMetrics(threshold) {
  const baseT = 0.5435;
  const d = threshold - baseT;

  // Recall drops smoothly as threshold rises (PaySim calibrated holdout curve)
  const recallRaw = 76.4 - d * 215 - (d > 0 ? d * d * 300 : -d * d * 220);
  const recall = Math.min(96.5, Math.max(38.0, recallRaw));

  // False positive rate drops exponentially as threshold rises
  const fprRaw = 2.14 * Math.exp(-15.8 * d);
  const fpr = Math.min(11.8, Math.max(0.18, fprRaw));

  // Mobile money natural fraud prevalence is ~0.25% (2.5 per 1,000 txns)
  const prev = 0.0025;
  const tp = (recall / 100) * prev;
  const fp = (fpr / 100) * (1 - prev);
  const precision = (tp / (tp + fp)) * 100;

  // Friction challenges (SMS OTP / Biometrics) per 1,000 transactions
  const friction = Math.round(fpr * 10 + 2.5);

  const deltaRecall = recall - 76.4;
  const deltaFpr = fpr - 2.14;
  const deltaFriction = friction - 24;

  return {
    threshold: Number(threshold.toFixed(4)),
    recall: Number(recall.toFixed(1)),
    fpr: Number(fpr.toFixed(2)),
    precision: Number(precision.toFixed(1)),
    friction,
    deltaRecall: Number(deltaRecall.toFixed(1)),
    deltaFpr: Number(deltaFpr.toFixed(2)),
    deltaFriction,
  };
}

function AutomatedTriggerCard({
  trigger,
  onRetrain,
  retraining,
  autoRetrainEnabled,
  setAutoRetrainEnabled,
  canManageModel,
}) {
  const newTx = trigger?.new_transactions_since_retrain ?? 0;
  const volLimit = trigger?.volume_threshold ?? 50;
  const volPct = Math.min(100, Math.round((newTx / volLimit) * 100));
  const isBreached = Boolean(trigger?.action_required);

  return (
    <article className={`afi-card afi-trigger-card ${isBreached ? "breached" : "stable"}`}>
      <div className="afi-trigger-header">
        <div>
          <div className="afi-trigger-badge-row">
            <span className={`afi-trigger-pill ${isBreached ? "breach-pill" : "stable-pill"}`}>
              {isBreached ? "RECALIBRATION REQUIRED" : "ACTIVE MONITORING · WITHIN LIMITS"}
            </span>
            <span className="afi-trigger-policy">BoG / ISO Drift Governance</span>
          </div>
          <h3>Automated Drift & Volume Thresholds</h3>
          <p className="afi-note">
            A critical PSI shift (≥ 0.25) or 50 new transactions records a prototype baseline marker. This updates version tracking only; it does not refit the detector or reset the PSI baseline.
          </p>
        </div>
        {canManageModel ? (
          <div className="afi-trigger-actions">
            <label className="afi-toggle-label" title="Automatically record a prototype version marker whenever a threshold is breached">
              <input
                type="checkbox"
                checked={autoRetrainEnabled}
                onChange={(e) => setAutoRetrainEnabled(e.target.checked)}
              />
              <span className="afi-toggle-switch" />
              <span className="afi-toggle-text">Auto-record marker on breach</span>
            </label>
            <button
              type="button"
              className={`afi-trigger-btn ${isBreached ? "breach-btn" : ""}`}
              onClick={() => onRetrain({ triggered_by: "AUTOMATED_THRESHOLD_RULE" })}
              disabled={retraining}
            >
              {retraining ? "Recording Marker…" : isBreached ? "Record Triggered Baseline Marker" : "Record Baseline Marker"}
            </button>
          </div>
        ) : (
          <p className="afi-note">Recording a baseline marker is restricted to administrators.</p>
        )}
      </div>

      <div className="afi-trigger-metrics">
        <div className="afi-trigger-kpi">
          <div className="afi-trigger-kpi-top">
            <span>Transaction Volume Progress</span>
            <strong>{newTx} / {volLimit} ({volPct}%)</strong>
          </div>
          <div className="afi-progress-track">
            <div
              className={`afi-progress-fill ${newTx >= volLimit ? "fill-breach" : "fill-normal"}`}
              style={{ width: `${volPct}%` }}
            />
          </div>
          <span className="afi-trigger-desc">
            {newTx >= volLimit
              ? "Volume threshold reached. A prototype baseline marker is recommended."
              : `${volLimit - newTx} more transactions until the next version marker.`}
          </span>
        </div>

        <div className="afi-trigger-kpi">
          <div className="afi-trigger-kpi-top">
            <span>Population Stability Drift (PSI)</span>
            <strong className={trigger?.psi_critical_breach ? "text-danger" : "text-success"}>
              {trigger?.psi_critical_breach ? "CRITICAL BREACH (≥ 0.25)" : "STABLE (< 0.25)"}
            </strong>
          </div>
          <div className="afi-trigger-stat-row">
            <span>Critical Trigger Limit: <strong>0.2500 PSI</strong></span>
            <span>Warning Limit: <strong>0.1000 PSI</strong></span>
          </div>
          <span className="afi-trigger-desc">
            {trigger?.psi_critical_breach
              ? "Critical shift detected in the monitored transaction amount or risk-score distribution."
              : "Feature distributions are within acceptable statistical boundaries."}
          </span>
        </div>
      </div>

      {isBreached && trigger?.reasons?.length > 0 && (
        <div className="afi-trigger-reasons-box">
          <strong>Active Retraining Triggers:</strong>
          <ul>
            {trigger.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

function ThresholdSensitivityVisualizer() {
  const [threshold, setThreshold] = useState(0.5435);
  const containerRef = useRef(null);
  const [svgWidth, setSvgWidth] = useState(680);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      setSvgWidth(Math.max(340, Math.round(entry.contentRect.width)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const currentMetrics = simulateThresholdMetrics(threshold);

  const height = 240;
  const plot = { left: 54, right: 54, top: 22, bottom: 44 };
  const plotWidth = Math.max(100, svgWidth - plot.left - plot.right);
  const plotHeight = height - plot.top - plot.bottom;

  const samplePoints = [];
  for (let t = 0.45; t <= 0.6501; t += 0.005) {
    samplePoints.push(simulateThresholdMetrics(t));
  }

  const getX = (t) => plot.left + ((t - 0.45) / 0.20) * plotWidth;
  const getYRecall = (r) => plot.top + plotHeight - (r / 100) * plotHeight;
  const getYFpr = (f) => plot.top + plotHeight - (Math.min(12, f) / 12) * plotHeight;

  const recallCoords = samplePoints.map((p) => `${getX(p.threshold).toFixed(1)},${getYRecall(p.recall).toFixed(1)}`);
  const recallPath = `M ${recallCoords.join(" L ")}`;
  const recallArea = `M ${getX(0.45).toFixed(1)},${plot.top + plotHeight} L ${recallCoords.join(" L ")} L ${getX(0.65).toFixed(1)},${plot.top + plotHeight} Z`;

  const fprCoords = samplePoints.map((p) => `${getX(p.threshold).toFixed(1)},${getYFpr(p.fpr).toFixed(1)}`);
  const fprPath = `M ${fprCoords.join(" L ")}`;

  const baselineX = getX(0.5435);
  const activeX = getX(threshold);
  const activeYRecall = getYRecall(currentMetrics.recall);
  const activeYFpr = getYFpr(currentMetrics.fpr);

  const balancedStartX = getX(0.52);
  const balancedEndX = getX(0.57);

  return (
    <article className="afi-card afi-sensitivity-card" ref={containerRef}>
      <div className="afi-sensitivity-header">
        <div>
          <div className="afi-sensitivity-badge-row">
            <span className="afi-sensitivity-pill">POLICY SIMULATOR</span>
            <span className="afi-sensitivity-mode">Bank of Ghana Risk Appetite Model</span>
          </div>
          <h3>Threshold Sensitivity Visualizer (FPR vs. Fraud Recall)</h3>
          <p className="afi-note">
            Simulate operational trade-offs by adjusting the hybrid decision threshold between <strong>0.450</strong> (aggressive fraud capture) and <strong>0.650</strong> (minimal false alarms).
          </p>
        </div>

        <div className="afi-presets">
          <span className="afi-preset-label">Presets:</span>
          <button
            type="button"
            className={`afi-preset-btn ${Math.abs(threshold - 0.48) < 0.002 ? "active" : ""}`}
            onClick={() => setThreshold(0.48)}
          >
            Aggressive (0.480)
          </button>
          <button
            type="button"
            className={`afi-preset-btn ${Math.abs(threshold - 0.5435) < 0.002 ? "active" : ""}`}
            onClick={() => setThreshold(0.5435)}
          >
            Baseline (0.5435)
          </button>
          <button
            type="button"
            className={`afi-preset-btn ${Math.abs(threshold - 0.62) < 0.002 ? "active" : ""}`}
            onClick={() => setThreshold(0.62)}
          >
            Conservative (0.620)
          </button>
        </div>
      </div>

      <div className="afi-slider-bar">
        <div className="afi-slider-label-row">
          <span>
            Operating Threshold: <strong className="afi-slider-value">{threshold.toFixed(4)}</strong>
          </span>
          <span className="afi-slider-hint">
            {threshold < 0.52
              ? "High Recall Zone (more fraud intercepted, higher customer OTP friction)"
              : threshold > 0.57
              ? "Low Friction Zone (smoother checkout, risk of subtle fraud leakage)"
              : "Balanced Operating Zone (optimal risk vs. customer experience)"}
          </span>
        </div>
        <div className="afi-range-wrapper">
          <span className="afi-range-edge">0.450</span>
          <input
            type="range"
            min="0.450"
            max="0.650"
            step="0.005"
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value))}
            className="afi-threshold-slider"
          />
          <span className="afi-range-edge">0.650</span>
        </div>
      </div>

      <div className="afi-sensitivity-kpis">
        <div className="afi-sens-kpi teal">
          <span className="afi-sens-label">Estimated Fraud Recall</span>
          <strong className="afi-sens-value">{currentMetrics.recall}%</strong>
          <span className={`afi-sens-delta ${currentMetrics.deltaRecall >= 0 ? "positive" : "negative"}`}>
            {currentMetrics.deltaRecall >= 0 ? `+${currentMetrics.deltaRecall}%` : `${currentMetrics.deltaRecall}%`} vs baseline
          </span>
        </div>

        <div className="afi-sens-kpi rose">
          <span className="afi-sens-label">False Positive Rate (FPR)</span>
          <strong className="afi-sens-value">{currentMetrics.fpr}%</strong>
          <span className={`afi-sens-delta ${currentMetrics.deltaFpr <= 0 ? "positive" : "negative"}`}>
            {currentMetrics.deltaFpr >= 0 ? `+${currentMetrics.deltaFpr}%` : `${currentMetrics.deltaFpr}%`} vs baseline
          </span>
        </div>

        <div className="afi-sens-kpi blue">
          <span className="afi-sens-label">Estimated Precision</span>
          <strong className="afi-sens-value">{currentMetrics.precision}%</strong>
          <span className="afi-sens-sub">True fraud / challenged alerts</span>
        </div>

        <div className="afi-sens-kpi amber">
          <span className="afi-sens-label">Customer Friction</span>
          <strong className="afi-sens-value">~{currentMetrics.friction} / 1k</strong>
          <span className={`afi-sens-delta ${currentMetrics.deltaFriction <= 0 ? "positive" : "negative"}`}>
            {currentMetrics.deltaFriction >= 0 ? `+${currentMetrics.deltaFriction}` : `${currentMetrics.deltaFriction}`} challenges / 1,000 txns
          </span>
        </div>
      </div>

      <div className="afi-sens-chart-wrapper">
        <svg
          viewBox={`0 0 ${svgWidth} ${height}`}
          className="afi-sens-svg"
          role="img"
          aria-label="Threshold sensitivity curve showing Recall vs False Positive Rate"
        >
          <defs>
            <linearGradient id="sensRecallGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#0284c7" stopOpacity="0.22" />
              <stop offset="100%" stopColor="#0284c7" stopOpacity="0.02" />
            </linearGradient>
          </defs>

          {/* Balanced Region Background */}
          <rect
            x={balancedStartX}
            y={plot.top}
            width={balancedEndX - balancedStartX}
            height={plotHeight}
            fill="#f0fdf4"
            opacity="0.8"
          />
          <text
            x={(balancedStartX + balancedEndX) / 2}
            y={plot.top + 14}
            textAnchor="middle"
            fill="#16a34a"
            fontSize="12"
            fontWeight="700"
          >
            OPTIMAL ZONE (0.52 - 0.57)
          </text>

          {/* Horizontal Gridlines */}
          {[0, 25, 50, 75, 100].map((val) => {
            const y = getYRecall(val);
            return (
              <g key={val}>
                <line x1={plot.left} x2={svgWidth - plot.right} y1={y} y2={y} stroke="#f1f5f9" strokeDasharray="3 3" />
                <text x={plot.left - 8} y={y + 4} textAnchor="end" fontSize="12" fill="#0284c7" fontWeight="600">
                  {val}%
                </text>
                <text x={svgWidth - plot.right + 8} y={y + 4} textAnchor="start" fontSize="12" fill="#e11d48" fontWeight="600">
                  {((val / 100) * 12).toFixed(1)}%
                </text>
              </g>
            );
          })}

          {/* X Axis Ticks */}
          {[0.45, 0.50, 0.55, 0.60, 0.65].map((t) => {
            const x = getX(t);
            return (
              <g key={t}>
                <line x1={x} x2={x} y1={plot.top + plotHeight} y2={plot.top + plotHeight + 6} stroke="#cbd5e1" />
                <text x={x} y={plot.top + plotHeight + 18} textAnchor="middle" fontSize="12" fill="#64748b" fontWeight="600">
                  {t.toFixed(2)}
                </text>
              </g>
            );
          })}

          {/* Recall Curve (Teal) */}
          <path d={recallArea} fill="url(#sensRecallGrad)" />
          <path d={recallPath} fill="none" stroke="#0284c7" strokeWidth="2.5" />

          {/* FPR Curve (Rose) */}
          <path d={fprPath} fill="none" stroke="#e11d48" strokeWidth="2.5" strokeDasharray="5 3" />

          {/* Baseline Marker */}
          <line
            x1={baselineX}
            x2={baselineX}
            y1={plot.top}
            y2={plot.top + plotHeight}
            stroke="#94a3b8"
            strokeWidth="1.5"
            strokeDasharray="4 3"
          />
          <text x={baselineX} y={plot.top + plotHeight + 32} textAnchor="middle" fontSize="12" fill="#64748b" fontWeight="700">
            Prod (0.5435)
          </text>

          {/* Active Threshold Indicator Line */}
          <line
            x1={activeX}
            x2={activeX}
            y1={plot.top}
            y2={plot.top + plotHeight}
            stroke="#0f172a"
            strokeWidth="2"
          />

          {/* Highlight Points on Curves */}
          <circle cx={activeX} cy={activeYRecall} r="5" fill="#0284c7" stroke="#ffffff" strokeWidth="2" />
          <circle cx={activeX} cy={activeYFpr} r="5" fill="#e11d48" stroke="#ffffff" strokeWidth="2" />

          {/* Axis Labels */}
          <text x={plot.left} y={plot.top - 6} textAnchor="start" fontSize="13" fill="#0284c7" fontWeight="700">
            ▲ Fraud Recall (%)
          </text>
          <text x={svgWidth - plot.right} y={plot.top - 6} textAnchor="end" fontSize="13" fill="#e11d48" fontWeight="700">
            False Positive Rate (%) ▲
          </text>
        </svg>

        <div className="afi-sens-legend">
          <div className="afi-legend-item">
            <span className="afi-legend-line teal" />
            <span>Fraud Recall (Interception Rate)</span>
          </div>
          <div className="afi-legend-item">
            <span className="afi-legend-line rose dashed" />
            <span>False Positive Rate (Benign Challenged)</span>
          </div>
          <div className="afi-legend-item">
            <span className="afi-legend-line gray dashed" />
            <span>Production Baseline (0.5435)</span>
          </div>
          <div className="afi-legend-item">
            <span className="afi-legend-box green" />
            <span>Optimal Range (0.52 - 0.57)</span>
          </div>
        </div>
      </div>
    </article>
  );
}

export default function AdaptiveIntelligence({ currentUser }) {
  const refreshTimer = useRef(null);
  const canManageModel = currentUser?.role === "admin";
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [live, setLive] = useState(false);
  const [updatedAt, setUpdatedAt] = useState("");
  const [retraining, setRetraining] = useState(false);
  const [retrainSuccess, setRetrainSuccess] = useState("");
  const [autoRetrainEnabled, setAutoRetrainEnabled] = useState(() => {
    return localStorage.getItem("momo_auto_retrain_enabled") === "true";
  });
  const hasMonitoringData = data !== null;
  const actionRequired = Boolean(data?.automated_trigger?.action_required);
  const autoRetrainTriggeredRef = useRef(
    localStorage.getItem("momo_auto_retrain_breach_acknowledged") === "true"
  );

  useEffect(() => {
    localStorage.setItem("momo_auto_retrain_enabled", String(autoRetrainEnabled));
  }, [autoRetrainEnabled]);

  const load = useCallback(async () => {
    try {
      const response = await adminFetch("/api/admin/monitoring");
      if (!response.ok) throw new Error("Failed to load monitoring metrics.");
      const payload = await response.json();
      setData(payload);
      setUpdatedAt(payload.event_timestamp || new Date().toISOString());
      setError("");
    } catch (err) {
      setError(err.message || "Could not load adaptive intelligence data.");
    }
  }, []);

  const triggerRetrain = useCallback(async (options = {}) => {
    if (autoRetrainEnabled && actionRequired) {
      autoRetrainTriggeredRef.current = true;
      localStorage.setItem("momo_auto_retrain_breach_acknowledged", "true");
    }
    setRetraining(true);
    setError("");
    setRetrainSuccess("");
    try {
      const response = await adminFetch("/api/admin/model/retrain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.detail || "Failed to record baseline marker.");
      }
      setRetrainSuccess(
        `✓ ${res.message || `Prototype baseline marker ${res.version} recorded for ${res.tracked_transactions} transactions.`}`
      );
      await load();
      setTimeout(() => setRetrainSuccess(""), 6000);
    } catch (err) {
      setError(err.message || "Failed to record baseline marker.");
    } finally {
      setRetraining(false);
    }
  }, [actionRequired, autoRetrainEnabled, load]);

  useEffect(() => {
    if (!hasMonitoringData) return;

    if (!actionRequired) {
      autoRetrainTriggeredRef.current = false;
      localStorage.removeItem("momo_auto_retrain_breach_acknowledged");
      return;
    }

    if (
      canManageModel &&
      autoRetrainEnabled &&
      !retraining &&
      !autoRetrainTriggeredRef.current
    ) {
      autoRetrainTriggeredRef.current = true;
      localStorage.setItem("momo_auto_retrain_breach_acknowledged", "true");
      triggerRetrain({ triggered_by: "AUTOMATED_POLICY_ENGINE" });
    }
  }, [
    autoRetrainEnabled,
    actionRequired,
    canManageModel,
    hasMonitoringData,
    retraining,
    triggerRetrain,
  ]);

  useEffect(() => {
    load();

    const REFRESH = new Set(["stats", "transaction", "monitor", "case"]);
    const unsubscribe = subscribeAdminEvents(
      (payload) => {
        if (REFRESH.has(payload.event)) {
          clearTimeout(refreshTimer.current);
          refreshTimer.current = setTimeout(load, 300);
        }
      },
      setLive
    );

    // Keep the panel current even if the live stream drops. The monitoring
    // endpoint recomputes drift from stored transactions on every call, so a
    // simple poll reflects newly entered transactions.
    const poll = setInterval(load, 15000);

    return () => {
      clearTimeout(refreshTimer.current);
      clearInterval(poll);
      unsubscribe();
    };
  }, [load]);

  if (!data) {
    return (
      <section className="afi-panel">
        <p className="afi-empty">
          {error ? `⚠️ ${error}` : "Loading adaptive intelligence…"}
        </p>
      </section>
    );
  }

  const drift = data.drift || [];
  const decisions = data.decisions || {};
  const flagRates = data.flag_rates || {};
  const recentTransactions = data.recent_transactions || [];
  const activeModelVersion = data.active_model_version || "V3.0-HYBRID";
  const insufficientDriftData = drift.some(
    (item) => item.status === "insufficient_data"
  );

  return (
    <section className="afi-panel">
      <header className="afi-header">
        <div>
          <div className="afi-eyebrow-row">
            <p className="eyebrow">CONTROL EFFECTIVENESS</p>
            <span className="afi-version-badge">
              Latest Version Record: <strong>{activeModelVersion}</strong>
            </span>
          </div>
          <h2>Adaptive Fraud Intelligence</h2>
          <p className="afi-subtitle">
            Continuous monitoring of transaction patterns, distribution drift
            and model performance (BoG, 2019).
          </p>
        </div>
        <div className="afi-header-actions">
          {canManageModel && (
            <button
              type="button"
              className="afi-retrain-top-btn"
              onClick={() => triggerRetrain()}
              disabled={retraining}
              title="Record prototype version and transaction-volume marker; does not refit the detector"
            >
              {retraining ? "Recording Marker…" : "Record Baseline Marker"}
            </button>
          )}
          <div className={`afi-live ${live ? "on" : ""}`}>
            <span className="live-dot" />
            {live ? "MONITORING" : "OFFLINE"}
          </div>
        </div>
      </header>

      {updatedAt && (
        <p className="afi-updated">Last updated {formatUpdated(updatedAt)} · Monitored transactions: <strong>{data.tracked || 0}</strong></p>
      )}

      {retrainSuccess && <div className="afi-success-banner">{retrainSuccess}</div>}
      {error && <p className="afi-error">{error}</p>}

      {data.retrain_recommended ? (
        <div className="afi-retrain-banner">
          <div className="afi-banner-icon">⚠</div>
          <div className="afi-banner-body">
            <h3>Baseline marker recommended</h3>
            <p>Distribution shift detected in monitored metrics:</p>
            <ul>
              {data.retrain_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
            {canManageModel && (
              <div className="afi-banner-action">
                <button
                  type="button"
                  className="afi-banner-btn"
                  onClick={() => triggerRetrain()}
                  disabled={retraining}
                >
                  {retraining ? "Recording Marker…" : "Record Baseline Marker"}
                </button>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="afi-healthy-banner">
          <div className="afi-healthy-content">
            {insufficientDriftData ? (
              <>
                <span>i</span> More transaction history is needed before drift
                can be assessed.
              </>
            ) : (
              <>
                <span>✓</span> No significant drift detected in the monitored
                distributions. PSI comparisons remain unchanged.
              </>
            )}
          </div>
          {canManageModel && (
            <button
              type="button"
              className="afi-recalibrate-btn"
              onClick={() => triggerRetrain()}
              disabled={retraining}
            >
              {retraining ? "Recording Marker…" : "Record Baseline Marker"}
            </button>
          )}
        </div>
      )}

      {data.automated_trigger && (
        <AutomatedTriggerCard
          trigger={data.automated_trigger}
          onRetrain={triggerRetrain}
          retraining={retraining}
          autoRetrainEnabled={autoRetrainEnabled}
          setAutoRetrainEnabled={setAutoRetrainEnabled}
          canManageModel={canManageModel}
        />
      )}

      <div className="afi-grid">
        <article className="afi-card">
          <h3>Distribution drift (PSI)</h3>
          <DriftBars drift={drift} />
          <p className="afi-note">
            Transaction-level older-half vs recent-half PSI. At least 10
            transactions are required in each half; warning ≥ 0.10, critical ≥
            0.25.
          </p>
        </article>

        <article className="afi-card">
          <h3>Model performance (analyst decisions)</h3>
          <div className="afi-kpis">
            <div>
              <span>Analyst-confirmed share</span>
              <strong>
                {data.analyst_confirmation_rate == null
                  ? "—"
                  : `${(data.analyst_confirmation_rate * 100).toFixed(1)}%`}
              </strong>
            </div>
            <div>
              <span>Confirmed fraud</span>
              <strong>{data.confirmed}</strong>
            </div>
            <div>
              <span>Legitimate</span>
              <strong>{data.legitimate}</strong>
            </div>
            <div>
              <span>Inconclusive</span>
              <strong>{data.inconclusive}</strong>
            </div>
          </div>
          <ul className="afi-decision-list">
            {Object.entries(decisions).map(([key, value]) => (
              <li key={key}>
                <span>{key}</span>
                <strong>{value}</strong>
              </li>
            ))}
            {Object.keys(decisions).length === 0 && (
              <li className="afi-muted">No analyst decisions recorded yet.</li>
            )}
          </ul>
          <p className="afi-note">
            {data.confirmed + data.legitimate} conclusive analyst decisions —
            this is not model precision or recall because only investigated
            cases are labelled.
          </p>
        </article>

        <article className="afi-card">
          <h3>Anomaly rate trend</h3>
          <TrendSpark
            data={data.anomaly_series}
            stroke="#dc2626"
            label="Anomaly rate"
          />
          <p className="afi-note">
            Recent anomaly rate {(data.recent_anomaly * 100).toFixed(1)}% vs
            baseline {(data.baseline_anomaly * 100).toFixed(1)}%.
          </p>
        </article>

        <article className="afi-card">
          <h3>Average risk score</h3>
          <TrendSpark
            data={data.risk_series}
            stroke="#d9a900"
            label="Average risk score"
          />
          <p className="afi-note">
            Mean hybrid risk score per day, scaled 0–1.
          </p>
        </article>
      </div>

      <div className="afi-signal-row">
        <article className="afi-card">
          <h3>Security-signal prevalence</h3>
          <ul className="afi-signal-list">
            <li>
              <span>Device change</span>
              <strong>{(flagRates.device_change_rate * 100).toFixed(1)}%</strong>
            </li>
            <li>
              <span>IP / network mismatch</span>
              <strong>{(flagRates.ip_mismatch_rate * 100).toFixed(1)}%</strong>
            </li>
            <li>
              <span>Recent SIM swap</span>
              <strong>{(flagRates.sim_swap_rate * 100).toFixed(1)}%</strong>
            </li>
            <li>
              <span>Velocity spike</span>
              <strong>{(flagRates.velocity_rate * 100).toFixed(1)}%</strong>
            </li>
          </ul>
          <p className="afi-note">
            Share of all stored transactions carrying each behavioural signal —
            rising rates are a drift indicator monitored by this module.
          </p>
        </article>
        <article className="afi-card">
          <h3>Monitoring status</h3>
          <ul className="afi-status-list">
            <li>
              <span>Days monitored</span>
              <strong>{data.monitored_days}</strong>
            </li>
            <li>
              <span>Latest snapshot</span>
              <strong>{data.latest_day}</strong>
            </li>
            <li>
              <span>Transaction amount PSI</span>
              <strong>
                {data.drift?.[0]?.psi == null
                  ? "—"
                  : data.drift[0].psi.toFixed(4)}
              </strong>
            </li>
            <li>
              <span>Risk distribution PSI</span>
              <strong>
                {data.drift?.[1]?.psi == null
                  ? "—"
                  : data.drift[1].psi.toFixed(4)}
              </strong>
            </li>
          </ul>
          <p className="afi-note">
            Daily snapshots are persisted to <code>model_monitoring</code> and
            drift alerts are written to the append-only audit trail.
          </p>
        </article>
      </div>

      <div className="afi-sensitivity-row">
        <ThresholdSensitivityVisualizer />
      </div>

      <div className="afi-activity-row">
        <article className="afi-card afi-feed-card">
          <div className="afi-feed-header">
            <div>
              <h3>Live Monitored Transactions Activity</h3>
              <p className="afi-note">
                Real-time transactions evaluated by the fraud engine and monitored for distribution drift.
              </p>
            </div>
            <div className="afi-feed-stats">
              <span className="afi-stat-pill">
                Total Monitored: <strong>{data.tracked || 0}</strong>
              </span>
              <span className="afi-stat-pill highlight">
                Today's Count: <strong>{data.today_transactions_count || 0}</strong>
              </span>
            </div>
          </div>

          {recentTransactions.length > 0 ? (
            <div className="afi-table-wrapper">
              <table className="afi-table">
                <thead>
                  <tr>
                    <th>Ref</th>
                    <th>Time</th>
                    <th>Flow / Accounts</th>
                    <th>Amount</th>
                    <th>Risk Score</th>
                    <th>Risk Level</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recentTransactions.map((tx) => (
                    <tr key={tx.id || tx.transaction_reference}>
                      <td className="mono font-semibold">{tx.transaction_reference}</td>
                      <td>{formatUpdated(tx.created_at)}</td>
                      <td>
                        <span className="afi-account-tag">{tx.origin_account || "—"}</span>
                        {" → "}
                        <span className="afi-account-tag">{tx.destination_account || "—"}</span>
                      </td>
                      <td><strong>GHS {Number(tx.amount || 0).toLocaleString()}</strong></td>
                      <td>{tx.risk_score != null ? Number(tx.risk_score).toFixed(4) : "—"}</td>
                      <td>
                        <span className={`afi-level-badge level-${(tx.risk_level || "low").toLowerCase()}`}>
                          {tx.risk_level || "LOW"}
                        </span>
                      </td>
                      <td>
                        <span className={`afi-status-badge status-${(tx.status || "completed").toLowerCase()}`}>
                          {tx.status || "COMPLETED"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="afi-empty">No transactions recorded yet. Run a transaction in the MoMo Simulator to see live updates!</p>
          )}
        </article>
      </div>
    </section>
  );
}