import { useEffect, useState } from "react";
import { adminFetch, subscribeAdminEvents, verifyAuditChain } from "./adminApi.js";
import "./AuditLogs.css";

export default function AuditLogs() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [chainStatus, setChainStatus] = useState(null);
  const [verifying, setVerifying] = useState(false);

  async function loadLogs() {
    try {
      const response = await adminFetch("/api/admin/audit-logs");
      if (!response.ok) {
        throw new Error("Failed to load audit logs.");
      }
      const data = await response.json();
      setLogs(data.logs || []);
    } catch (err) {
      setError(err.message || "Unable to load audit logs.");
    } finally {
      setLoading(false);
    }
  }

  async function checkChainIntegrity() {
    setVerifying(true);
    try {
      const result = await verifyAuditChain();
      setChainStatus(result);
    } catch (err) {
      setChainStatus({ valid: false, error: err.message });
    } finally {
      setVerifying(false);
    }
  }

  useEffect(() => {
    loadLogs();
    checkChainIntegrity();

    const unsubscribe = subscribeAdminEvents((payload) => {
      if (
        payload.event === "audit" ||
        payload.event === "transaction" ||
        payload.event === "case"
      ) {
        loadLogs();
        checkChainIntegrity();
      }
    });
    return unsubscribe;
  }, []);

  function formatDetails(details) {
    if (!details) return "—";
    if (typeof details === "object") {
      return JSON.stringify(details, null, 2);
    }
    try {
      return JSON.stringify(JSON.parse(details), null, 2);
    } catch {
      return String(details);
    }
  }

  function truncateHash(hash) {
    if (!hash || hash === "GENESIS") return hash || "—";
    return `${hash.slice(0, 8)}…${hash.slice(-6)}`;
  }

  if (loading) return <p className="audit-loading">Loading audit logs...</p>;

  if (error) {
    return <p className="audit-error" role="alert">{error}</p>;
  }

  return (
    <section className="audit-logs">
      <div className="audit-header-row">
        <div>
          <h2>Cryptographic Audit Trail</h2>
          <p>
            Tamper-evident, append-only operational log secured by sequential SHA-256
            hash chaining and server HMAC signatures.
          </p>
        </div>
        <button
          type="button"
          className="audit-verify-btn"
          disabled={verifying}
          onClick={checkChainIntegrity}
        >
          {verifying ? "Verifying chain…" : "Verify Cryptographic Integrity"}
        </button>
      </div>

      {/* Chain Integrity Banner */}
      {chainStatus && (
        <div className={`chain-integrity-panel ${chainStatus.valid ? "status-valid" : "status-invalid"}`}>
          <div className="chain-status-header">
            <span className="chain-status-indicator" />
            <strong>
              {chainStatus.valid
                ? "Cryptographic Hash Chain: VERIFIED & INTACT"
                : "Cryptographic Hash Chain: INTEGRITY ALERT"}
            </strong>
            <span className="chain-timestamp">
              Checked: {chainStatus.verified_at ? new Date(chainStatus.verified_at).toLocaleTimeString() : "Just now"}
            </span>
          </div>
          <div className="chain-meta-grid">
            <div className="chain-meta-item">
              <span>Signed Entries</span>
              <strong>{chainStatus.signed_count ?? 0}</strong>
            </div>
            <div className="chain-meta-item">
              <span>Legacy (Unsigned)</span>
              <strong>{chainStatus.legacy_unsigned_count ?? 0}</strong>
            </div>
            <div className="chain-meta-item">
              <span>Chain Head Hash</span>
              <code>{truncateHash(chainStatus.chain_head)}</code>
            </div>
            <div className="chain-meta-item">
              <span>Tamper Resistance</span>
              <strong>SQLite Append-Only Triggers Active</strong>
            </div>
          </div>
          {chainStatus.broken_reason && (
            <p className="chain-broken-notice">
              Integrity issue detected at entry ID #{chainStatus.first_broken_id}: {chainStatus.broken_reason}
            </p>
          )}
        </div>
      )}

      {logs.length === 0 ? (
        <p className="no-events-notice">No audit events recorded yet.</p>
      ) : (
        <div className="audit-table-container">
          <table>
            <thead>
              <tr>
                <th>Time (UTC)</th>
                <th>Action</th>
                <th>Operator</th>
                <th>Txn ID</th>
                <th>Chain Integrity</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id}>
                  <td className="audit-time">
                    {log.timestamp ? new Date(log.timestamp).toLocaleString() : "—"}
                  </td>
                  <td>
                    <span className="audit-action-badge">{log.action || "—"}</span>
                  </td>
                  <td className="audit-operator">
                    <strong>{log.performed_by || "SYSTEM"}</strong>
                  </td>
                  <td>{log.transaction_id ?? "—"}</td>
                  <td>
                    {log.entry_hash ? (
                      <span className="chain-badge signed" title={`SHA-256: ${log.entry_hash}`}>
                        Signed ✓ <code>{truncateHash(log.entry_hash)}</code>
                      </span>
                    ) : (
                      <span className="chain-badge unsigned" title="Written prior to HMAC chain">
                        Legacy
                      </span>
                    )}
                  </td>
                  <td className="audit-details">
                    <pre>{formatDetails(log.details)}</pre>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}