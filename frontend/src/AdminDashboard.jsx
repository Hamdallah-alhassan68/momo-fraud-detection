
import { useCallback, useEffect, useRef, useState } from "react";
import { adminFetch, getCurrentUser, subscribeAdminEvents } from "./adminApi.js";
import { formatDeviceDisplayName, formatLocationDisplayName } from "./deviceInfo.js";
import { describeRiskSignal } from "./riskSignals.js";
import "./AdminDashboard.css";

function PhoneIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
    </svg>
  );
}

function MessageSquareIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function CheckCircleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}

function getCaseStatusPresentation(status) {
  switch (status) {
    case "OPEN":
      return { label: "Not started", className: "not-started" };
    case "INVESTIGATING":
      return { label: "Investigating", className: "investigating" };
    case "RESOLVED":
      return { label: "Resolved", className: "investigated" };
    default:
      return { label: status || "Unknown", className: "unknown" };
  }
}

function getFreezeReasonLabel(reason) {
  if (reason === "ADMIN_MANUAL") {
    return "Manual administrator freeze (not a risk signal)";
  }
  if (reason === "FROZEN_BY_CUSTOMER") return "Security freeze requested by customer";
  const caseMatch = /^ADMIN_FRAUD_CASE_(\d+)$/.exec(reason || "");
  if (caseMatch) {
    return `Fraud-confirmed administrator freeze (case ${caseMatch[1]})`;
  }
  return reason || "Not recorded";
}

function getLocationLabel(location, latitude, longitude) {
  const mappedArea = formatLocationDisplayName(location);
  if (mappedArea !== "Location not captured") {
    return `Nearby mapped area (approximate): ${mappedArea}`;
  }
  if (latitude != null && longitude != null) {
    return "Mapped address unavailable";
  }
  return "Location not captured";
}

function getLocationAccuracyLabel(accuracy) {
  if (accuracy == null || accuracy === "") {
    return "Device-reported accuracy unavailable";
  }
  const meters = Number(accuracy);
  return Number.isFinite(meters) && meters >= 0
    ? `Device-reported accuracy ±${Math.round(meters)} m`
    : "Device-reported accuracy unavailable";
}

function getLocationMapUrl(latitude, longitude) {
  return `https://www.openstreetmap.org/?mlat=${encodeURIComponent(latitude)}&mlon=${encodeURIComponent(longitude)}#map=17/${encodeURIComponent(latitude)}/${encodeURIComponent(longitude)}`;
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
  const [unfreezeRequests, setUnfreezeRequests] = useState([]);
  const [unfreezeRequestsLoading, setUnfreezeRequestsLoading] = useState(true);
  const [unfreezeRequestsError, setUnfreezeRequestsError] = useState("");
  const [complaints, setComplaints] = useState([]);
  const [complaintsLoading, setComplaintsLoading] = useState(true);
  const [complaintsError, setComplaintsError] = useState("");
  const [complaintNotification, setComplaintNotification] = useState(null);
  const [accountControlTab, setAccountControlTab] = useState("frozen");
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

  const loadUnfreezeRequests = useCallback(async () => {
    if (!isAdmin) {
      setUnfreezeRequestsLoading(false);
      return;
    }

    setUnfreezeRequestsLoading(true);
    setUnfreezeRequestsError("");
    try {
      const response = await adminFetch("/api/admin/account-unfreeze-requests");
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Unable to load customer requests.");
      }
      setUnfreezeRequests(data.requests || []);
    } catch (err) {
      setUnfreezeRequestsError(err.message || "Could not load customer requests.");
    } finally {
      setUnfreezeRequestsLoading(false);
    }
  }, [isAdmin]);

  const loadComplaints = useCallback(async () => {
    setComplaintsLoading(true);
    setComplaintsError("");
    try {
      const response = await adminFetch("/api/admin/customer-complaints");
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Unable to load customer complaints.");
      }
      setComplaints(data.complaints || []);
    } catch (err) {
      setComplaintsError(err.message || "Could not load customer complaints.");
    } finally {
      setComplaintsLoading(false);
    }
  }, []);

  async function resolveComplaint(complaintId) {
    try {
      const response = await adminFetch(`/api/admin/customer-complaints/${complaintId}/resolve`, {
        method: "POST",
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Unable to resolve complaint.");
      }
      await loadComplaints();
      setMessage(`Complaint #${complaintId} marked as reviewed.`);
    } catch (err) {
      setError(err.message || "Failed to resolve complaint.");
    }
  }

  useEffect(() => {
    loadCases();
    loadAlerts();
    loadAccounts();
    loadUnfreezeRequests();
    loadComplaints();

    const unsubscribe = subscribeAdminEvents((payload) => {
      if (payload.event === "case") loadCases();
      if (payload.event === "stats") loadAccounts();
      if (payload.event === "audit") loadUnfreezeRequests();
      if (payload.event === "complaint") {
        loadComplaints();
        if (payload.phone_number || payload.message) {
          setComplaintNotification(
            `New ${payload.channel === "CALL" ? "Phone Call Request" : "Complaint Message"} from ${payload.phone_number || payload.customer_ref || "customer"}: "${(payload.message || "").slice(0, 75)}"`
          );
        }
      }
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
  }, [loadUnfreezeRequests, loadComplaints]);

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
    if (!selectedCase || isViewer) return;
    const decision = selectedCase.analyst_decision || "LEGITIMATE";

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
            case_status: "RESOLVED",
            analyst_decision: decision,
            analyst_notes: selectedCase.analyst_notes || "Resolved by analyst.",
          }),
        }
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.error || "Unable to complete investigation.");
      }

      setSelectedCase({
        ...selectedCase,
        case_status: "RESOLVED",
        analyst_decision: decision,
      });
      setReviewSaved(true);
      await loadCases();
      setMessage("Case marked as RESOLVED and investigation complete.");
      window.dispatchEvent(new CustomEvent("momo:stats-refresh"));
    } catch (err) {
      setError(err.message || "Unable to complete investigation.");
    } finally {
      setSaving(false);
    }
  }

  async function resolveCase(caseId, decision = "LEGITIMATE") {
    if (isViewer) {
      setError("Viewer accounts cannot resolve cases. Sign in as Admin or Analyst.");
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");

    try {
      let response = await adminFetch(`/api/admin/fraud-cases/${caseId}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          analyst_decision: decision,
          analyst_notes: `Resolved as ${decision} by analyst.`,
        }),
      });

      if (!response.ok) {
        // Fallback to PATCH if POST endpoint is unavailable
        response = await adminFetch(`/api/admin/fraud-cases/${caseId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            case_status: "RESOLVED",
            analyst_decision: decision,
            analyst_notes: `Resolved as ${decision} by analyst.`,
          }),
        });
      }

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.error || "Unable to resolve case.");
      }

      const now = new Date().toISOString();
      setCases((prev) =>
        prev.map((c) =>
          c.case_id === caseId
            ? {
                ...c,
                case_status: "RESOLVED",
                analyst_decision: decision,
                resolved_at: now,
              }
            : c
        )
      );

      if (selectedCase && selectedCase.case_id === caseId) {
        setSelectedCase((prev) => ({
          ...prev,
          case_status: "RESOLVED",
          analyst_decision: decision,
          resolved_at: now,
        }));
        setReviewSaved(true);
      }

      setMessage(`Case #${caseId} resolved as ${decision === "LEGITIMATE" ? "Legitimate" : "Fraud Confirmed"}.`);
      window.dispatchEvent(new CustomEvent("momo:stats-refresh"));
    } catch (err) {
      setError(err.message || "Unable to resolve case.");
    } finally {
      setSaving(false);
    }
  }

  async function reopenCase(caseId) {
    if (isViewer) return;
    setSaving(true);
    setError("");
    setMessage("");

    try {
      let response = await adminFetch(`/api/admin/fraud-cases/${caseId}/reopen`, {
        method: "POST",
      });

      if (!response.ok) {
        response = await adminFetch(`/api/admin/fraud-cases/${caseId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            case_status: "INVESTIGATING",
          }),
        });
      }

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.error || "Unable to reopen case.");
      }

      setCases((prev) =>
        prev.map((c) =>
          c.case_id === caseId
            ? {
                ...c,
                case_status: "INVESTIGATING",
              }
            : c
        )
      );

      if (selectedCase && selectedCase.case_id === caseId) {
        setSelectedCase((prev) => ({
          ...prev,
          case_status: "INVESTIGATING",
        }));
        setReviewSaved(false);
      }

      setMessage(`Case #${caseId} reopened for investigation.`);
      window.dispatchEvent(new CustomEvent("momo:stats-refresh"));
    } catch (err) {
      setError(err.message || "Unable to reopen case.");
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
        case_status: "RESOLVED",
      }));
      await Promise.all([loadCases(), loadAccounts(), loadUnfreezeRequests()]);
      setMessage(
        `${data.customer_ref} is unfrozen and linked investigation case(s) automatically resolved.`
      );
      window.dispatchEvent(new CustomEvent("momo:stats-refresh"));
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

      await Promise.all([loadAccounts(), loadCases(), loadUnfreezeRequests()]);
      setSelectedCase((current) =>
        current?.customer_ref === customerRef
          ? {
              ...current,
              account_frozen: false,
              account_freeze_reason: null,
              case_status: "RESOLVED",
            }
          : current
      );
      setMessage(
        `${data.customer_ref} is unfrozen and linked investigation case(s) automatically resolved.`
      );
      window.dispatchEvent(new CustomEvent("momo:stats-refresh"));
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
            loadComplaints();
          }}
        >
          Refresh dashboard
        </button>
      </header>

      {complaintNotification && (
        <div className="complaint-alert-toast" role="alert">
          <div className="toast-content">
            <span className="toast-icon">🚨</span>
            <div>
              <strong>Customer Action Required:</strong> {complaintNotification}
            </div>
          </div>
          <button
            type="button"
            className="toast-dismiss-btn"
            onClick={() => setComplaintNotification(null)}
          >
            Dismiss
          </button>
        </div>
      )}

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
            <th>Location</th>
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
              <td className="device-cell" title={alert.device_id}>
                {formatDeviceDisplayName(alert.device_id, alert.device_label)}
              </td>
              <td className="location-cell">
                <span>
                  {getLocationLabel(
                    alert.location,
                    alert.location_latitude,
                    alert.location_longitude,
                  )}
                </span>
                {alert.location_latitude != null && alert.location_longitude != null && (
                  <small className="location-coords mono">
                    <a
                      href={getLocationMapUrl(
                        alert.location_latitude,
                        alert.location_longitude,
                      )}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {Number(alert.location_latitude).toFixed(6)}°,{" "}
                      {Number(alert.location_longitude).toFixed(6)}°
                    </a>
                    <br />
                    {getLocationAccuracyLabel(alert.location_accuracy_meters)}
                  </small>
                )}
              </td>
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

{/* CUSTOMER COMPLAINTS & INQUIRIES SECTION */}
<section id="customer-complaints-section" className="cases-panel complaints-panel">
  <div className="panel-heading">
    <div>
      <h2>Customer Complaints &amp; Inquiries</h2>
      <p>
        Live feed of dispute messages and call requests from customers whose transactions were blocked.
      </p>
    </div>
    <strong>
      {complaints.filter((c) => c.status === "NEW").length} new · {complaints.length} total
    </strong>
  </div>

  {complaintsError && <p className="dashboard-error">{complaintsError}</p>}
  {complaintsLoading ? (
    <p className="empty-state">Loading customer complaints...</p>
  ) : complaints.length === 0 ? (
    <p className="empty-state">No customer complaints or call requests have been submitted.</p>
  ) : (
    <div className="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Customer Phone</th>
            <th>Channel</th>
            <th>Transaction Ref</th>
            <th>Complaint Message</th>
            <th>Time</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {complaints.map((c) => (
            <tr key={c.complaint_id} className={c.status === "NEW" ? "complaint-row-new" : ""}>
              <td>#{c.complaint_id}</td>
              <td>
                <strong className="mono customer-phone-highlight">
                  {c.phone_number || c.customer_ref}
                </strong>
              </td>
              <td>
                <span className={`channel-badge ${c.channel === "CALL" ? "channel-call" : "channel-message"}`}>
                  <span className="complaint-channel-pill">
                    {c.channel === "CALL" ? (
                      <>
                        <PhoneIcon />
                        <span>CALL REQUEST</span>
                      </>
                    ) : (
                      <>
                        <MessageSquareIcon />
                        <span>MESSAGE</span>
                      </>
                    )}
                  </span>
                </span>
              </td>
              <td className="mono">{c.transaction_reference || "—"}</td>
              <td className="complaint-message-cell">
                <div className="complaint-message-text">{c.message}</div>
              </td>
              <td>{c.created_at ? new Date(c.created_at).toLocaleString() : "—"}</td>
              <td>
                <span className={`case-status-badge ${c.status === "NEW" ? "not-started" : "investigated"}`}>
                  {c.status}
                </span>
              </td>
              <td>
                {c.status === "NEW" && isAdmin ? (
                  <button
                    type="button"
                    className="review-button"
                    onClick={() => resolveComplaint(c.complaint_id)}
                  >
                    Mark Reviewed
                  </button>
                ) : c.status === "NEW" ? (
                  <span className="text-muted text-sm">New</span>
                ) : (
                  <span className="text-muted text-sm">Reviewed</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )}
</section>

{isAdmin && (
  <section className="cases-panel unfreeze-requests-panel">
    <div className="panel-heading">
      <div>
        <h2>Customer unfreeze requests</h2>
        <p>Review the customer’s message and wallet freeze before taking action.</p>
      </div>
      <strong>
        {unfreezeRequests.filter((request) => request.pending).length} pending
      </strong>
    </div>

    {unfreezeRequestsError && (
      <p className="dashboard-error">{unfreezeRequestsError}</p>
    )}
    {unfreezeRequestsLoading ? (
      <p className="empty-state">Loading customer requests...</p>
    ) : unfreezeRequests.length === 0 ? (
      <p className="empty-state">No customer unfreeze requests have been submitted.</p>
    ) : (
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Customer</th>
              <th>Freeze reason</th>
              <th>Customer message</th>
              <th>Requested</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {unfreezeRequests.map((request) => (
              <tr key={request.id}>
                <td className="mono">{request.customer_ref}</td>
                <td>
                  {getFreezeReasonLabel(
                    request.current_freeze_reason || request.freeze_reason,
                  )}
                </td>
                <td className="unfreeze-request-message">
                  {request.message || "No message provided"}
                </td>
                <td>
                  {request.created_at
                    ? new Date(request.created_at).toLocaleString()
                    : "—"}
                </td>
                <td>{request.pending ? "Pending review" : "No longer pending"}</td>
                <td>
                  {request.pending && (
                    <button
                      type="button"
                      className="unfreeze-account-button account-list-unfreeze"
                      onClick={() =>
                        unfreezeCustomerAccount(request.customer_ref)
                      }
                      disabled={Boolean(updatingCustomer)}
                    >
                      {updatingCustomer === request.customer_ref
                        ? "Updating..."
                        : "Unfreeze account"}
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
)}

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
          Wallet freeze/unfreeze actions require Administrator privileges (current role: {role}).
        </div>
      )}
    </div>
    <strong>
      {accounts.filter((account) => account.account_frozen).length} frozen ·{" "}
      {accounts.length} wallets
    </strong>
  </div>

  <div className="controls-tab-row">
    <button
      type="button"
      className={`controls-tab-btn ${accountControlTab === "frozen" ? "active" : ""}`}
      onClick={() => setAccountControlTab("frozen")}
    >
      Frozen Wallets ({accounts.filter((a) => a.account_frozen).length})
    </button>
    <button
      type="button"
      className={`controls-tab-btn ${accountControlTab === "all" ? "active" : ""}`}
      onClick={() => setAccountControlTab("all")}
    >
      All Demo Wallets ({accounts.length})
    </button>
  </div>

  {accountsError && <p className="dashboard-error">{accountsError}</p>}
  {accountsLoading ? (
    <p className="empty-state">Loading demo accounts...</p>
  ) : (accountControlTab === "frozen" ? accounts.filter((a) => a.account_frozen) : accounts).length === 0 ? (
    <p className="empty-state">
      {accountControlTab === "frozen"
        ? "✅ No wallets are currently frozen under Admin controls. All customer wallets active."
        : "No demo accounts are available."}
    </p>
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
          {(accountControlTab === "frozen" ? accounts.filter((a) => a.account_frozen) : accounts).map((account) => (
            <tr key={account.customer_ref}>
              <td>{account.name}</td>
              <td className="mono">{account.customer_ref}</td>
              <td>
                {account.account_frozen
                  ? getFreezeReasonLabel(account.reason)
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
    <strong>{activeCaseCount} active · {cases.length - activeCaseCount} resolved</strong>
  </div>

  <div className="case-status-legend" aria-label="Case status guide">
    <span><i className="not-started" />Not started</span>
    <span><i className="investigating" />Open / investigating</span>
    <span><i className="investigated" />Resolved</span>
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
            <th>Location</th>
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
              <td className="device-cell" title={caseItem.device_id}>
                {formatDeviceDisplayName(caseItem.device_id, caseItem.device_label)}
              </td>
              <td className="location-cell">
                <span>
                  {getLocationLabel(
                    caseItem.location,
                    caseItem.location_latitude,
                    caseItem.location_longitude,
                  )}
                </span>
                {caseItem.location_latitude != null && caseItem.location_longitude != null && (
                  <small className="location-coords mono">
                    <a
                      href={getLocationMapUrl(
                        caseItem.location_latitude,
                        caseItem.location_longitude,
                      )}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {Number(caseItem.location_latitude).toFixed(6)}°,{" "}
                      {Number(caseItem.location_longitude).toFixed(6)}°
                    </a>
                    <br />
                    {getLocationAccuracyLabel(caseItem.location_accuracy_meters)}
                  </small>
                )}
              </td>
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
                <div className="table-actions-cluster">
                  <button
                    type="button"
                    className="review-button"
                    onClick={() => openCase(caseItem)}
                  >
                    Review
                  </button>
                  {caseItem.case_status !== "RESOLVED" && !isViewer && (
                    <button
                      type="button"
                      className="review-button resolve-case-quick-btn"
                      onClick={() => resolveCase(caseItem.case_id, "LEGITIMATE")}
                      title="Quick resolve as legitimate transfer"
                    >
                      Resolve
                    </button>
                  )}
                </div>
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
              <strong className="device-cell" title={selectedCase.device_id}>
                {formatDeviceDisplayName(selectedCase.device_id, selectedCase.device_label)}
              </strong>
            </div>
            <div>
              <span>Customer Location</span>
              <strong className="location-cell">
                {getLocationLabel(
                  selectedCase.location,
                  selectedCase.location_latitude,
                  selectedCase.location_longitude,
                )}
              </strong>
              {selectedCase.location_latitude != null && selectedCase.location_longitude != null && (
                <small className="location-coords mono">
                  {Number(selectedCase.location_latitude).toFixed(6)}°, {Number(selectedCase.location_longitude).toFixed(6)}°{" "}
                  <a
                    href={getLocationMapUrl(
                      selectedCase.location_latitude,
                      selectedCase.location_longitude,
                    )}
                    target="_blank"
                    rel="noreferrer"
                  >
                    (Map)
                  </a>
                  <br />
                  {getLocationAccuracyLabel(
                    selectedCase.location_accuracy_meters,
                  )}
                </small>
              )}
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
                    <PhoneIcon />
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

            {selectedCase.case_status === "RESOLVED" ? (
              <div className="case-resolved-banner">
                <div>
                  <strong>Case Resolved ({selectedCase.analyst_decision || "LEGITIMATE"})</strong>
                  <span>Investigation complete. This case is closed.</span>
                </div>
                {!isViewer && (
                  <button
                    type="button"
                    className="reopen-case-btn"
                    disabled={saving}
                    onClick={() => reopenCase(selectedCase.case_id)}
                  >
                    Reopen Case
                  </button>
                )}
              </div>
            ) : (
              <div className="resolve-action-group">
                <button
                  type="button"
                  className="resolve-btn-legitimate"
                  disabled={saving || isViewer}
                  onClick={() => resolveCase(selectedCase.case_id, "LEGITIMATE")}
                  title="Mark transfer as legitimate and complete case"
                >
                  <CheckCircleIcon />
                  Resolve as Legitimate
                </button>
                <button
                  type="button"
                  className="resolve-btn-fraud"
                  disabled={saving || isViewer}
                  onClick={() => resolveCase(selectedCase.case_id, "FRAUD_CONFIRMED")}
                  title="Confirm fraud and complete case"
                >
                  Resolve as Confirmed Fraud
                </button>
              </div>
            )}

            <button
              type="submit"
              className="save-button"
              disabled={saving || isViewer}
              title={isViewer ? "Viewer accounts cannot modify investigation decisions" : undefined}
            >
              {saving ? "Saving..." : isViewer ? "Read-only (viewer)" : "Save draft notes"}
            </button>
            <p className="completion-note">
              Resolving closes the investigation; use the Freeze button if the account must be locked.
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
