
import { useEffect, useRef, useState } from "react";
import { adminFetch, getCurrentUser, subscribeAdminEvents } from "./adminApi.js";
import { describeRiskSignal } from "./riskSignals.js";
import "./AdminDashboard.css";

function getCaseStatusPresentation(status) {
  switch (status) {
    case "OPEN":
      return { label: "Not started", className: "not-started" };
    case "INVESTIGATING":
      return { label: "Investigating", className: "investigating" };
    case "RESOLVED":
      return { label: "Investigated", className: "investigated" };
    default:
      return { label: status || "Unknown", className: "unknown" };
  }
}

function AdminDashboard({ currentUser: propUser } = {}) {
  const currentUser = propUser || getCurrentUser();
  const role = currentUser?.role || "admin";
  const isViewer = role === "viewer";
  const isAdmin = role === "admin";

  const [cases, setCases] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState("");
  const [updatingCustomer, setUpdatingCustomer] = useState("");
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertsError, setAlertsError] = useState("");
  const [selectedCase, setSelectedCase] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [contactChannel, setContactChannel] = useState("CALL");
  const [contactOutcome, setContactOutcome] = useState("NO_ANSWER");
  const [reviewSaved, setReviewSaved] = useState(false);
  const [callSimulation, setCallSimulation] = useState("idle");
  const [callSoundMessage, setCallSoundMessage] = useState("");
  const ringtoneTimer = useRef(null);
  const audioContext = useRef(null);
  const activeCaseCount = cases.filter(
    (caseItem) => caseItem.case_status !== "RESOLVED"
  ).length;

  async function loadCases() {
    setLoading(true);
    setError("");

    try {
      const response = await adminFetch("/api/admin/fraud-cases");

      if (!response.ok) {
        throw new Error("Unable to load fraud cases.");
      }

      const data = await response.json();
      setCases(data.cases || []);
    } catch (err) {
      setError(err.message || "Could not connect to the FastAPI service on port 5001.");
    } finally {
      setLoading(false);
    }
  }
  
async function loadAlerts() {
  setAlertsLoading(true);
  setAlertsError("");

  try {
    const response = await adminFetch("/api/admin/fraud-alerts");

    if (!response.ok) {
      throw new Error("Unable to load fraud alerts.");
    }

    const data = await response.json();
    setAlerts(data.alerts || []);
  } catch (err) {
    setAlertsError(err.message || "Could not load fraud alerts.");
  } finally {
    setAlertsLoading(false);
  }
}

  async function loadAccounts() {
    setAccountsLoading(true);
    setAccountsError("");

    try {
      const response = await adminFetch("/api/admin/accounts");
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Unable to load demo accounts.");
      }
      setAccounts(data.accounts || []);
    } catch (err) {
      setAccountsError(err.message || "Could not load demo accounts.");
    } finally {
      setAccountsLoading(false);
    }
  }

  useEffect(() => {
    loadCases();
    loadAccounts();

    const unsubscribe = subscribeAdminEvents((payload) => {
      if (payload.event === "case") loadCases();
      if (payload.event === "stats") loadAccounts();
      if (payload.event === "alert" || payload.event === "transaction") {
        loadAlerts();
        loadCases();
        loadAccounts();
      }
    });
    return () => {
      unsubscribe();
      stopRingtone();
    };
  }, []);

  function stopRingtone() {
    if (ringtoneTimer.current !== null) {
      window.clearInterval(ringtoneTimer.current);
      ringtoneTimer.current = null;
    }
    if (audioContext.current) {
      void audioContext.current.close();
      audioContext.current = null;
    }
  }

  function playRingBurst(context) {
    const startAt = context.currentTime;
    for (const frequency of [440, 480]) {
      const oscillator = context.createOscillator();
      const volume = context.createGain();
      oscillator.frequency.value = frequency;
      volume.gain.setValueAtTime(0.035, startAt);
      volume.gain.exponentialRampToValueAtTime(0.001, startAt + 0.7);
      oscillator.connect(volume);
      volume.connect(context.destination);
      oscillator.start(startAt);
      oscillator.stop(startAt + 0.7);
    }
  }

  async function startSimulatedCall() {
    if (isViewer) return;
    stopRingtone();
    setCallSoundMessage("");
    setCallSimulation("ringing");
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) {
        setCallSoundMessage("Audio is unavailable; the visual call simulation is active.");
        return;
      }
      const context = new AudioContextClass();
      audioContext.current = context;
      await context.resume();
      playRingBurst(context);
      ringtoneTimer.current = window.setInterval(() => {
        if (audioContext.current) playRingBurst(audioContext.current);
      }, 2600);
    } catch {
      stopRingtone();
      setCallSoundMessage("Audio is unavailable; the visual call simulation is active.");
    }
  }

  function answerSimulatedCall() {
    stopRingtone();
    setCallSimulation("answered");
  }

  function closeCaseReview() {
    stopRingtone();
    setCallSimulation("idle");
    setSelectedCase(null);
  }

  function openCase(item) {
    stopRingtone();
    setCallSimulation("idle");
    setSelectedCase({ ...item });
    setContactChannel(item.contact_channel || "CALL");
    setContactOutcome(item.contact_outcome || "NO_ANSWER");
    setReviewSaved(Boolean(item.analyst_decision));
    setMessage("");
    setError("");
  }

  function updateField(field, value) {
    setReviewSaved(false);
    setSelectedCase((current) => ({
      ...current,
      [field]: value,
    }));
  }

  async function saveCase(event) {
    event.preventDefault();

    if (!selectedCase || isViewer) return;

    setSaving(true);
    setError("");
    setMessage("");

    try {
      const response = await adminFetch(
        `/api/admin/fraud-cases/${selectedCase.case_id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            case_status:
              selectedCase.case_status === "RESOLVED"
                ? "RESOLVED"
                : selectedCase.analyst_decision
                  ? "INVESTIGATING"
                  : selectedCase.case_status,
            assigned_to: selectedCase.assigned_to || "",
            ...(selectedCase.analyst_decision
              ? { analyst_decision: selectedCase.analyst_decision }
              : {}),
            analyst_notes: selectedCase.analyst_notes || "",
          }),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.error || "Unable to update case.");
      }

      await loadCases();
      setSelectedCase({
        ...selectedCase,
        case_status: selectedCase.analyst_decision
          ? "INVESTIGATING"
          : selectedCase.case_status,
      });
      setReviewSaved(true);
      setMessage("Investigation decision saved. Mark it complete when your review is finished.");
    } catch (err) {
      setError(err.message || "Unable to save changes.");
    } finally {
      setSaving(false);
    }
  }

  async function completeInvestigation() {
    if (!selectedCase || !selectedCase.analyst_decision || !reviewSaved || isViewer) return;

    setSaving(true);
    setError("");
    setMessage("");

    try {
      const response = await adminFetch(
        `/api/admin/fraud-cases/${selectedCase.case_id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ case_status: "RESOLVED" }),
        }
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.error || "Unable to complete investigation.");
      }

      setSelectedCase({ ...selectedCase, case_status: "RESOLVED" });
      await loadCases();
      setMessage("Investigation marked complete. Account freezing remains a separate action.");
    } catch (err) {
      setError(err.message || "Unable to complete investigation.");
    } finally {
      setSaving(false);
    }
  }

  async function recordContact(event, overrides = {}) {
    event?.preventDefault();
    if (!selectedCase || isViewer) return;
    const channel = overrides.channel || contactChannel;
    const outcome = overrides.outcome || contactOutcome;
    stopRingtone();
    setSaving(true);
    setError("");
    setMessage("");

    try {
      const response = await adminFetch(
        `/api/admin/fraud-cases/${selectedCase.case_id}/contact`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            channel,
            outcome,
          }),
        }
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || "Unable to record contact.");

      setSelectedCase({ ...selectedCase, ...data });
      setReviewSaved(true);
      await loadCases();
      setCallSimulation("complete");
      setMessage("Simulated contact outcome recorded. No real call or SMS was sent.");
    } catch (err) {
      setError(err.message || "Unable to record customer contact.");
    } finally {
      setSaving(false);
    }
  }

  async function freezeAccount() {
    if (!selectedCase || !isAdmin) return;
    setSaving(true);
    setError("");
    setMessage("");

    try {
      const response = await adminFetch(
        `/api/admin/fraud-cases/${selectedCase.case_id}/freeze-account`,
        { method: "POST" }
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || "Unable to freeze account.");

      setSelectedCase((current) => ({
        ...current,
        account_frozen: true,
      }));
      await loadCases();
      setMessage(
        `${data.customer_ref} is frozen. The investigation status is unchanged.`
      );
    } catch (err) {
      setError(err.message || "Unable to freeze customer account.");
    } finally {
      setSaving(false);
    }
  }

  async function unfreezeAccount() {
    if (!selectedCase || !isAdmin) return;
    setSaving(true);
    setError("");
    setMessage("");

    try {
      const response = await adminFetch(
        `/api/admin/fraud-cases/${selectedCase.case_id}/unfreeze-account`,
        { method: "POST" }
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.error || "Unable to unfreeze account.");
      }

      setSelectedCase((current) => ({
        ...current,
        account_frozen: false,
        account_freeze_reason: null,
      }));
      await loadCases();
      setMessage(
        `${data.customer_ref} is unfrozen. The investigation status is unchanged.`
      );
    } catch (err) {
      setError(err.message || "Unable to unfreeze customer account.");
    } finally {
      setSaving(false);
    }
  }

  async function unfreezeCustomerAccount(customerRef) {
    if (!isAdmin) return;
    setUpdatingCustomer(customerRef);
    setError("");
    setMessage("");

    try {
      const response = await adminFetch(
        `/api/admin/accounts/${encodeURIComponent(customerRef)}/unfreeze`,
        { method: "POST" }
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Unable to unfreeze account.");
      }

      await Promise.all([loadAccounts(), loadCases()]);
      setSelectedCase((current) =>
        current?.customer_ref === customerRef
          ? {
              ...current,
              account_frozen: false,
              account_freeze_reason: null,
            }
          : current
      );
      setMessage(`${data.customer_ref} is unfrozen.`);
    } catch (err) {
      setError(err.message || "Unable to unfreeze customer account.");
    } finally {
      setUpdatingCustomer("");
    }
  }

  async function freezeCustomerAccount(customerRef) {
    if (!isAdmin) return;
    const confirmed = window.confirm(
      `Freeze ${customerRef} now? This will block account activity until an admin unfreezes it.`
    );
    if (!confirmed) return;

    setUpdatingCustomer(customerRef);
    setError("");
    setMessage("");

    try {
      const response = await adminFetch(
        `/api/admin/accounts/${encodeURIComponent(customerRef)}/freeze`,
        { method: "POST" }
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Unable to freeze account.");
      }

      await Promise.all([loadAccounts(), loadCases()]);
      setSelectedCase((current) =>
        current?.customer_ref === customerRef
          ? {
              ...current,
              account_frozen: true,
              account_freeze_reason: "ADMIN_MANUAL",
            }
          : current
      );
      setMessage(`${data.customer_ref} is frozen by an admin.`);
    } catch (err) {
      setError(err.message || "Unable to freeze customer account.");
    } finally {
      setUpdatingCustomer("");
    }
  }

  const canFreezeAccount =
    selectedCase?.analyst_decision === "FRAUD_CONFIRMED" &&
    reviewSaved &&
    (selectedCase.risk_level === "HIGH" ||
      selectedCase.contact_outcome === "CONFIRMED");

  return (
    <main className="admin-dashboard">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">GHANA MOBILE MONEY</p>
          <h1>Alerts &amp; investigations</h1>
          <p className="subtitle">
            Triage live signals and manage high-risk transaction cases.
          </p>
        </div>

        <button
          className="refresh-button"
          onClick={() => {
            loadCases();
            loadAlerts();
            loadAccounts();
          }}
        >
          Refresh dashboard
        </button>
      </header>

      {error && <p className="dashboard-error">{error}</p>}
      {message && <p className="dashboard-success">{message}</p>}

      {isViewer && (
        <div className="viewer-role-banner" role="status">
          <div className="viewer-role-banner-title">
            <span className="viewer-role-badge">VIEWER · READ-ONLY</span>
            <strong>Compliance &amp; Monitoring Access Mode</strong>
          </div>
          <p>
            Signed in as <strong>{currentUser?.display_name || currentUser?.username}</strong>. You have read-only access to inspect alerts, review transaction cases, and verify cryptographic audit trails. Updating case decisions, recording customer contact, and freezing wallets are restricted to Analysts and Administrators.
          </p>
        </div>
      )}

     
<section className="cases-panel">
  <div className="panel-heading">
    <div>
      <h2>Fraud alerts</h2>
      <p>Suspicious transactions requiring attention.</p>
    </div>
    <strong>{alerts.length} alerts</strong>
  </div>

  {alertsError && (
    <p className="dashboard-error">{alertsError}</p>
  )}

  {alertsLoading ? (
    <p className="empty-state">Loading fraud alerts...</p>
  ) : alerts.length === 0 ? (
    <p className="empty-state">No fraud alerts found.</p>
  ) : (
    <div className="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Transaction</th>
            <th>Severity</th>
            <th>Reasons</th>
            <th>Status</th>
            <th>IP address</th>
            <th>Device</th>
            <th>Date created</th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((alert) => (
            <tr key={alert.alert_id}>
              <td>{alert.transaction_reference}</td>
              <td>
                <span
                  className={`risk-badge ${
                    alert.severity?.toLowerCase()
                  }`}
                >
                  {alert.severity}
                </span>
              </td>
              <td>
                {Array.isArray(alert.reason)
                  ? alert.reason.join(", ")
                  : alert.reason || "No reason provided"}
              </td>
              <td>{alert.status}</td>
              <td className="mono">{alert.ip_address || "—"}</td>
              <td className="mono">{alert.device_id || "—"}</td>
              <td>
                {alert.created_at
                  ? new Date(alert.created_at).toLocaleString()
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )}
</section>

<section className="cases-panel frozen-accounts-panel">
  <div className="panel-heading">
    <div>
      <h2>Admin account controls</h2>
      <p>
        Freeze or unfreeze any demo wallet directly, without waiting for a call
        or investigation decision. Actions are recorded in the audit log.
      </p>
      {!isAdmin && (
        <div className="role-restriction-note">
          🔒 Wallet freeze/unfreeze actions require Administrator privileges (current role: {role}).
        </div>
      )}
    </div>
    <strong>
      {accounts.filter((account) => account.account_frozen).length} frozen ·{" "}
      {accounts.length} wallets
    </strong>
  </div>

  {accountsError && <p className="dashboard-error">{accountsError}</p>}
  {accountsLoading ? (
    <p className="empty-state">Loading demo accounts...</p>
  ) : accounts.length === 0 ? (
    <p className="empty-state">No demo accounts are available.</p>
  ) : (
    <div className="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Customer</th>
            <th>Wallet</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <tr key={account.customer_ref}>
              <td>{account.name}</td>
              <td className="mono">{account.customer_ref}</td>
              <td>
                {account.account_frozen
                  ? account.reason === "FROZEN_BY_CUSTOMER"
                    ? "Frozen by customer"
                    : "Frozen by admin"
                  : "Active"}
              </td>
              <td>
                {account.account_frozen ? (
                  <button
                    type="button"
                    className="unfreeze-account-button account-list-unfreeze"
                    onClick={() =>
                      unfreezeCustomerAccount(account.customer_ref)
                    }
                    disabled={Boolean(updatingCustomer) || !isAdmin}
                    title={!isAdmin ? "Administrator role required to unfreeze wallets" : undefined}
                  >
                    {updatingCustomer === account.customer_ref
                      ? "Updating..."
                      : !isAdmin
                        ? "Admin required"
                        : "Unfreeze account"}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="freeze-account-button account-list-freeze"
                    onClick={() => freezeCustomerAccount(account.customer_ref)}
                    disabled={Boolean(updatingCustomer) || !isAdmin}
                    title={!isAdmin ? "Administrator role required to freeze wallets" : undefined}
                  >
                    {updatingCustomer === account.customer_ref
                      ? "Updating..."
                      : !isAdmin
                        ? "Admin required"
                        : "Freeze account"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )}
</section>

<section className="cases-panel cases-list">
  <div className="panel-heading">
    <div>
      <h2>Investigation cases</h2>
      <p>High-risk transactions, including completed investigations.</p>
    </div>
    <strong>{activeCaseCount} active · {cases.length - activeCaseCount} investigated</strong>
  </div>

  <div className="case-status-legend" aria-label="Case status guide">
    <span><i className="not-started" />Not started</span>
    <span><i className="investigating" />Open / investigating</span>
    <span><i className="investigated" />Investigated</span>
  </div>

  {loading ? (
    <p className="empty-state">Loading cases…</p>
  ) : cases.length === 0 ? (
    <p className="empty-state">No investigation cases found.</p>
  ) : (
    <div className="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Transaction</th>
            <th>Amount</th>
            <th>Risk</th>
            <th>IP address</th>
            <th>Device</th>
            <th>Account</th>
            <th>Status</th>
            <th>Created</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {cases.map((caseItem) => (
            <tr key={caseItem.case_id}>
              <td>#{caseItem.case_id}</td>
              <td className="mono">{caseItem.transaction_reference}</td>
              <td>GHS {Number(caseItem.amount).toLocaleString()}</td>
              <td>
                <span
                  className={`risk-badge ${
                    caseItem.risk_level?.toLowerCase()
                  }`}
                >
                  {caseItem.risk_level || "—"}
                </span>
              </td>
              <td className="mono">{caseItem.ip_address || "—"}</td>
              <td className="mono">{caseItem.device_id || "—"}</td>
              <td>
                <span
                  className={`account-status-badge ${
                    caseItem.account_frozen ? "frozen" : "active"
                  }`}
                >
                  {caseItem.account_frozen ? "Frozen" : "Active"}
                </span>
              </td>
              <td>
                {(() => {
                  const status = getCaseStatusPresentation(caseItem.case_status);
                  return (
                    <span className={`case-status-badge ${status.className}`}>
                      {status.label}
                    </span>
                  );
                })()}
              </td>
              <td>
                {caseItem.created_at
                  ? new Date(caseItem.created_at).toLocaleString()
                  : "—"}
              </td>
              <td>
                <button
                  type="button"
                  className="review-button"
                  onClick={() => openCase(caseItem)}
                >
                  Review
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )}
</section>

      {selectedCase && (
        <div className="case-overlay" onClick={closeCaseReview}>
        <section
          className="case-editor"
          role="dialog"
          aria-modal="true"
          aria-labelledby="case-review-title"
          onClick={(event) => event.stopPropagation()}
        >
          {error && <p className="dashboard-error">{error}</p>}
          {message && <p className="dashboard-success">{message}</p>}
          <div className="panel-heading">
            <div>
              <p className="eyebrow">ANALYST REVIEW</p>
              <h2 id="case-review-title">Case #{selectedCase.case_id}</h2>
              <p>
                Transaction: {selectedCase.transaction_reference}
              </p>
            </div>

            <button
              type="button"
              className="close-button"
              onClick={closeCaseReview}
            >
              Close
            </button>
          </div>

          <div className="risk-brief">
            <div
              className={`risk-score-orb ${selectedCase.risk_level?.toLowerCase() || "high"}`}
              style={{
                "--risk-progress": `${Math.min(
                  100,
                  Math.max(0, Number(selectedCase.risk_score || 0) * 100)
                )}%`,
              }}
            >
              <span>MODEL RISK</span>
              <strong>{Math.round(Number(selectedCase.risk_score || 0) * 100)}</strong>
              <small>/ 100</small>
            </div>
            <div className="risk-brief-copy">
              <span className={`risk-badge ${selectedCase.risk_level?.toLowerCase() || "high"}`}>
                {selectedCase.risk_level || "HIGH"} RISK
              </span>
              <h3>Why the system escalated this transfer</h3>
              <p>
                These are the signals that contributed to the model decision.
                They are indicators for investigation, not proof of fraud by themselves.
              </p>
            </div>
          </div>

          <div className="evidence-grid">
            {selectedCase.fraud_reasons?.length > 0 ? (
              selectedCase.fraud_reasons.map((reason, index) => (
                <article className="evidence-card" key={`${index}-${reason}`}>
                  <span className="evidence-index">{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <strong>{reason}</strong>
                    <p>{describeRiskSignal(reason)}</p>
                  </div>
                  <span className="evidence-mark">Signal</span>
                </article>
              ))
            ) : (
              <p className="empty-state">No explanatory signals were stored for this case.</p>
            )}
          </div>

          <div className="risk-score-scale">
            <div className="risk-score-scale-heading">
              <strong>Relative model score</strong>
              <span>{Math.round(Number(selectedCase.risk_score || 0) * 100)} / 100</span>
            </div>
            <div
              className={`risk-score-track ${selectedCase.risk_level?.toLowerCase() || "high"}`}
              role="img"
              aria-label={`Relative model risk score ${Math.round(Number(selectedCase.risk_score || 0) * 100)} out of 100`}
            >
              <span
                className="risk-score-marker"
                style={{
                  left: `${Math.min(100, Math.max(0, Number(selectedCase.risk_score || 0) * 100))}%`,
                }}
              />
            </div>
            <div className="risk-score-scale-labels">
              <span>Lower relative score</span>
              <span>Higher relative score</span>
            </div>
            <small>This is a model score, not a probability that fraud occurred.</small>
          </div>

          <div className="transaction-details">
            <div>
              <span>Transaction</span>
              <strong>{selectedCase.transaction_type}</strong>
            </div>
            <div>
              <span>Origin account</span>
              <strong>{selectedCase.origin_account || "Not provided"}</strong>
            </div>
            <div>
              <span>Destination account</span>
              <strong>{selectedCase.destination_account}</strong>
            </div>
            <div>
              <span>Amount</span>
              <strong>GHS {Number(selectedCase.amount).toLocaleString()}</strong>
            </div>
            <div>
              <span>IP address</span>
              <strong className="mono">
                {selectedCase.ip_address || "Not provided"}
              </strong>
            </div>
            <div>
              <span>Device</span>
              <strong className="mono">
                {selectedCase.device_id || "Not provided"}
              </strong>
            </div>
            <div>
              <span>Account status</span>
              <strong
                className={
                  selectedCase.account_frozen
                    ? "account-status-text frozen"
                    : "account-status-text active"
                }
              >
                {selectedCase.account_frozen ? "Frozen" : "Active"}
              </strong>
            </div>
          </div>

          <section className="contact-workflow">
            <div>
              <p className="eyebrow">CUSTOMER CONTACT · SIMULATED</p>
              <h3>Simulate customer contact</h3>
              <p>
                The phone-call demo rings in this browser only; it does not call
                the customer&apos;s phone. Record the simulated result for the
                investigation audit trail. For a confirmed
                high-risk case, contact is encouraged but does not block an
                administrator from freezing the account.
              </p>
            </div>
            <form className="contact-form" onSubmit={recordContact}>
              <label>
                Contact method
                <select
                  value={contactChannel}
                  onChange={(event) => setContactChannel(event.target.value)}
                  disabled={saving || isViewer}
                >
                  <option value="CALL">Phone call (simulated)</option>
                  <option value="SMS">SMS (simulated)</option>
                </select>
              </label>
              <label>
                Customer response
                <select
                  value={contactOutcome}
                  onChange={(event) => setContactOutcome(event.target.value)}
                  disabled={saving || isViewer}
                >
                  <option value="CONFIRMED">Agrees to freeze account</option>
                  <option value="DECLINED">Declines account freeze</option>
                  <option value="NO_ANSWER">No answer / no response</option>
                </select>
              </label>
              <button
                type={contactChannel === "CALL" ? "button" : "submit"}
                className="contact-button"
                disabled={saving || isViewer}
                title={isViewer ? "Viewer accounts cannot initiate simulated customer contact" : undefined}
                onClick={
                  contactChannel === "CALL"
                    ? startSimulatedCall
                    : undefined
                }
              >
                {saving
                  ? "Recording..."
                  : isViewer
                    ? "Contact restricted (viewer)"
                    : contactChannel === "CALL"
                      ? "Start simulated call"
                      : "Record simulated SMS"}
              </button>
            </form>
            {callSimulation !== "idle" && callSimulation !== "complete" && (
              <div
                className="simulated-call-overlay"
                role="dialog"
                aria-modal="true"
                aria-labelledby="simulated-call-title"
              >
                <section className="simulated-call-card">
                  <span className={`simulated-call-icon ${callSimulation}`}>
                    ☎
                  </span>
                  <p className="eyebrow">BROWSER-ONLY DEMONSTRATION</p>
                  <h3 id="simulated-call-title">
                    {callSimulation === "ringing"
                      ? "Outgoing call · Ringing"
                      : "Simulated call connected"}
                  </h3>
                  <p>
                    Demo customer ·{" "}
                    {selectedCase.customer_ref || selectedCase.origin_account}
                  </p>
                  {callSimulation === "ringing" ? (
                    <>
                      <p className="simulated-call-status" aria-live="polite">
                        The demo phone is ringing here. No real phone is being
                        called.
                      </p>
                      {callSoundMessage && (
                        <p className="simulated-call-audio-note">
                          {callSoundMessage}
                        </p>
                      )}
                      <div className="simulated-call-actions">
                        <button
                          type="button"
                          className="call-answer-button"
                          onClick={answerSimulatedCall}
                        >
                          Answer demo call
                        </button>
                        <button
                          type="button"
                          className="call-decline-button"
                          disabled={saving}
                          onClick={() =>
                            recordContact(null, {
                              channel: "CALL",
                              outcome: "NO_ANSWER",
                            })
                          }
                        >
                          Mark no answer
                        </button>
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => {
                            stopRingtone();
                            setCallSimulation("idle");
                          }}
                        >
                          Cancel demo
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <p className="simulated-call-transcript">
                        “This is a simulated fraud-prevention call. A transfer
                        needs review. Do you agree to freeze the account?”
                      </p>
                      <div className="simulated-call-actions">
                        <button
                          type="button"
                          className="call-answer-button"
                          disabled={saving}
                          onClick={() =>
                            recordContact(null, {
                              channel: "CALL",
                              outcome: "CONFIRMED",
                            })
                          }
                        >
                          Agrees to freeze
                        </button>
                        <button
                          type="button"
                          className="call-decline-button"
                          disabled={saving}
                          onClick={() =>
                            recordContact(null, {
                              channel: "CALL",
                              outcome: "DECLINED",
                            })
                          }
                        >
                          Declines
                        </button>
                      </div>
                    </>
                  )}
                </section>
              </div>
            )}
            {(!reviewSaved ||
              selectedCase.analyst_decision !== "FRAUD_CONFIRMED") &&
              !selectedCase.account_frozen &&
              !canFreezeAccount && (
              <p className="freeze-locked">
                This case-linked freeze requires saved fraud confirmation.
                For urgent account-wide action, use Admin account controls above.
              </p>
            )}
            {selectedCase.contact_outcome && (
              <div className={`contact-result ${selectedCase.contact_outcome.toLowerCase()}`}>
                <strong>
                  {selectedCase.contact_channel === "SMS" ? "SMS" : "Call"} ·{" "}
                  {contactOutcomeLabel(selectedCase.contact_outcome)}
                </strong>
                <span>
                  {selectedCase.contact_at
                    ? new Date(selectedCase.contact_at).toLocaleString()
                    : "Recorded"}
                </span>
              </div>
            )}
            {selectedCase.account_frozen ? (
              <button
                type="button"
                className="unfreeze-account-button"
                onClick={unfreezeAccount}
                disabled={saving || !isAdmin}
                title={!isAdmin ? "Administrator role required to unfreeze wallets" : undefined}
              >
                {saving
                  ? "Unfreezing..."
                  : !isAdmin
                    ? "Unfreeze requires admin role"
                    : `Unfreeze ${selectedCase.customer_ref || selectedCase.origin_account}`}
              </button>
            ) : canFreezeAccount ? (
              <button
                type="button"
                className="freeze-account-button"
                onClick={freezeAccount}
                disabled={saving || !isAdmin}
                title={!isAdmin ? "Administrator role required to freeze wallets" : undefined}
              >
                {saving
                  ? "Freezing..."
                  : !isAdmin
                    ? "Freeze requires admin role"
                    : `Freeze ${selectedCase.customer_ref || selectedCase.origin_account}${
                        selectedCase.risk_level === "HIGH" &&
                        selectedCase.contact_outcome !== "CONFIRMED"
                          ? " · high-risk control"
                          : " · confirmed"
                      }`}
              </button>
            ) : selectedCase.analyst_decision === "FRAUD_CONFIRMED" &&
              reviewSaved &&
              selectedCase.risk_level !== "HIGH" &&
              selectedCase.contact_outcome !== "CONFIRMED" ? (
              <p className="freeze-locked">
                This case-linked freeze requires customer consent for a
                non-high-risk case. Use Admin account controls for urgent action.
              </p>
            ) : null}
          </section>

          <form onSubmit={saveCase} className="case-form">
            <label>
              Assigned analyst
              <input
                value={selectedCase.assigned_to || ""}
                onChange={(event) =>
                  updateField("assigned_to", event.target.value)
                }
                placeholder="Enter analyst name"
                disabled={saving || isViewer}
              />
            </label>

            <label>
              Investigation decision
              <select
                value={selectedCase.analyst_decision || ""}
                onChange={(event) =>
                  updateField("analyst_decision", event.target.value)
                }
                disabled={saving || isViewer}
              >
                <option value="">Not decided</option>
                <option value="FRAUD_CONFIRMED">Fraud confirmed</option>
                <option value="LEGITIMATE">Legitimate transaction</option>
                <option value="INCONCLUSIVE">Inconclusive</option>
              </select>
            </label>

            <label>
              Investigation notes
              <textarea
                rows="4"
                value={selectedCase.analyst_notes || ""}
                onChange={(event) =>
                  updateField("analyst_notes", event.target.value)
                }
                placeholder="Record the findings of the investigation"
                disabled={saving || isViewer}
              />
            </label>

            <button
              type="submit"
              className="save-button"
              disabled={saving || isViewer}
              title={isViewer ? "Viewer accounts cannot modify investigation decisions" : undefined}
            >
              {saving ? "Saving..." : isViewer ? "Read-only (viewer)" : "Save investigation"}
            </button>
            <button
              type="button"
              className="complete-investigation-button"
              onClick={completeInvestigation}
              disabled={
                saving ||
                isViewer ||
                !reviewSaved ||
                !selectedCase.analyst_decision ||
                selectedCase.case_status === "RESOLVED"
              }
              title={isViewer ? "Viewer accounts cannot modify case status" : undefined}
            >
              {selectedCase.case_status === "RESOLVED"
                ? "Investigation complete"
                : saving
                  ? "Completing..."
                  : isViewer
                    ? "Complete restricted"
                    : "Mark investigation complete"}
            </button>
            <p className="completion-note">
              Completion closes the investigation; it does not freeze the customer account.
            </p>
          </form>

        </section>
        </div>
      )}
    </main>
  );
}

function contactOutcomeLabel(outcome) {
  return {
    CONFIRMED: "Customer agrees to freeze",
    DECLINED: "Customer declined freeze",
    NO_ANSWER: "No answer / no response",
  }[outcome] || outcome;
}

export default AdminDashboard;
