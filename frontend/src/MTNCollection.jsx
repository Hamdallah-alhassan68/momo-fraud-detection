import { useEffect, useState } from "react";
import { adminFetch } from "./adminApi.js";
import "./MTNCollection.css";

async function readResponse(response) {
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "MTN Collection request failed.");
  }
  return data;
}

export default function MTNCollection() {
  const [payerMsisdn, setPayerMsisdn] = useState("");
  const [amount, setAmount] = useState("");
  const [assessment, setAssessment] = useState(null);
  const [payment, setPayment] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [productionConfirmed, setProductionConfirmed] = useState(false);
  const [configuration, setConfiguration] = useState({
    loading: true,
    configured: false,
    missingVariables: [],
    environment: "production",
    currency: "GHS",
    maximumAmount: "5.00",
    error: "",
  });

  useEffect(() => {
    let active = true;
    adminFetch("/api/momo/mtn/collection/configuration")
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || "Could not check MTN backend configuration.");
        }
        if (active) {
          setConfiguration({
            loading: false,
            configured: data.configured,
            missingVariables: data.missing_variables || [],
            environment: "production",
            currency: "GHS",
            maximumAmount: "5.00",
            error: "",
          });
        }
      })
      .catch((requestError) => {
        if (active) {
          setConfiguration({
            loading: false,
            configured: false,
            missingVariables: [],
            environment: "production",
            currency: "GHS",
            maximumAmount: "5.00",
            error: requestError.message || "Could not check MTN backend configuration.",
          });
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const transactionPayload = {
    payer_msisdn: payerMsisdn,
    amount,
  };

  async function assessTransaction(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setAssessment(null);
    setPayment(null);
    setProductionConfirmed(false);

    try {
      const response = await adminFetch(
        "/api/momo/mtn/collection/assess",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(transactionPayload),
        },
      );
      setAssessment(await readResponse(response));
    } catch (requestError) {
      setError(requestError.message || "Could not assess this transaction.");
    } finally {
      setLoading(false);
    }
  }

  async function requestPayment() {
    if (!assessment || assessment.status === "BLOCKED_BY_FRAUD_MODEL") return;
    setLoading(true);
    setError("");
    const requestPayload = {
      ...transactionPayload,
      transaction_reference: assessment.transaction_reference,
      production_confirmed: productionConfirmed,
    };

    try {
      const response = await adminFetch(
        "/api/momo/mtn/collection/request-to-pay",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestPayload),
        },
      );
      setPayment(await readResponse(response));
    } catch (requestError) {
      setError(requestError.message || "Could not reach MTN Collection.");
    } finally {
      setLoading(false);
    }
  }

  async function checkStatus() {
    if (!payment?.mtn_reference_id) return;
    setLoading(true);
    setError("");

    try {
      const response = await adminFetch(
        `/api/momo/mtn/collection/request-to-pay/${encodeURIComponent(
          payment.mtn_reference_id
        )}`,
      );
      const status = await readResponse(response);
      setPayment((current) => ({ ...current, ...status }));
    } catch (requestError) {
      setError(requestError.message || "Could not retrieve payment status.");
    } finally {
      setLoading(false);
    }
  }

  function startNewRequest() {
    setAssessment(null);
    setPayment(null);
    setError("");
    setProductionConfirmed(false);
    setAmount("");
  }

  function editPaymentDetails() {
    setAssessment(null);
    setPayment(null);
    setError("");
    setProductionConfirmed(false);
  }

  async function checkConfiguration() {
    setConfiguration((current) => ({ ...current, loading: true, error: "" }));
    try {
      const response = await adminFetch("/api/momo/mtn/collection/configuration");
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Could not check MTN backend configuration.");
      }
      setConfiguration({
        loading: false,
        configured: data.configured,
        missingVariables: data.missing_variables || [],
        environment: "production",
        currency: "GHS",
        maximumAmount: "5.00",
        error: "",
      });
    } catch (requestError) {
      setConfiguration({
        loading: false,
        configured: false,
        missingVariables: [],
        environment: "production",
        currency: "GHS",
        maximumAmount: "5.00",
        error: requestError.message || "Could not check MTN backend configuration.",
      });
    }
  }

  const displayedAssessment =
    payment?.risk_assessment || assessment?.risk_assessment;
  return (
    <main className="mtn-sandbox-page">
      <header className="mtn-sandbox-header">
        <p className="eyebrow">MTN MOBILE MONEY · GHANA PRODUCTION</p>
        <h1>MTN Collection</h1>
        <p>
          Request a Ghana cedi Collection payment from your own MoMo wallet,
          after the risk review.
        </p>
      </header>

      <section className="mtn-sandbox-card">
        <div
          className={`mtn-configuration-status ${
            configuration.configured ? "is-configured" : "is-unconfigured"
          }`}
          role="status"
        >
          <strong>
            {configuration.loading
              ? "Checking backend configuration…"
              : configuration.configured
                ? "MTN Ghana production credentials are configured on this backend."
                : "MTN Ghana production credentials are not configured on this backend."}
          </strong>
          {configuration.missingVariables.length > 0 && (
            <span>
              Missing variable names: {configuration.missingVariables.join(", ")}
            </span>
          )}
          {configuration.error && <span>{configuration.error}</span>}
          {!configuration.configured && !configuration.loading && (
            <span>
              Configure the variables in the backend process, then restart
              the backend and refresh this check.
            </span>
          )}
          <button
            type="button"
            className="mtn-configuration-refresh"
            onClick={checkConfiguration}
            disabled={configuration.loading}
          >
            Recheck backend
          </button>
        </div>

        <div className="mtn-sandbox-card-heading">
          <div>
            <p className="mtn-sandbox-kicker">GHANA PRODUCTION · LIVE PAYMENTS</p>
            <h2>Request a payment</h2>
          </div>
          <span className="mtn-sandbox-currency">{configuration.currency}</span>
        </div>

        <div className="mtn-sandbox-notice" role="note">
          This uses MTN’s <strong>Ghana production</strong> Collection API and
          can move real money if you approve the prompt on your own wallet. The
          backend enforces a maximum request of{" "}
          <strong>GHS {configuration.maximumAmount}</strong>. Only test with a
          wallet you control. An accepted request starts as{" "}
          <strong>PENDING</strong>; check its status after submitting.
        </div>

        <div className="mtn-sandbox-model-warning" role="note">
          Prototype limitation: this model was trained on synthetic PaySim
          transactions, not MTN {configuration.currency} payments. The score is
          a demonstration of the pre-payment integration and is not calibrated
          for real fraud decisions. Do not rely on it to protect a production
          payment. This page currently receives the entered amount and payer
          number only. Time, frequency, velocity, and amount-pattern signals
          need transaction history; IP/network signals need trusted connection
          telemetry; SIM-swap and device signals need trusted app or carrier
          integrations. Those are not available in this MTN payment flow.
          Recipient history is unavailable and is treated as unknown, not as a
          confirmed new recipient. Manual synthetic overrides are confined to
          the separate simulator.
        </div>

        {!assessment && !payment ? (
          <form className="mtn-sandbox-form" onSubmit={assessTransaction}>
            <h3 className="mtn-step-title">Step 1: Enter payment details</h3>
            <label>
              Your Ghana MoMo wallet number
              <input
                type="tel"
                required
                inputMode="tel"
                autoComplete="off"
                value={payerMsisdn}
                onChange={(event) => setPayerMsisdn(event.target.value)}
                placeholder="e.g. 233241234567"
              />
              <small>
                Enter your own MTN wallet number with Ghana country code 233.
                MTN will send a real payment prompt.
              </small>
            </label>

            <label>
              Amount ({configuration.currency})
              <input
                type="number"
                required
                min="0.01"
                step="0.01"
                max={configuration.maximumAmount}
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="e.g. 1.00"
              />
            </label>

            {error && <p className="mtn-sandbox-error" role="alert">{error}</p>}

            <button type="submit" disabled={loading}>
              {loading ? "Assessing transaction…" : "Step 1: Assess risk"}
            </button>
          </form>
        ) : null}

        {(assessment || payment) && (
          <div className="mtn-payment-result" aria-live="polite">
            {payment && (
              <div className="mtn-payment-status">
                <span>MTN payment status</span>
                <strong className={`status-${payment.status?.toLowerCase() || "unknown"}`}>
                  {payment.status === "BLOCKED_BY_FRAUD_MODEL"
                    ? "BLOCKED BY FRAUD MODEL"
                    : payment.status}
                </strong>
              </div>
            )}
            {payment?.mtn_reference_id && (
              <div className="mtn-payment-reference">
                <span>MTN reference</span>
                <code>{payment.mtn_reference_id}</code>
              </div>
            )}
            {displayedAssessment && (
              <section className="mtn-risk-result">
                <h3>Step 1 result: fraud risk assessment</h3>
                <div className="mtn-risk-summary">
                  <span>
                    Risk:{" "}
                    <strong
                      className={`risk-${displayedAssessment.risk_level.toLowerCase()}`}
                    >
                      {displayedAssessment.risk_level}
                    </strong>
                  </span>
                  <span>
                    Score:{" "}
                    <strong>
                      {Number(displayedAssessment.risk_score).toFixed(4)}
                    </strong>
                  </span>
                </div>
                <p>{displayedAssessment.decision}</p>
                {displayedAssessment.reasons?.length > 0 && (
                  <ul>
                    {displayedAssessment.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                )}
              </section>
            )}
            {(payment || assessment)?.message && (
              <p className="mtn-payment-reason">
                {(payment || assessment).message}
              </p>
            )}
            {assessment && !payment && (
              <p className="mtn-no-payment-yet">
                No request has been sent to MTN yet. Review the risk result
                above before choosing whether to continue.
              </p>
            )}
            {assessment &&
              !payment &&
              configuration.loading && (
                <p className="mtn-continue-reason" role="status">
                  Checking whether the backend is ready to contact MTN…
                </p>
              )}
            {assessment &&
              !payment &&
              !configuration.loading &&
              !configuration.configured && (
                <p className="mtn-continue-reason" role="alert">
                  You can’t continue because MTN {configuration.environment} is
                  not configured on the backend.
                  {configuration.missingVariables.length > 0 && (
                    <>
                      {" "}Missing: {configuration.missingVariables.join(", ")}.
                    </>
                  )}
                  {configuration.error && <> {configuration.error}</>}
                  {" "}Set the variables in the backend process, restart the
                  backend, then select <strong>Recheck backend</strong>.
                </p>
              )}
            {assessment &&
              !payment &&
              assessment.status !== "BLOCKED_BY_FRAUD_MODEL" && (
                <label className="mtn-production-confirmation">
                  <input
                    type="checkbox"
                    checked={productionConfirmed}
                    onChange={(event) =>
                      setProductionConfirmed(event.target.checked)
                    }
                  />
                  <span>
                    I control this wallet and understand that approving MTN’s
                    prompt can transfer real money. I am authorizing a request
                    of no more than GHS {configuration.maximumAmount}.
                  </span>
                </label>
              )}
            {payment?.amount && (
              <div className="mtn-payment-reference">
                <span>Amount</span>
                <strong>
                  {payment.amount} {payment.currency || configuration.currency}
                </strong>
              </div>
            )}
            {payment?.reason && (
              <p className="mtn-payment-reason">{payment.reason}</p>
            )}
            {error && <p className="mtn-sandbox-error" role="alert">{error}</p>}
            <div className="mtn-sandbox-actions">
              {!payment &&
                assessment?.status !== "BLOCKED_BY_FRAUD_MODEL" && (
                  <button
                    type="button"
                    onClick={requestPayment}
                    disabled={
                      loading ||
                      configuration.loading ||
                      !configuration.configured ||
                      !productionConfirmed
                    }
                  >
                    {loading
                      ? "Sending request…"
                      : "Step 2: Send real MTN payment request"}
                  </button>
                )}
              {payment?.mtn_reference_id && (
                <button type="button" onClick={checkStatus} disabled={loading}>
                  {loading ? "Checking…" : "Check payment status"}
                </button>
              )}
              {!payment && assessment && (
                <button
                  type="button"
                  className="mtn-secondary-button"
                  onClick={editPaymentDetails}
                  disabled={loading}
                >
                  Edit payment details
                </button>
              )}
              {payment && (
                <button
                  type="button"
                  className="mtn-secondary-button"
                  onClick={startNewRequest}
                  disabled={loading}
                >
                  Start a new test
                </button>
              )}
            </div>
          </div>
        )}
      </section>

      <p className="mtn-sandbox-footnote">
        API user, API key, and subscription key are read only by the backend;
        never enter them in this page or commit them to source control.
      </p>
    </main>
  );
}
