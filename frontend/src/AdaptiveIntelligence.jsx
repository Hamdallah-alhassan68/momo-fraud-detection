import { useCallback, useEffect, useRef, useState } from "react";
import { adminFetch, subscribeAdminEvents } from "./adminApi.js";
import "./AdaptiveIntelligence.css";

const SIGNAL_LABELS = {
  device_change: "New device",
  new_account: "New account",
  cold_start: "Limited history",
  ip_network_mismatch: "Network change",
  location_mismatch: "Location change",
  impossible_travel: "Impossible travel",
  recipient_novel: "New recipient",
  recipient_burst: "Recipient velocity",
  velocity_spike: "Velocity spike",
  frequency_spike: "Frequency spike",
  amount_deviation: "Unusual amount",
  unusual_hour: "Unusual time",
  recent_sim_swap: "SIM-swap test signal",
};

function formatUpdated(value) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatMetric(value, digits = 1) {
  return Number.isFinite(Number(value))
    ? `${(Number(value) * 100).toFixed(digits)}%`
    : "—";
}

function formatCount(value) {
  return Number(value || 0).toLocaleString();
}

function Metric({ label, value, note }) {
  return (
    <article className="afi-v2-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </article>
  );
}

function RiskSignals({ signals }) {
  if (!signals || Array.isArray(signals)) {
    return <span className="afi-v2-muted">No signal details</span>;
  }

  const active = Object.entries(signals)
    .filter(([key, value]) => SIGNAL_LABELS[key] && value === true)
    .map(([key]) => SIGNAL_LABELS[key]);

  if (!active.length) {
    return <span className="afi-v2-muted">No elevated signals</span>;
  }

  return (
    <div className="afi-v2-signal-list">
      {active.map((label) => (
        <span className="afi-v2-signal" key={label}>{label}</span>
      ))}
    </div>
  );
}

function DriftList({ drift = [] }) {
  if (!drift.length) return <p className="afi-v2-muted">No drift metrics available.</p>;
  return (
    <div className="afi-v2-drift-list">
      {drift.map((item) => (
        <div className="afi-v2-drift-row" key={item.feature}>
          <div>
            <strong>{item.feature}</strong>
            <span className={`afi-v2-status ${item.status}`}>
              {(item.status || "unknown").replaceAll("_", " ")}
            </span>
          </div>
          <span>{item.psi == null ? "Insufficient data" : `PSI ${item.psi.toFixed(4)}`}</span>
        </div>
      ))}
    </div>
  );
}

export default function AdaptiveIntelligence() {
  const refreshTimer = useRef(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [live, setLive] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await adminFetch("/api/admin/monitoring");
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || "Could not load model monitoring.");
      }
      setData(payload);
      setError("");
    } catch (loadError) {
      setError(loadError.message || "Could not load model monitoring.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const refreshEvents = new Set(["stats", "transaction", "monitor", "case"]);
    const unsubscribe = subscribeAdminEvents((payload) => {
      if (refreshEvents.has(payload.event)) {
        clearTimeout(refreshTimer.current);
        refreshTimer.current = setTimeout(load, 250);
      }
    }, setLive);
    const poll = window.setInterval(load, 15000);

    return () => {
      clearTimeout(refreshTimer.current);
      window.clearInterval(poll);
      unsubscribe();
    };
  }, [load]);

  if (!data) {
    return (
      <section className="afi-panel">
        <p className="afi-empty">{error || "Loading adaptive intelligence…"}</p>
      </section>
    );
  }

  const model = data.model_training || {};
  const evaluation = model.runtime_evaluation || data.runtime_model_evaluation || {};
  const feedback = model.feedback_labels || {};
  const rates = data.flag_rates || {};
  const transactions = data.recent_transactions || [];
  const modelHistory = model.version_history || [];
  const trainingSplits = [
    { label: "Training set", rows: model.training_rows, labels: model.training_fraud_labels },
    { label: "Validation set", rows: model.validation_rows, labels: model.validation_fraud_labels },
    { label: "Time-separated test set", rows: model.test_rows, labels: model.test_fraud_labels },
  ];

  return (
    <section className="afi-panel afi-v2">
      <header className="afi-v2-header">
        <div>
          <p className="afi-v2-eyebrow">MODEL MONITORING &amp; LEARNING</p>
          <h2>Adaptive Fraud Intelligence</h2>
          <p>
            See what trained the active model, how production activity is
            monitored, and which reviewed outcomes could support future releases.
          </p>
        </div>
        <div className="afi-v2-actions">
          <span className={`afi-v2-live ${live ? "on" : ""}`}>
            <i /> {live ? "LIVE UPDATES" : "POLLING"}
          </span>
          <button type="button" onClick={load} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      <p className="afi-v2-updated">
        Updated {formatUpdated(data.event_timestamp)} · {formatCount(data.tracked)} assessed transactions
      </p>
      {error && <p className="afi-error">{error}</p>}

      <div className="afi-v2-section-heading">
        <div>
          <h3>Active model training record</h3>
          <p>Training is an offline, reviewed release process—not automatic online learning.</p>
        </div>
        <span className="afi-version-badge">
          Active model: <strong>{model.active_model_version || data.active_model_version || "Unversioned artifact"}</strong>
        </span>
      </div>

      <div className="afi-v2-grid afi-v2-training-grid">
        <article className="afi-v2-card afi-v2-training-card">
          <h4>Training data and method</h4>
          <dl className="afi-v2-details">
            <div><dt>Dataset</dt><dd>{model.dataset || "Not recorded"}</dd></div>
            <div><dt>Training pipeline</dt><dd>Chronological synthetic PaySim split</dd></div>
            <div><dt>Random seed</dt><dd>{model.training_seed ?? "Not recorded"}</dd></div>
            <div><dt>scikit-learn</dt><dd>{model.scikit_learn_version || "Not recorded"}</dd></div>
            <div><dt>Training run time</dt><dd>{formatUpdated(model.trained_at)}</dd></div>
            <div>
              <dt>Dataset SHA-256</dt>
              <dd className="afi-v2-hash">{model.dataset_sha256 || "Not recorded"}</dd>
            </div>
          </dl>
          <h5>Feature inputs ({model.features?.length || 0})</h5>
          <div className="afi-v2-feature-list">
            {(model.features || []).map((feature) => (
              <code key={feature}>{feature}</code>
            ))}
          </div>
        </article>

        <article className="afi-v2-card">
          <h4>Dataset splits</h4>
          <p className="afi-v2-note">
            Fraud labels shown here are synthetic dataset labels. They are not
            labels inferred from public tester transactions.
          </p>
          <div className="afi-v2-split-list">
            {trainingSplits.map((split) => (
              <div className="afi-v2-split" key={split.label}>
                <strong>{split.label}</strong>
                <span>{formatCount(split.rows)} rows</span>
                <small>{formatCount(split.labels)} synthetic fraud labels</small>
              </div>
            ))}
          </div>
          <h4 className="afi-v2-subheading">Fixed test-set metrics</h4>
          <div className="afi-v2-metrics">
            <Metric label="Isolation Forest precision" value={formatMetric(evaluation.precision)} />
            <Metric label="Isolation Forest recall" value={formatMetric(evaluation.recall)} />
            <Metric label="False-positive rate" value={formatMetric(evaluation.false_positive_rate)} />
            <Metric
              label="Hybrid MEDIUM+ precision"
              value={formatMetric(evaluation.hybrid_medium_or_high?.precision)}
            />
            <Metric
              label="Hybrid MEDIUM+ recall"
              value={formatMetric(evaluation.hybrid_medium_or_high?.recall)}
            />
            <Metric
              label="Hybrid HIGH false-positive rate"
              value={formatMetric(evaluation.hybrid_high?.false_positive_rate)}
            />
          </div>
          <p className="afi-v2-note">
            These metrics describe the saved offline evaluation only. Live
            traffic has no verified fraud labels and is not included in them.
          </p>
        </article>
      </div>

      <div className="afi-v2-grid">
        <article className="afi-v2-card">
          <h4>Is the model learning from live transactions?</h4>
          <div className="afi-v2-learning-status">
            <strong>No automatic retraining is active.</strong>
            <p>
              Live transactions update account behavior history and monitoring
              metrics. They do not update model weights or thresholds.
            </p>
          </div>
          <div className="afi-v2-metrics">
            <Metric label="Resolved fraud-confirmed cases" value={formatCount(feedback.fraud_confirmed)} />
            <Metric label="Resolved legitimate cases" value={formatCount(feedback.legitimate)} />
          </div>
          <p className="afi-v2-note">
            These reviewed, selected-case outcomes are candidate training
            feedback only. They are not yet used to train the active model;
            reviewed alerts alone are not a representative training set.
          </p>
        </article>

        <article className="afi-v2-card">
          <h4>Model release history</h4>
          {modelHistory.length ? (
            <div className="afi-v2-table-wrap">
              <table className="afi-v2-table">
                <thead>
                  <tr><th>Version</th><th>Created</th><th>Rows</th><th>Precision</th><th>Recall</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {modelHistory.map((version) => (
                    <tr key={`${version.version}-${version.created_at}`}>
                      <td>{version.version}</td>
                      <td>{formatUpdated(version.created_at)}</td>
                      <td>{formatCount(version.samples_count)}</td>
                      <td>{formatMetric(version.precision_score)}</td>
                      <td>{formatMetric(version.recall_score)}</td>
                      <td>{version.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="afi-v2-note">
              No model release history is recorded in this deployment yet. A
              real improvement trend can be shown after separately evaluated
              artifacts are registered; no trend is fabricated from baseline
              captures.
            </p>
          )}
        </article>
      </div>

      <div className="afi-v2-section-heading">
        <div>
          <h3>Live behavior monitoring</h3>
          <p>Account history and signal rates help analysts see what is changing.</p>
        </div>
        <span className="afi-v2-count">
          {formatCount(data.step_up_pending)} awaiting step-up
        </span>
      </div>

      <div className="afi-v2-grid">
        <article className="afi-v2-card">
          <h4>Distribution drift</h4>
          <DriftList drift={data.drift} />
          <p className="afi-v2-note">
            PSI compares incoming transaction and risk-score distributions with
            a reference sample. Drift signals a need to investigate, not a
            request to retrain automatically.
          </p>
        </article>
        <article className="afi-v2-card">
          <h4>Observed risk signals</h4>
          <p className="afi-v2-note">
            Percent of assessed transactions containing each signal. SIM-swap
            counts represent injected test scenarios, not carrier data.
          </p>
          <div className="afi-v2-rate-grid">
            {[
              ["New device", rates.device_change_rate],
              ["New recipient", rates.recipient_novel_rate],
              ["Network mismatch", rates.ip_mismatch_rate],
              ["Location mismatch", rates.location_mismatch_rate],
              ["Impossible travel", rates.impossible_travel_rate],
              ["Velocity spike", rates.velocity_rate],
              ["Amount deviation", rates.amount_deviation_rate],
              ["Unusual hour", rates.unusual_hour_rate],
              ["New account", rates.new_account_rate],
              ["Limited history", rates.cold_start_rate],
              ["SIM-swap test", rates.sim_swap_rate],
            ].map(([label, rate]) => (
              <div className="afi-v2-rate" key={label}>
                <span>{label}</span>
                <strong>{formatMetric(rate, 2)}</strong>
              </div>
            ))}
          </div>
        </article>
      </div>

      <article className="afi-v2-card afi-v2-activity">
        <div className="afi-v2-section-heading">
          <div>
            <h3>Recent risk decisions</h3>
            <p>Includes completed, blocked, pending, and email step-up transactions.</p>
          </div>
          <span className="afi-v2-count">{formatCount(data.today_transactions_count)} today</span>
        </div>
        {transactions.length ? (
          <div className="afi-v2-table-wrap">
            <table className="afi-v2-table">
              <thead>
                <tr>
                  <th>Time</th><th>Reference</th><th>Sender</th><th>Recipient</th>
                  <th>Amount</th><th>Risk</th><th>Status</th><th>Risk signals</th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((transaction) => {
                  const reasons = Array.isArray(transaction.reasons)
                    ? transaction.reasons.filter(
                      (reason) => !String(reason).startsWith("IP:") && !String(reason).startsWith("Device:"),
                    )
                    : [];
                  return (
                    <tr key={`${transaction.id}-${transaction.transaction_reference}`}>
                      <td>{formatUpdated(transaction.created_at)}</td>
                      <td className="afi-v2-mono">{transaction.transaction_reference}</td>
                      <td>{transaction.origin_account || "—"}</td>
                      <td>{transaction.destination_account || "—"}</td>
                      <td>{Number(transaction.amount || 0).toFixed(2)}</td>
                      <td>
                        {transaction.risk_level || "—"}
                        {transaction.risk_score != null
                          ? ` · ${Number(transaction.risk_score).toFixed(2)}`
                          : ""}
                      </td>
                      <td><span className="afi-v2-status">{transaction.status}</span></td>
                      <td>
                        <RiskSignals signals={transaction.risk_signals} />
                        {reasons.length > 0 && (
                          <small className="afi-v2-reasons">{reasons.join(" · ")}</small>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="afi-v2-note">
            No scored transactions yet. Complete a sandbox transaction to
            populate this view.
          </p>
        )}
      </article>

      <p className="afi-v2-disclaimer">
        Research prototype: synthetic test metrics, customer-history signals,
        and analyst-reviewed outcomes are not evidence of production fraud
        detection effectiveness or regulatory compliance.
      </p>
    </section>
  );
}
