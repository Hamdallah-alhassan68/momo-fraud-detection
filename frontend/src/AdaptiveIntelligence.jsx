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

function AutomatedTriggerCard({
  trigger,
  onRetrain,
  retraining,
  autoRetrainEnabled,
  setAutoRetrainEnabled,
  canManageModel,
}) {
  const newTx = trigger?.new_transactions_since_baseline
    ?? trigger?.new_transactions_since_retrain
    ?? 0;
  const volLimit = trigger?.volume_threshold ?? 50;
  const volPct = Math.min(100, Math.round((newTx / volLimit) * 100));
  const isBreached = Boolean(trigger?.action_required);

  return (
    <article className={`afi-card afi-trigger-card ${isBreached ? "breached" : "stable"}`}>
      <div className="afi-trigger-header">
        <div>
          <div className="afi-trigger-badge-row">
            <span className={`afi-trigger-pill ${isBreached ? "breach-pill" : "stable-pill"}`}>
              {isBreached ? "BASELINE UPDATE REQUIRED" : "ACTIVE MONITORING · WITHIN LIMITS"}
            </span>
            <span className="afi-trigger-policy">Prototype Monitoring Thresholds</span>
          </div>
          <h3>Drift Monitoring & Baseline Thresholds</h3>
          <p className="afi-note">
            A critical PSI shift (≥ 0.25) or 50 new transactions recommends capturing a reference distribution. This resets the PSI comparison baseline, but does not refit or version the detector.
          </p>
        </div>
        {canManageModel ? (
          <div className="afi-trigger-actions">
            <label className="afi-toggle-label" title="Automatically capture a reference distribution whenever a threshold is breached">
              <input
                type="checkbox"
                checked={autoRetrainEnabled}
                onChange={(e) => setAutoRetrainEnabled(e.target.checked)}
              />
              <span className="afi-toggle-switch" />
              <span className="afi-toggle-text">Auto-capture baseline on breach</span>
            </label>
            <button
              type="button"
              className={`afi-trigger-btn ${isBreached ? "breach-btn" : ""}`}
              onClick={() => onRetrain({ triggered_by: "AUTOMATED_THRESHOLD_RULE" })}
              disabled={retraining}
            >
              {retraining ? "Capturing Baseline…" : isBreached ? "Capture Triggered Baseline" : "Capture Baseline"}
            </button>
          </div>
        ) : (
          <p className="afi-note">Capturing a new reference baseline is restricted to administrators.</p>
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
              ? "Volume threshold reached. A new reference baseline is recommended."
              : `${volLimit - newTx} more transactions until the next baseline review.`}
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

function ThresholdSensitivityVisualizer({ modelEvaluation }) {
  const sensitivity = modelEvaluation?.hybrid_threshold_sensitivity;
  const samplePoints = sensitivity?.points || [];
  const minimumThreshold = samplePoints[0]?.threshold ?? 0.4;
  const maximumThreshold =
    samplePoints[samplePoints.length - 1]?.threshold ?? 0.65;
  const activeMediumBoundary =
    modelEvaluation?.active_medium_boundary ?? minimumThreshold;
  const activeHighBoundary = modelEvaluation?.active_high_boundary ?? 0.5;
  const [threshold, setThreshold] = useState(activeHighBoundary);
  const containerRef = useRef(null);
  const [svgWidth, setSvgWidth] = useState(680);

  useEffect(() => {
    setThreshold(activeHighBoundary);
  }, [activeHighBoundary]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      setSvgWidth(Math.max(340, Math.round(entry.contentRect.width)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const findNearestPoint = (target) =>
    samplePoints.reduce(
      (closest, point) =>
        Math.abs(point.threshold - target)
          < Math.abs(closest.threshold - target)
          ? point
          : closest,
      samplePoints[0],
    );

  if (!samplePoints.length) {
    return (
      <article className="afi-card afi-sensitivity-card">
        <h3>Threshold Sensitivity (PaySim validation)</h3>
        <p className="afi-note">
          No measured threshold curve is available for the active model. The
          dashboard will not estimate or invent these metrics.
        </p>
      </article>
    );
  }

  const currentMetrics = findNearestPoint(threshold);
  const baselineMetrics = findNearestPoint(activeHighBoundary);
  const validationRows = sensitivity.rows || 0;
  const percent = (value) => (value * 100).toFixed(1);
  const delta = (value, baseline) => {
    const difference = (value - baseline) * 100;
    return `${difference >= 0 ? "+" : ""}${difference.toFixed(1)} pp`;
  };
  const flaggedPerThousand = (point) =>
    validationRows ? (point.flagged_count / validationRows) * 1000 : 0;

  const height = 240;
  const plot = { left: 54, right: 54, top: 22, bottom: 44 };
  const plotWidth = Math.max(100, svgWidth - plot.left - plot.right);
  const plotHeight = height - plot.top - plot.bottom;
  const maximumFpr = Math.max(
    1,
    Math.ceil(
      Math.max(...samplePoints.map((point) => point.false_positive_rate)) * 100 / 5,
    ) * 5,
  );
  const getX = (t) =>
    plot.left
    + ((t - minimumThreshold) / (maximumThreshold - minimumThreshold))
      * plotWidth;
  const getYRecall = (r) => plot.top + plotHeight - (r / 100) * plotHeight;
  const getYFpr = (f) =>
    plot.top + plotHeight - (Math.min(maximumFpr, f * 100) / maximumFpr) * plotHeight;
  const recallCoords = samplePoints.map(
    (point) =>
      `${getX(point.threshold).toFixed(1)},${getYRecall(point.recall * 100).toFixed(1)}`,
  );
  const recallPath = `M ${recallCoords.join(" L ")}`;
  const fprCoords = samplePoints.map(
    (point) =>
      `${getX(point.threshold).toFixed(1)},${getYFpr(point.false_positive_rate).toFixed(1)}`,
  );
  const fprPath = `M ${fprCoords.join(" L ")}`;
  const baselineX = getX(activeHighBoundary);
  const activeX = getX(threshold);
  const activeYRecall = getYRecall(currentMetrics.recall * 100);
  const activeYFpr = getYFpr(currentMetrics.false_positive_rate);
  const xTicks = Array.from(
    { length: 6 },
    (_, index) =>
      minimumThreshold + (maximumThreshold - minimumThreshold) * index / 5,
  );

  return (
    <article className="afi-card afi-sensitivity-card" ref={containerRef}>
      <div className="afi-sensitivity-header">
        <div>
          <div className="afi-sensitivity-badge-row">
            <span className="afi-sensitivity-pill">MEASURED PAYSIM VALIDATION</span>
            <span className="afi-sensitivity-mode">Offline analysis · not applied to runtime</span>
          </div>
          <h3>Hybrid Policy Threshold Sensitivity</h3>
          <p className="afi-note">
            Measured on {Number(sensitivity.rows).toLocaleString()} validation
            transactions ({Number(sensitivity.fraud_labels).toLocaleString()} fraud
            labels). Move the cutoff to inspect the observed precision/recall
            trade-off; this what-if control does not change live decisions.
            Security signals absent from PaySim are not represented in this curve.
          </p>
        </div>

        <div className="afi-presets">
          <span className="afi-preset-label">Compare cutoffs:</span>
          <button
            type="button"
            className={`afi-preset-btn ${Math.abs(threshold - activeMediumBoundary) < 0.002 ? "active" : ""}`}
            onClick={() => setThreshold(activeMediumBoundary)}
          >
            MEDIUM+ ({activeMediumBoundary.toFixed(3)})
          </button>
          <button
            type="button"
            className={`afi-preset-btn ${Math.abs(threshold - activeHighBoundary) < 0.002 ? "active" : ""}`}
            onClick={() => setThreshold(activeHighBoundary)}
          >
            Current HIGH ({activeHighBoundary.toFixed(3)})
          </button>
          <button
            type="button"
            className={`afi-preset-btn ${Math.abs(threshold - 0.6) < 0.002 ? "active" : ""}`}
            onClick={() => setThreshold(0.6)}
          >
            More conservative (0.600)
          </button>
        </div>
      </div>

      <div className="afi-slider-bar">
        <div className="afi-slider-label-row">
          <span>
            What-if cutoff: <strong className="afi-slider-value">{currentMetrics.threshold.toFixed(3)}</strong>
          </span>
          <span className="afi-slider-hint">
            Active MEDIUM boundary: {activeMediumBoundary.toFixed(3)}
            {" · "}active HIGH boundary: {activeHighBoundary.toFixed(3)}
          </span>
        </div>
        <div className="afi-range-wrapper">
          <span className="afi-range-edge">{minimumThreshold.toFixed(3)}</span>
          <input
            type="range"
            min={minimumThreshold}
            max={maximumThreshold}
            step="0.005"
            value={threshold}
            onChange={(event) => setThreshold(Number(event.target.value))}
            className="afi-threshold-slider"
          />
          <span className="afi-range-edge">{maximumThreshold.toFixed(3)}</span>
        </div>
      </div>

      <div className="afi-sensitivity-kpis">
        <div className="afi-sens-kpi teal">
          <span className="afi-sens-label">Validation fraud recall</span>
          <strong className="afi-sens-value">{percent(currentMetrics.recall)}%</strong>
          <span className={`afi-sens-delta ${currentMetrics.recall >= baselineMetrics.recall ? "positive" : "negative"}`}>
            {delta(currentMetrics.recall, baselineMetrics.recall)} vs current HIGH
          </span>
        </div>

        <div className="afi-sens-kpi rose">
          <span className="afi-sens-label">Validation false-positive rate</span>
          <strong className="afi-sens-value">{percent(currentMetrics.false_positive_rate)}%</strong>
          <span className={`afi-sens-delta ${currentMetrics.false_positive_rate <= baselineMetrics.false_positive_rate ? "positive" : "negative"}`}>
            {delta(currentMetrics.false_positive_rate, baselineMetrics.false_positive_rate)} vs current HIGH
          </span>
        </div>

        <div className="afi-sens-kpi blue">
          <span className="afi-sens-label">Validation precision</span>
          <strong className="afi-sens-value">{percent(currentMetrics.precision)}%</strong>
          <span className="afi-sens-sub">Confirmed PaySim fraud / flagged transactions</span>
        </div>

        <div className="afi-sens-kpi amber">
          <span className="afi-sens-label">Flagged per 1,000 validation transactions</span>
          <strong className="afi-sens-value">{flaggedPerThousand(currentMetrics).toFixed(1)}</strong>
          <span className="afi-sens-sub">
            {Number(currentMetrics.flagged_count).toLocaleString()} flagged of{" "}
            {Number(validationRows).toLocaleString()}
          </span>
        </div>
      </div>
      <p className="afi-note">
        {sensitivity.selection_note}
      </p>

      <div className="afi-sens-chart-wrapper">
        <svg
          viewBox={`0 0 ${svgWidth} ${height}`}
          className="afi-sens-svg"
          role="img"
          aria-label="Measured PaySim validation recall and false-positive rate by hybrid score threshold"
        >
          {[0, 25, 50, 75, 100].map((val) => {
            const y = getYRecall(val);
            const fprTick = (val / 100) * maximumFpr;
            return (
              <g key={val}>
                <line x1={plot.left} x2={svgWidth - plot.right} y1={y} y2={y} stroke="#f1f5f9" strokeDasharray="3 3" />
                <text x={plot.left - 8} y={y + 4} textAnchor="end" fontSize="12" fill="#0284c7" fontWeight="600">
                  {val}%
                </text>
                <text x={svgWidth - plot.right + 8} y={y + 4} textAnchor="start" fontSize="12" fill="#e11d48" fontWeight="600">
                  {fprTick.toFixed(1)}%
                </text>
              </g>
            );
          })}

          {xTicks.map((t) => {
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

          <path d={recallPath} fill="none" stroke="#0284c7" strokeWidth="2.5" />
          <path d={fprPath} fill="none" stroke="#e11d48" strokeWidth="2.5" strokeDasharray="5 3" />

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
            HIGH ({activeHighBoundary.toFixed(3)})
          </text>

          <line
            x1={activeX}
            x2={activeX}
            y1={plot.top}
            y2={plot.top + plotHeight}
            stroke="#0f172a"
            strokeWidth="2"
          />

          <circle cx={activeX} cy={activeYRecall} r="5" fill="#0284c7" stroke="#ffffff" strokeWidth="2" />
          <circle cx={activeX} cy={activeYFpr} r="5" fill="#e11d48" stroke="#ffffff" strokeWidth="2" />

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
            <span>Measured validation recall</span>
          </div>
          <div className="afi-legend-item">
            <span className="afi-legend-line rose dashed" />
            <span>Measured validation false-positive rate</span>
          </div>
          <div className="afi-legend-item">
            <span className="afi-legend-line gray dashed" />
            <span>Current HIGH boundary ({activeHighBoundary.toFixed(3)})</span>
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
  const [performanceReplayIndex, setPerformanceReplayIndex] = useState(-1);
  const [performanceReplayPlaying, setPerformanceReplayPlaying] = useState(false);
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
      const response = await adminFetch("/api/admin/model/baseline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.detail || "Failed to capture drift baseline.");
      }
      setRetrainSuccess(
        `✓ ${res.message || `Drift baseline captured for ${res.tracked_transactions} transactions.`}`
      );
      await load();
      setTimeout(() => setRetrainSuccess(""), 6000);
    } catch (err) {
      setError(err.message || "Failed to capture drift baseline.");
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

  const performanceReplayWindows =
    data?.runtime_model_evaluation?.hybrid_policy_time_windows?.windows || [];

  useEffect(() => {
    if (!performanceReplayPlaying || performanceReplayWindows.length === 0) {
      return undefined;
    }

    const timer = window.setTimeout(() => {
      if (performanceReplayIndex >= performanceReplayWindows.length - 1) {
        setPerformanceReplayPlaying(false);
        return;
      }
      setPerformanceReplayIndex((current) => current + 1);
    }, 1100);

    return () => window.clearTimeout(timer);
  }, [
    performanceReplayIndex,
    performanceReplayPlaying,
    performanceReplayWindows.length,
  ]);

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
  const modelEvaluation = data.runtime_model_evaluation;
  const visiblePerformanceReplayWindows = performanceReplayWindows.slice(
    0,
    performanceReplayIndex + 1,
  );
  const resolvedCaseFeedback = data.resolved_case_feedback || {};
  const driftBaseline = data.drift_baseline || {};
  const insufficientDriftData = drift.some(
    (item) => item.status === "insufficient_data"
  );
  const baselineUpdateRecommended = Boolean(
    data.baseline_update_recommended ?? data.retrain_recommended
  );
  const baselineUpdateReasons =
    data.baseline_update_reasons || data.retrain_reasons || [];

  return (
    <section className="afi-panel">
      <header className="afi-header">
        <div>
          <div className="afi-eyebrow-row">
            <p className="eyebrow">CONTROL EFFECTIVENESS</p>
            <span className="afi-version-badge">
              Active Model: <strong>{activeModelVersion}</strong>
            </span>
          </div>
          <h2>Adaptive Fraud Intelligence</h2>
          <p className="afi-subtitle">
            Continuous monitoring of transaction patterns, distribution drift
            and selected-case outcome feedback. Prototype thresholds do not
            certify regulatory compliance.
          </p>
        </div>
        <div className="afi-header-actions">
          {canManageModel && (
            <button
              type="button"
              className="afi-retrain-top-btn"
              onClick={() => triggerRetrain()}
              disabled={retraining}
              title="Capture a new reference distribution; this does not refit the detector"
            >
              {retraining ? "Capturing Baseline…" : "Capture Baseline"}
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

      {baselineUpdateRecommended ? (
        <div className="afi-retrain-banner">
          <div className="afi-banner-icon">⚠</div>
          <div className="afi-banner-body">
            <h3>New drift baseline recommended</h3>
            <p>Distribution shift detected in monitored metrics:</p>
            <ul>
              {baselineUpdateReasons.map((reason) => (
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
                  {retraining ? "Capturing Baseline…" : "Capture Baseline"}
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
                distributions against the displayed reference.
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
              {retraining ? "Capturing Baseline…" : "Capture Baseline"}
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
            {driftBaseline.recorded_at
              ? `Reference captured ${formatUpdated(driftBaseline.recorded_at)} by ${driftBaseline.recorded_by} from ${driftBaseline.amount_samples} amount and ${driftBaseline.risk_score_samples} risk-score samples.`
              : "Until an administrator captures a reference, PSI compares the older and recent halves of the rolling sample."}{" "}
            At least 10 samples per feature are required; warning ≥ 0.10,
            critical ≥ 0.25.
          </p>
          <div className="afi-drift-risk-chart">
              <h4>Average risk score trend</h4>
              <TrendSpark
                data={data.risk_series}
                stroke="#d9a900"
                label="Average risk score"
              />
              <p className="afi-note">
                Mean hybrid risk score per day, scaled 0–1.
              </p>
          </div>
        </article>

        <article className="afi-card">
          <h3>Resolved-case feedback (last 14 days)</h3>
          <div className="afi-kpis">
            <div>
              <span>Conclusive resolved cases</span>
              <strong>{resolvedCaseFeedback.conclusive_cases ?? 0}</strong>
            </div>
            <div>
              <span>Confirmed share of conclusive cases</span>
              <strong>
                {resolvedCaseFeedback.confirmation_rate == null
                  ? "—"
                  : `${(resolvedCaseFeedback.confirmation_rate * 100).toFixed(1)}%`}
              </strong>
            </div>
            <div>
              <span>All fraud decisions</span>
              <strong>{data.confirmed}</strong>
            </div>
            <div>
              <span>All legitimate decisions</span>
              <strong>{data.legitimate}</strong>
            </div>
          </div>
          {(resolvedCaseFeedback.conclusive_cases ?? 0) === 0 && (
            <p className="afi-feedback-empty">
              No conclusive cases were marked resolved in the last 14 days.
              In Alerts &amp; investigations, save a FRAUD_CONFIRMED or
              LEGITIMATE decision and mark that case RESOLVED. Unresolved
              decisions are excluded from this trend.
            </p>
          )}
          <p className="afi-note">
            The chart shows the confirmed share among resolved cases with a
            conclusive decision. Reviews cover flagged cases only, so this
            selection-biased feedback is not model precision or recall.
          </p>
          <TrendSpark
            data={resolvedCaseFeedback.trend}
            stroke="#7c3aed"
            label="Confirmed share of resolved cases"
          />
          <p className="afi-note">
            Inconclusive decisions: {data.inconclusive}. Unreviewed and
            low-risk transactions are not treated as verified labels.
          </p>
        </article>

        <article className="afi-card">
          <h3>Active model and policy — PaySim holdout</h3>
          {modelEvaluation ? (
            <>
              <div className="afi-kpis">
                <div>
                  <span>Isolation Forest precision</span>
                  <strong>{(modelEvaluation.precision * 100).toFixed(1)}%</strong>
                </div>
                <div>
                  <span>Isolation Forest recall</span>
                  <strong>{(modelEvaluation.recall * 100).toFixed(1)}%</strong>
                </div>
                <div>
                  <span>Isolation Forest false-positive rate</span>
                  <strong>
                    {(modelEvaluation.false_positive_rate * 100).toFixed(1)}%
                  </strong>
                </div>
                <div>
                  <span>Hybrid MEDIUM+ false-positive rate</span>
                  <strong>
                    {modelEvaluation.hybrid_medium_or_high
                      ? `${(modelEvaluation.hybrid_medium_or_high.false_positive_rate * 100).toFixed(1)}%`
                      : "—"}
                  </strong>
                </div>
                <div>
                  <span>Hybrid HIGH false-positive rate</span>
                  <strong>
                    {modelEvaluation.hybrid_high
                      ? `${(modelEvaluation.hybrid_high.false_positive_rate * 100).toFixed(1)}%`
                      : "—"}
                  </strong>
                </div>
              </div>
              <p className="afi-note">
                Fixed chronological synthetic-data holdout from{" "}
                {modelEvaluation.dataset}; seed {modelEvaluation.seed}. The
                test set contains{" "}
                {Number(modelEvaluation.test_fraud_labels).toLocaleString()}{" "}
                fraud labels from {Number(modelEvaluation.sampled_rows).toLocaleString()}{" "}
                sampled transactions ({Number(modelEvaluation.test_rows).toLocaleString()}{" "}
                test transactions). Hybrid MEDIUM+ precision/recall:{" "}
                {modelEvaluation.hybrid_medium_or_high
                  ? `${(modelEvaluation.hybrid_medium_or_high.precision * 100).toFixed(1)}% / ${(modelEvaluation.hybrid_medium_or_high.recall * 100).toFixed(1)}%`
                  : "not available"}
                ; hybrid HIGH precision/recall:{" "}
                {modelEvaluation.hybrid_high
                  ? `${(modelEvaluation.hybrid_high.precision * 100).toFixed(1)}% / ${(modelEvaluation.hybrid_high.recall * 100).toFixed(1)}%`
                  : "not available"}.
              </p>
              <p className="afi-note">
                Dataset SHA-256:{" "}
                <code>{modelEvaluation.dataset_sha256?.slice(0, 16)}…</code>
                {" · "}scikit-learn {modelEvaluation.scikit_learn_version}
              </p>
            </>
          ) : (
            <p className="afi-evaluation-unavailable">
              The running backend did not provide its PaySim evaluation
              manifest. Restart the backend so it loads the latest
              models/paysim-runtime artifact.
            </p>
          )}
          <p className="afi-note">
            Evaluation is synthetic and fixed; it does not demonstrate
            real-customer performance or compliance.
          </p>
        </article>

        {modelEvaluation?.hybrid_seed_sensitivity && (
          <article className="afi-card">
            <h3>Fixed-policy sensitivity across seeded PaySim runs</h3>
            <p className="afi-note">
              Seeds: {modelEvaluation.hybrid_seed_sensitivity.seeds.join(", ")}
              {" · "}scikit-learn{" "}
              {modelEvaluation.hybrid_seed_sensitivity.evaluation_scikit_learn_version}
            </p>
            <div className="afi-kpis">
              <div>
                <span>MEDIUM+ mean FPR</span>
                <strong>
                  {(
                    modelEvaluation.hybrid_seed_sensitivity.medium_or_high
                      .false_positive_rate.mean * 100
                  ).toFixed(2)}
                  %
                </strong>
              </div>
              <div>
                <span>MEDIUM+ mean recall</span>
                <strong>
                  {(
                    modelEvaluation.hybrid_seed_sensitivity.medium_or_high
                      .recall.mean * 100
                  ).toFixed(1)}
                  %
                </strong>
              </div>
              <div>
                <span>HIGH mean FPR</span>
                <strong>
                  {(
                    modelEvaluation.hybrid_seed_sensitivity.high
                      .false_positive_rate.mean * 100
                  ).toFixed(2)}
                  %
                </strong>
              </div>
              <div>
                <span>HIGH mean recall</span>
                <strong>
                  {(
                    modelEvaluation.hybrid_seed_sensitivity.high.recall.mean *
                    100
                  ).toFixed(1)}
                  %
                </strong>
              </div>
            </div>
            <p className="afi-note">
              Across-run FPR range: MEDIUM+{" "}
              {(
                modelEvaluation.hybrid_seed_sensitivity.medium_or_high
                  .false_positive_rate.min * 100
              ).toFixed(2)}
              –{(
                modelEvaluation.hybrid_seed_sensitivity.medium_or_high
                  .false_positive_rate.max * 100
              ).toFixed(2)}
              %; HIGH{" "}
              {(
                modelEvaluation.hybrid_seed_sensitivity.high
                  .false_positive_rate.min * 100
              ).toFixed(2)}
              –{(
                modelEvaluation.hybrid_seed_sensitivity.high
                  .false_positive_rate.max * 100
              ).toFixed(2)}
              %.
            </p>
            <p className="afi-note">
              Each run fits a different model on a seeded sample of the same
              synthetic source. This is sensitivity evidence, not performance
              of the exact active artifact or independent external validation.
            </p>
          </article>
        )}

        {modelEvaluation?.hybrid_policy_time_windows?.windows?.length > 0 && (
          <article className="afi-card">
            <h3>Synthetic labelled-performance replay</h3>
            <p className="afi-note">
              Replay chronological windows from the fixed PaySim test holdout.
              True synthetic labels are available for this offline demonstration;
              they are not incoming live transactions or customer outcomes.
            </p>
            <div className="afi-replay-actions">
              <button
                type="button"
                className="afi-recalibrate-btn"
                onClick={() => {
                  setPerformanceReplayIndex(0);
                  setPerformanceReplayPlaying(true);
                }}
                disabled={
                  performanceReplayPlaying ||
                  performanceReplayWindows.length === 0
                }
              >
                {performanceReplayWindows.length > 0 &&
                performanceReplayIndex >= performanceReplayWindows.length - 1
                  ? "Replay labelled holdout"
                  : "Start labelled replay"}
              </button>
              {performanceReplayPlaying && (
                <button
                  type="button"
                  className="afi-recalibrate-btn"
                  onClick={() => setPerformanceReplayPlaying(false)}
                >
                  Pause replay
                </button>
              )}
              <button
                type="button"
                className="afi-recalibrate-btn"
                onClick={() => {
                  setPerformanceReplayPlaying(false);
                  setPerformanceReplayIndex(-1);
                }}
                disabled={
                  performanceReplayIndex < 0 && !performanceReplayPlaying
                }
              >
                Reset
              </button>
              <span aria-live="polite">
                {performanceReplayIndex < 0
                  ? "Replay ready"
                  : `Processed ${performanceReplayIndex + 1} of ${performanceReplayWindows.length} windows`}
              </span>
            </div>
            <div className="afi-time-window-list">
              {visiblePerformanceReplayWindows.map(
                (window) => (
                  <div
                    className="afi-time-window"
                    key={`${window.window}-${window.step_min}`}
                  >
                    <strong>
                      Replay window {window.window}: steps {window.step_min}–
                      {window.step_max}
                    </strong>
                    <span>
                      {Number(window.rows).toLocaleString()} transactions ·{" "}
                      {Number(window.fraud_labels).toLocaleString()} fraud
                      labels
                    </span>
                    <span>
                      MEDIUM+: precision{" "}
                      {(window.medium_or_high.precision * 100).toFixed(1)}% ·
                      recall{" "}
                      {(window.medium_or_high.recall * 100).toFixed(1)}% ·
                      FPR{" "}
                      {(window.medium_or_high.false_positive_rate * 100).toFixed(1)}%
                    </span>
                    <span>
                      HIGH: precision {(window.high.precision * 100).toFixed(1)}%
                      {" · "}recall {(window.high.recall * 100).toFixed(1)}%
                      {" · "}FPR{" "}
                      {(window.high.false_positive_rate * 100).toFixed(1)}%
                    </span>
                    <strong>
                      Cumulative through step {window.cumulative.through_step}
                    </strong>
                    <span>
                      {Number(window.cumulative.rows).toLocaleString()} labelled
                      transactions ·{" "}
                      {Number(window.cumulative.fraud_labels).toLocaleString()}{" "}
                      fraud outcomes
                    </span>
                    <span>
                      MEDIUM+ cumulative: precision{" "}
                      {(window.cumulative.medium_or_high.precision * 100).toFixed(1)}%
                      {" · "}recall{" "}
                      {(window.cumulative.medium_or_high.recall * 100).toFixed(1)}%
                      {" · "}FPR{" "}
                      {(window.cumulative.medium_or_high.false_positive_rate * 100).toFixed(1)}%
                    </span>
                    <span>
                      HIGH cumulative: precision{" "}
                      {(window.cumulative.high.precision * 100).toFixed(1)}%
                      {" · "}recall{" "}
                      {(window.cumulative.high.recall * 100).toFixed(1)}%
                      {" · "}FPR{" "}
                      {(window.cumulative.high.false_positive_rate * 100).toFixed(1)}%
                    </span>
                  </div>
                ),
              )}
            </div>
            <p className="afi-note">
              Cumulative metrics are recomputed over all test rows through each
              displayed time window. Production monitoring still requires
              reliable delayed labels, including transactions that were not
              flagged.
            </p>
          </article>
        )}

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
        <ThresholdSensitivityVisualizer modelEvaluation={modelEvaluation} />
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