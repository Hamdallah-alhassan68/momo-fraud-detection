
import { useState } from "react";
import { adminFetch } from "./adminApi.js";
import "./TransactionAssessment.css";

const initialForm = {
  transaction_reference: "",
  transaction_type: "TRANSFER",
  amount: "",
  origin_account: "",
  destination_account: "",
  old_balance_origin: "",
  new_balance_origin: "",
  old_balance_destination: "",
  new_balance_destination: "",
  recipient_new: false,
  device_change: false,
  ip_network_mismatch: false,
  recent_sim_swap: false,
  velocity_spike: false,
};

export default function TransactionAssessment() {
  const [form, setForm] = useState(initialForm);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function assessTransaction(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);

    const payload = {
      ...form,
      amount: Number(form.amount),
      old_balance_origin: Number(form.old_balance_origin || 0),
      new_balance_origin: Number(form.new_balance_origin || 0),
      old_balance_destination: Number(
        form.old_balance_destination || 0
      ),
      new_balance_destination: Number(
        form.new_balance_destination || 0
      ),
    };

    try {
      const response = await adminFetch(
        "/api/transactions/score",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail || data.error || "Transaction assessment failed."
        );
      }

      setResult(data);
    } catch (err) {
      setError(
        err.message ||
          "Could not connect to the fraud detection backend."
      );
    } finally {
      setLoading(false);
    }
  }

  function resetForm() {
    setForm(initialForm);
    setResult(null);
    setError("");
  }

  const riskClass = result?.risk_level?.toLowerCase();

  return (
    <main className="assessment-page">
      <header className="assessment-header">
        <p className="eyebrow">GHANA MOBILE MONEY</p>
        <h1>Transaction Assessment</h1>
        <p>
          Evaluate a simulated transaction using the hybrid
          Isolation Forest and rule-based fraud detection system.
        </p>
      </header>

      <form className="assessment-form" onSubmit={assessTransaction}>
        <section className="assessment-section">
          <h2>Transaction information</h2>

          <div className="assessment-grid">
            <label>
              Transaction reference *
              <input
                required
                value={form.transaction_reference}
                onChange={(e) =>
                  updateField("transaction_reference", e.target.value)
                }
                placeholder="e.g. TXN-DEMO-101"
              />
            </label>

            <label>
              Transaction type *
              <select
                required
                value={form.transaction_type}
                onChange={(e) =>
                  updateField("transaction_type", e.target.value)
                }
              >
                <option value="TRANSFER">Wallet to Wallet</option>
                <option value="CASH_OUT">Wallet to Bank / Cash Out</option>
                <option value="CASH_IN">Bank to Wallet / Cash In</option>
              </select>
            </label>

            <label>
              Amount (GHS) *
              <input
                required
                type="number"
                min="0.01"
                step="0.01"
                value={form.amount}
                onChange={(e) => updateField("amount", e.target.value)}
                placeholder="e.g. 500"
              />
            </label>

            <label>
              Origin wallet *
              <input
                required
                value={form.origin_account}
                onChange={(e) =>
                  updateField("origin_account", e.target.value)
                }
                placeholder="e.g. WALLET-1001"
              />
            </label>

            <label>
              Destination wallet *
              <input
                required
                value={form.destination_account}
                onChange={(e) =>
                  updateField("destination_account", e.target.value)
                }
                placeholder="e.g. WALLET-2001"
              />
            </label>
          </div>
        </section>

        <section className="assessment-section">
          <h2>Account balances (GHS)</h2>
          <p className="section-description">
            Enter the balances immediately before and after the
            simulated transaction.
          </p>

          <div className="assessment-grid">
            <label>
              Origin balance before
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.old_balance_origin}
                onChange={(e) =>
                  updateField("old_balance_origin", e.target.value)
                }
                placeholder="e.g. 1000"
              />
            </label>

            <label>
              Origin balance after
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.new_balance_origin}
                onChange={(e) =>
                  updateField("new_balance_origin", e.target.value)
                }
                placeholder="e.g. 500"
              />
            </label>

            <label>
              Destination balance before
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.old_balance_destination}
                onChange={(e) =>
                  updateField("old_balance_destination", e.target.value)
                }
                placeholder="e.g. 200"
              />
            </label>

            <label>
              Destination balance after
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.new_balance_destination}
                onChange={(e) =>
                  updateField("new_balance_destination", e.target.value)
                }
                placeholder="e.g. 700"
              />
            </label>
          </div>
        </section>

        <section className="assessment-section">
          <h2>Behavioral risk indicators</h2>
          <p className="section-description">
            Select the signals observed in this simulated transaction.
          </p>

          <div className="risk-indicators">
            {[
              ["recipient_new", "New or previously unseen recipient"],
              ["device_change", "New or changed device"],
              ["ip_network_mismatch", "IP or network mismatch"],
              ["recent_sim_swap", "Recent SIM change"],
              ["velocity_spike", "Unusual transaction velocity"],
            ].map(([field, label]) => (
              <label className="indicator-option" key={field}>
                <input
                  type="checkbox"
                  checked={form[field]}
                  onChange={(e) =>
                    updateField(field, e.target.checked)
                  }
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </section>

        {error && (
          <p className="assessment-error" role="alert">
            {error}
          </p>
        )}

        <div className="assessment-actions">
          <button type="submit" disabled={loading}>
            {loading ? "Assessing transaction..." : "Assess transaction"}
          </button>

          <button type="button" className="secondary-button" onClick={resetForm}>
            Clear form
          </button>
        </div>
      </form>

      {result && (
        <section className="assessment-result">
          <div className="result-heading">
            <div>
              <p className="eyebrow">ASSESSMENT RESULT</p>
              <h2>{result.transaction_reference || form.transaction_reference}</h2>
            </div>

            <span className={`risk-badge ${riskClass}`}>
              {result.risk_level}
            </span>
          </div>

          <div className="result-score">
            <span>Hybrid risk score</span>
            <strong>
              {Number(result.risk_score).toFixed(4)}
            </strong>
            <div className="score-track">
              <div
                className={`score-fill ${riskClass}`}
                style={{
                  width: `${Math.max(
                    0,
                    Math.min(100, Number(result.risk_score) * 100)
                  )}%`,
                }}
              />
            </div>
          </div>

          <div className="result-metrics">
            <article>
              <span>Isolation Forest score</span>
              <strong>
                {Number(result.isolation_forest_score).toFixed(4)}
              </strong>
            </article>

            <article>
              <span>Rule-based score</span>
              <strong>{Number(result.rule_score).toFixed(4)}</strong>
            </article>

            <article>
              <span>Recipient history</span>
              <strong>{result.recipient_history ?? "—"}</strong>
            </article>
          </div>

          <div className="recommended-action">
            <h3>Recommended action</h3>
            <p>{result.decision || "No decision returned."}</p>
          </div>

          <div className="result-reasons">
            <h3>Risk indicators detected</h3>
            {result.reasons?.length ? (
              <ul>
                {result.reasons.map((reason, index) => (
                  <li key={`${index}-${reason}`}>{reason}</li>
                ))}
              </ul>
            ) : (
              <p>No rule-based risk indicators were returned.</p>
            )}
          </div>

          <p className="simulation-note">
            This is a prototype assessment. The result does not
            execute a real mobile-money transaction, verification,
            or payment block.
          </p>
        </section>
      )}
    </main>
  );
}