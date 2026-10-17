import { useEffect, useState } from "react";
import "./MomoSimulator.css";

const API_URL = "http://127.0.0.1:5001";

const TYPE_OPTIONS = [
  { value: "TRANSFER", label: "Wallet to Wallet", medium: "PIN" },
  { value: "CASH_OUT", label: "Wallet to Bank / Cash Out", medium: "OTP" },
  { value: "CASH_IN", label: "Bank to Wallet / Cash In", medium: "OTP" },
];

const DEFAULT_BANK_ACCOUNT = "BANK-ACC-7001";

const CUSTOM_DEVICE_DEFAULT = "CUSTOM-DEVICE-001";

const SCENARIO_OPTIONS = [
  {
    value: "RECENT_SIM_SWAP",
    label: "Simulated recent SIM swap signal",
    hint: "Inject a recent SIM re-issue flag; no telecom provider is queried.",
  },
  {
    value: "DEVICE_CHANGE",
    label: "New device signal",
    hint: "Unrecognized hardware fingerprint or new phone",
  },
  {
    value: "IP_MISMATCH",
    label: "IP / network mismatch signal",
    hint: "IP address outside customer's usual /24 subnet",
  },
  {
    value: "VELOCITY_SPIKE",
    label: "Velocity spike signal",
    hint: "Burst of 3+ transactions within 3 minutes",
  },
  {
    value: "FREQUENCY_SPIKE",
    label: "Unusual daily frequency",
    hint: "Daily transaction volume exceeds 2× historical rate",
  },
  {
    value: "AMOUNT_DEVIATION",
    label: "Amount deviates from usual pattern",
    hint: "Transaction amount is significantly above the customer's usual amount",
  },
  {
    value: "UNUSUAL_HOUR",
    label: "Unusual transaction time",
    hint: "Transaction initiated outside normal operating hours",
  },
];

const PROCESSING_STEPS = [
  "Contacting payment gateway…",
  "Verifying PIN / OTP…",
  "Fraud engine scanning in real time…",
  "Finalising…",
];

function ProcessingLabel() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const timer = setInterval(
      () => setIndex((current) => (current + 1) % PROCESSING_STEPS.length),
      900
    );
    return () => clearInterval(timer);
  }, []);

  return <p>{PROCESSING_STEPS[index]}</p>;
}

export default function MomoSimulator() {
  const [customers, setCustomers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState("form"); // form | verify | stepup | result
  const [error, setError] = useState("");

  const [customerRef, setCustomerRef] = useState("");
  const [type, setType] = useState("TRANSFER");
  const [recipient, setRecipient] = useState("");
  const [customWalletMode, setCustomWalletMode] = useState(false);
  const [customRecipientMode, setCustomRecipientMode] = useState(false);
  const [amount, setAmount] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [ipAddress, setIpAddress] = useState("");
  const [scenarios, setScenarios] = useState([]);

  const [session, setSession] = useState(null);
  const [code, setCode] = useState("");
  const [result, setResult] = useState(null);
  const [showInsights, setShowInsights] = useState(false);

  const [securityMode, setSecurityMode] = useState(null);
  const [securityCode, setSecurityCode] = useState("");
  const [securityInfo, setSecurityInfo] = useState("");

  useEffect(() => {
    fetch(`${API_URL}/api/momo/customers`)
      .then((response) => response.json())
      .then((data) => {
        setCustomers(data.customers || []);
        if (data.customers?.length) {
          const first = data.customers[0];
          setCustomerRef(first.ref);
          setDeviceId(first.devices[0]);
          setIpAddress(first.base_ip);
          if (first.recipients?.length) {
            setRecipient(first.recipients[0].ref);
          }
        }
      })
      .catch(() => setError("Could not load the mobile money service."));
  }, []);

  const customer = customers.find((c) => c.ref === customerRef);

  function resetForm() {
    setStep("form");
    setSession(null);
    setCode("");
    setResult(null);
    setError("");
    setShowInsights(false);
    if (customer) {
      setDeviceId(customer.devices[0]);
      setIpAddress(customer.base_ip);
    }
  }

  function selectWallet(nextRef) {
    setCustomerRef(nextRef);
    const match = customers.find((c) => c.ref === nextRef);
    if (match) {
      setDeviceId(match.devices[0]);
      setIpAddress(match.base_ip);
    } else if (nextRef.trim() && !deviceId) {
      setDeviceId(CUSTOM_DEVICE_DEFAULT);
    }
  }

  function handleWalletSelectChange(event) {
    const val = event.target.value;
    if (val === "__CUSTOM__") {
      setCustomWalletMode(true);
      setCustomerRef("0241234567");
      setDeviceId(CUSTOM_DEVICE_DEFAULT);
      setIpAddress("192.168.1.50");
      setCustomRecipientMode(true);
      setRecipient("0249876543");
    } else {
      setCustomWalletMode(false);
      selectWallet(val);
      const match = customers.find((c) => c.ref === val);
      if (match?.recipients?.length) {
        setRecipient(match.recipients[0].ref);
        setCustomRecipientMode(false);
      }
    }
  }

  function handleRecipientSelectChange(event) {
    const val = event.target.value;
    if (val === "__CUSTOM__") {
      setCustomRecipientMode(true);
      setRecipient("0249876543");
    } else {
      setCustomRecipientMode(false);
      setRecipient(val);
    }
  }

  function toggleScenario(value) {
    setScenarios((current) =>
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value]
    );
  }

  async function sendMoney(event) {
    event.preventDefault();
    setError("");
    setLoading(true);

    const payload = {
      customer_ref: customerRef,
      transaction_type: type,
      amount: Number(amount),
      recipient,
      device_id: deviceId,
      ip_address: ipAddress,
      scenarios,
    };

    try {
      const response = await fetch(
        `${API_URL}/api/momo/transaction/initiate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || "Unable to start transaction.");
      await new Promise((resolve) => setTimeout(resolve, 1000));
      setSession(data);
      setStep("verify");
    } catch (err) {
      setError(err.message || "Could not reach the mobile money service.");
    } finally {
      setLoading(false);
    }
  }

  async function confirmCode(event, isStepUp = false) {
    event.preventDefault();
    if (!session) return;
    setError("");
    setLoading(true);

    try {
      const response = await fetch(
        `${API_URL}/api/momo/transaction/verify`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: session.session_id,
            code,
            step_up: isStepUp,
          }),
        }
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || "Verification failed.");
      await new Promise((resolve) => setTimeout(resolve, 1300));

      if (data.status === "STEP_UP_REQUIRED") {
        setSession((current) => ({ ...current, ...data }));
        setCode("");
        setStep("stepup");
      } else {
        setResult(data);
        setStep("result");
      }
    } catch (err) {
      setError(err.message || "Verification failed.");
    } finally {
      setLoading(false);
    }
  }

  const available = customer?.balance ?? 0;

  async function submitSecurityAction(event) {
    event.preventDefault();
    if (!securityMode) return;
    setError("");
    setLoading(true);

    try {
      const path =
        securityMode === "freeze"
          ? "/api/momo/account/freeze"
          : "/api/momo/account/unfreeze";

      const response = await fetch(`${API_URL}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ customer_ref: customerRef, code: securityCode }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.error || "Security action failed.");
      }

      setSecurityInfo(data.message);
      setSecurityMode(null);
      setSecurityCode("");

      const customersResponse = await fetch(`${API_URL}/api/momo/customers`);
      const customersData = await customersResponse.json();
      setCustomers(customersData.customers || []);
    } catch (err) {
      setError(err.message || "Security action failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="momo-page">
      <header className="momo-header">
        <p className="eyebrow">GHANA MOBILE MONEY</p>
        <h1>Mobile Money Simulator</h1>
        <p>
          Simulate a wallet-to-wallet, wallet-to-bank, or bank-to-wallet
          transaction. After PIN/OTP authentication, the{" "}
          <strong>behavioral fraud engine</strong> scores the transaction and
          decides whether to approve, step up verification, or block it.
        </p>
      </header>

      <div className="momo-layout">
        <section className="phone-column">
          <div className="phone-frame">
            <div className="phone-status">
              <span>• 12:47</span>
              <span className="phone-status-mid">MoMo</span>
              <span>90%</span>
            </div>
            {step === "form" && (
              <form className="momo-form" onSubmit={sendMoney}>
                <h2>Send money</h2>

                {customer?.account_frozen && (
                  <div className="security-frozen-banner">
                    <strong>Account frozen by customer request</strong>
                    <p>
                      No money can leave your wallet. Use the security section
                      below to unfreeze and resume sending.
                    </p>
                  </div>
                )}

                <label>
                  Your wallet
                  <select
                    value={customWalletMode ? "__CUSTOM__" : customerRef}
                    onChange={handleWalletSelectChange}
                  >
                    {customers.map((c) => (
                      <option key={c.ref} value={c.ref}>
                        {c.name} ({c.ref}) — GHS {Number(c.balance).toLocaleString()}
                      </option>
                    ))}
                    <option value="__CUSTOM__">+ Enter custom wallet / mobile number...</option>
                  </select>
                  {customWalletMode && (
                    <div className="custom-input-group">
                      <input
                        type="text"
                        required
                        value={customerRef}
                        onChange={(e) => selectWallet(e.target.value)}
                        placeholder="e.g. 0241234567 or WALLET-1005"
                        autoComplete="off"
                        aria-describedby="wallet-guidance"
                      />
                      <small className="field-guidance" id="wallet-guidance">
                        Custom wallets start with a GHS 5,000 demo balance and default PIN <strong>0000</strong>.
                      </small>
                    </div>
                  )}
                </label>

                <p className="balance-pill">
                  {customer ? (
                    <>
                      Wallet: <strong>{customer.name}</strong> · Balance:{" "}
                      <strong>GHS {Number(available).toLocaleString()}</strong>
                    </>
                  ) : (
                    <>Custom wallet ({customerRef || "custom"}) · Demo balance: GHS 5,000 (PIN: 0000)</>
                  )}
                </p>

                <label>
                  Medium
                  <select
                    value={type}
                    onChange={(event) => {
                      const nextType = event.target.value;
                      setType(nextType);
                      setCustomRecipientMode(false);
                      setRecipient(
                        nextType === "TRANSFER"
                          ? (customer?.recipients?.[0]?.ref || "")
                          : DEFAULT_BANK_ACCOUNT
                      );
                    }}
                  >
                    {TYPE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label} (verify with {o.medium})
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  {type === "TRANSFER"
                    ? "Recipient wallet or mobile number"
                    : type === "CASH_OUT"
                      ? "Destination bank account"
                      : "Source bank account"}

                  {type === "TRANSFER" ? (
                    <>
                      <select
                        value={customRecipientMode ? "__CUSTOM__" : recipient}
                        onChange={handleRecipientSelectChange}
                      >
                        <option value="" disabled>Select recipient...</option>
                        {customer?.recipients?.map((r) => (
                          <option key={r.ref} value={r.ref}>
                            {r.name} ({r.ref})
                          </option>
                        ))}
                        <option value="__CUSTOM__">+ Enter custom recipient / mobile number...</option>
                      </select>
                      {customRecipientMode && (
                        <div className="custom-input-group">
                          <input
                            type="text"
                            required
                            value={recipient}
                            onChange={(event) => setRecipient(event.target.value)}
                            placeholder="e.g. 0249876543 or WALLET-3001"
                            autoComplete="off"
                            aria-describedby="recipient-guidance"
                          />
                          <small className="field-guidance" id="recipient-guidance">
                            Enter any Ghana mobile number (024xxxxxxx, 055xxxxxxx) or custom wallet reference.
                          </small>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <select
                        value={recipient === DEFAULT_BANK_ACCOUNT ? DEFAULT_BANK_ACCOUNT : "__CUSTOM__"}
                        onChange={(event) => {
                          const val = event.target.value;
                          if (val === "__CUSTOM__") {
                            setRecipient("BANK-ACC-7002");
                          } else {
                            setRecipient(DEFAULT_BANK_ACCOUNT);
                          }
                        }}
                      >
                        <option value={DEFAULT_BANK_ACCOUNT}>GCB Bank (Demo) — BANK-ACC-7001</option>
                        <option value="__CUSTOM__">+ Enter other bank reference...</option>
                      </select>
                      {recipient !== DEFAULT_BANK_ACCOUNT && (
                        <div className="custom-input-group">
                          <input
                            type="text"
                            required
                            value={recipient}
                            onChange={(event) => setRecipient(event.target.value)}
                            placeholder="Bank account reference e.g. BANK-ACC-7002"
                            autoComplete="off"
                          />
                        </div>
                      )}
                    </>
                  )}
                </label>

                <label>
                  Amount (GHS)
                  <input
                    required
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    placeholder="e.g. 150"
                  />
                </label>

                <label>
                  Device
                  {customer ? (
                    <select value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
                      {customer.devices.map((d) => (
                        <option key={d} value={d}>{d} (known)</option>
                      ))}
                      <option value="HACKER-PHONE-999">New device (unregistered)</option>
                    </select>
                  ) : (
                    <input
                      type="text"
                      required
                      value={deviceId}
                      onChange={(e) => setDeviceId(e.target.value)}
                      placeholder="e.g. CUSTOM-DEVICE-001"
                      autoComplete="off"
                    />
                  )}
                </label>

                <label>
                  Network / IP address
                  <input
                    value={ipAddress}
                    onChange={(e) => setIpAddress(e.target.value)}
                    type="text"
                    required
                    autoComplete="off"
                    spellCheck="false"
                    aria-describedby="ip-guidance"
                    placeholder="e.g. 192.168.1.22"
                  />
                  <small className="field-guidance" id="ip-guidance">
                    Enter a valid IPv4 or IPv6 address. Use the wallet's usual
                    network to simulate normal activity, or another address
                    to test an IP mismatch.
                  </small>
                </label>

                <fieldset className="scenario-fieldset">
                  <div className="scenario-legend-row">
                    <legend>Synthetic risk scenarios</legend>
                    <span
                      className={`scenario-badge ${
                        scenarios.length === 0
                          ? "normal"
                          : scenarios.length === 1
                            ? "single"
                            : "compound"
                      }`}
                    >
                      <span className="badge-dot" />
                      {scenarios.length === 0
                        ? "Normal activity"
                        : scenarios.length === 1
                          ? "1 risk signal active"
                          : `Compound risk (${scenarios.length} signals active)`}
                    </span>
                  </div>

                  <div className="scenario-quick-actions">
                    <button
                      type="button"
                      className={`scenario-pill ${scenarios.length === 0 ? "active" : ""}`}
                      onClick={() => setScenarios([])}
                    >
                      Normal (clean)
                    </button>
                    <button
                      type="button"
                      className={`scenario-pill ${
                        scenarios.length === 1 &&
                        scenarios[0] === "RECENT_SIM_SWAP"
                          ? "active"
                          : ""
                      }`}
                      onClick={() => setScenarios(["RECENT_SIM_SWAP"])}
                    >
                      SIM-swap demo
                    </button>
                    <button
                      type="button"
                      className="scenario-pill-action"
                      onClick={() =>
                        setScenarios(SCENARIO_OPTIONS.map((o) => o.value))
                      }
                    >
                      Select all risks
                    </button>
                    {scenarios.length > 0 && (
                      <button
                        type="button"
                        className="scenario-pill-action"
                        onClick={() => setScenarios([])}
                      >
                        Reset to normal
                      </button>
                    )}
                  </div>

                  <div className="scenario-cards-list">
                    <label
                      className={`scenario-card ${
                        scenarios.length === 0 ? "selected-normal" : ""
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={scenarios.length === 0}
                        onChange={(event) => {
                          if (event.target.checked) setScenarios([]);
                        }}
                      />
                      <div className="scenario-card-content">
                        <div className="scenario-card-header">
                          <strong>Normal baseline activity</strong>
                        </div>
                        <p className="scenario-card-hint">
                          Clean transaction with no synthetic risk signals injected.
                        </p>
                      </div>
                    </label>

                    {SCENARIO_OPTIONS.map((option) => {
                      const isChecked = scenarios.includes(option.value);
                      return (
                        <label
                          key={option.value}
                          className={`scenario-card ${isChecked ? "selected" : ""}`}
                        >
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => toggleScenario(option.value)}
                          />
                          <div className="scenario-card-content">
                            <div className="scenario-card-header">
                              <strong>{option.label}</strong>
                            </div>
                            <p className="scenario-card-hint">{option.hint}</p>
                          </div>
                        </label>
                      );
                    })}
                  </div>

                  <small className="field-guidance">
                    These are manually injected synthetic signals, not live
                    telecom or network checks. Select one to demonstrate its
                    effect, or combine signals to model compound risk.
                  </small>
                </fieldset>

                <div className="security-section">
                  <p className="security-note">
                    Think someone else has access? Freeze your account instantly —
                    this control works even when the fraud engine has not flagged
                    anything.
                  </p>

                  {customer?.account_frozen ? (
                    <>
                      <button
                        type="button"
                        className="security-button unfreeze"
                        onClick={() => {
                          setSecurityInfo("");
                          setError("");
                          setSecurityCode("");
                          setSecurityMode("unfreeze");
                        }}
                      >
                        Unfreeze account
                      </button>

                      {securityMode === "unfreeze" && (
                        <div className="security-dialog">
                          <h3>Unfreeze your account</h3>
                          <p className="demo-hint">
                            Confirm with your MoMo PIN. Demo PINs: WALLET-1001 →
                            2233 · WALLET-1002 → 7788 · WALLET-1003 → 9911.
                          </p>
                          <input
                            required
                            value={securityCode}
                            maxLength={4}
                            onChange={(e) => setSecurityCode(e.target.value)}
                            placeholder="••••"
                          />
                          <div className="security-actions">
                            <button
                              type="button"
                              className="send-button"
                              onClick={submitSecurityAction}
                              disabled={loading}
                            >
                              Unfreeze now
                            </button>
                            <button
                              type="button"
                              className="link-button"
                              onClick={() => setSecurityMode(null)}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="security-button"
                        onClick={() => {
                          setSecurityInfo("");
                          setError("");
                          setSecurityCode("");
                          setSecurityMode("freeze");
                        }}
                      >
                        Freeze account
                      </button>

                      {securityMode === "freeze" && (
                        <div className="security-dialog">
                          <h3>Freeze your account</h3>
                          <p className="demo-hint">
                            Confirm with your MoMo PIN. Demo PINs: WALLET-1001 →
                            2233 · WALLET-1002 → 7788 · WALLET-1003 → 9911.
                          </p>
                          <input
                            required
                            value={securityCode}
                            maxLength={4}
                            onChange={(e) => setSecurityCode(e.target.value)}
                            placeholder="••••"
                          />
                          <div className="security-actions">
                            <button
                              type="button"
                              className="send-button"
                              onClick={submitSecurityAction}
                              disabled={loading}
                            >
                              Freeze now
                            </button>
                            <button
                              type="button"
                              className="link-button"
                              onClick={() => setSecurityMode(null)}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>

                {securityInfo && <p className="security-info">{securityInfo}</p>}

                {error && <p className="momo-error">{error}</p>}

                {customer?.account_frozen ? (
                  <p className="frozen-send-note">
                    Sending is disabled — your account is frozen at your
                    request. Unfreeze it above to resume.
                  </p>
                ) : (
                  <button type="submit" className="send-button" disabled={loading}>
                    {loading ? "Starting…" : "Send money"}
                  </button>
                )}
              </form>
            )}

            {step === "verify" && session && (
              <form className="momo-form" onSubmit={confirmCode}>
                <h2>Confirm with {session.verification_method}</h2>
                <p className="momo-note">{session.message}</p>

                <div className="receipt-preview">
                  <div>
                    <span>{type === "CASH_IN" ? "From bank" : "To"}</span>
                    <strong>{recipient}</strong>
                  </div>
                  <div>
                    <span>Amount</span>
                    <strong>GHS {Number(session.amount).toLocaleString()}</strong>
                  </div>
                  <div>
                    <span>Network</span>
                    <strong>{ipAddress}</strong>
                  </div>
                  <div>
                    <span>Device</span>
                    <strong>{deviceId}</strong>
                  </div>
                  <div>
                    <span>Scenarios</span>
                    <strong>
                      {scenarios.length ? scenarios.join(", ") : "Normal activity"}
                    </strong>
                  </div>
                </div>

                <label>
                  {session.verification_method === "PIN"
                    ? "Enter your 4-digit MoMo PIN"
                    : "Enter the OTP you received"}
                  <input
                    required
                    value={code}
                    maxLength={session.verification_method === "PIN" ? 4 : 6}
                    onChange={(e) => setCode(e.target.value)}
                    placeholder={session.verification_method === "PIN" ? "••••" : "••••••"}
                  />
                </label>

                <div className="credential-autofill-box">
                  <p className="demo-hint">
                    Required {session.verification_method}: <strong>{session.demo_hint}</strong>
                  </p>
                  <button
                    type="button"
                    className="autofill-code-btn"
                    onClick={() => setCode(session.demo_hint)}
                  >
                    Quick-fill {session.verification_method} ({session.demo_hint})
                  </button>
                </div>

                {error && <p className="momo-error">{error}</p>}

                <button type="submit" className="send-button" disabled={loading}>
                  {loading ? "Verifying…" : "Confirm & send"}
                </button>
                <button type="button" className="link-button" onClick={resetForm}>
                  ← Cancel transaction
                </button>
              </form>
            )}
{step === "stepup" && session && (
              <form className="momo-form" onSubmit={(e) => confirmCode(e, true)}>
                <h2>Extra security check</h2>
                <p className="momo-note">
                  Our fraud engine flagged risk signals on this transaction.
                  Complete the extra check using the email already registered
                  to this demo account. SMS is not used for this step-up.
                </p>
                <p className="demo-hint">
                  Simulated email to <strong>{session.verification_destination}</strong>.
                  No email was actually sent.
                </p>

                <ul className="stepup-reasons">
                  {(session.reasons || []).map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>

                <label>
                  Code from simulated email
                  <input
                    required
                    value={code}
                    maxLength={6}
                    onChange={(e) => setCode(e.target.value)}
                    placeholder="••••••"
                  />
                </label>

                <div className="credential-autofill-box">
                  <p className="demo-hint">
                    Demo email code: <strong>{session.demo_hint}</strong>
                  </p>
                  <button
                    type="button"
                    className="autofill-code-btn"
                    onClick={() => setCode(session.demo_hint)}
                  >
                    Quick-fill Email Code ({session.demo_hint})
                  </button>
                </div>

                {error && <p className="momo-error">{error}</p>}

                <button type="submit" className="send-button" disabled={loading}>
                  {loading ? "Verifying…" : "Verify email code"}
                </button>
              </form>
            )}
{step === "result" && result && (
              <div className="momo-result">
                {result.approved ? (
                  <>
                    <div className="result-icon success">✓</div>
                    <h2>Money sent</h2>
                    <p className="momo-note">{result.message}</p>
                  </>
                ) : result.status === "BLOCKED" ? (
                  <>
                    <div className="result-icon blocked">✕</div>
                    <h2>Transaction blocked</h2>
                    <p className="momo-note">{result.message}</p>
                  </>
                ) : null}

                <div className="receipt-full">
                  <div>
                    <span>Reference</span>
                    <strong>{result.transaction_reference}</strong>
                  </div>
                  <div>
                    <span>Amount</span>
                    <strong>GHS {Number(result.amount).toLocaleString()}</strong>
                  </div>
                  <div>
                    <span>Risk score</span>
                    <strong>{Number(result.risk_score ?? 0).toFixed(4)}</strong>
                  </div>
                  <div>
                    <span>Risk level</span>
                    <strong className={`risk-text ${result.risk_level?.toLowerCase()}`}>
                      {result.risk_level}
                    </strong>
                  </div>
                </div>

                {!result.approved && result.reasons?.length > 0 && (
                  <div className="blocked-reasons">
                    <h3>Why it was flagged</h3>
                    <ul>
                      {result.reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                    {result.case_created && (
                      <p className="case-note">
                        A fraud case was opened automatically for investigation.
                      </p>
                    )}
                  </div>
                )}

                <button
                  type="button"
                  className="insight-toggle"
                  onClick={() => setShowInsights((visible) => !visible)}
                >
                  {showInsights ? "Hide" : "Show"} — what the fraud engine checked
                </button>

                {showInsights && (
                  <div className="engine-insights">
                    <ul>
                      <li className={result.signals?.device_change ? "flag" : "ok"}>
                        Device change:{" "}
                        {result.signals?.device_change ? "detected" : "not detected"}
                        <small>{result.signals?.device_id}</small>
                      </li>
                      <li className={result.signals?.ip_network_mismatch ? "flag" : "ok"}>
                        IP / network mismatch:{" "}
                        {result.signals?.ip_network_mismatch ? "detected" : "not detected"}
                        <small>Expected network: {result.signals?.expected_network}</small>
                      </li>
                      <li className={result.signals?.recipient_novel ? "flag" : "ok"}>
                        New recipient: {result.signals?.recipient_novel ? "yes" : "no"}
                      </li>
                      <li className={result.signals?.velocity_spike ? "flag" : "ok"}>
                        Velocity spike:{" "}
                        {result.signals?.velocity_spike ? "detected" : "not detected"}
                      </li>
                    </ul>
                  </div>
                )}

                <button type="button" className="send-button" onClick={resetForm}>
                  New transaction
                </button>
              </div>
            )}

            {loading && (
              <div className="processing-layer">
                <div className="spinner" />
                <ProcessingLabel />
              </div>
            )}
          </div>
        </section>
<aside className="role-panel">
          <div>
            <p className="role-kicker">THE MOMENT AFTER AUTHENTICATION</p>
            <h2>How a transfer gets its risk check</h2>
          </div>
          <div
            className="role-illustration"
            role="img"
            aria-label="Flow from PIN or OTP verification through behavior and security checks to a transaction decision"
          >
            <svg viewBox="0 0 560 190" aria-hidden="true">
              <defs>
                <linearGradient id="riskFlowLine" x1="0" x2="1">
                  <stop offset="0%" stopColor="#f472b6" />
                  <stop offset="100%" stopColor="#38b6c3" />
                </linearGradient>
              </defs>
              <path
                d="M75 82 H485"
                fill="none"
                stroke="url(#riskFlowLine)"
                strokeWidth="4"
                strokeDasharray="6 8"
                strokeLinecap="round"
              />
              {[
                { x: 75, title: "PIN / OTP", detail: "Identity check", mark: "01" },
                { x: 210, title: "BEHAVIOR", detail: "Customer history", mark: "02" },
                { x: 350, title: "RISK SCORE", detail: "Signals combined", mark: "03" },
                { x: 485, title: "DECISION", detail: "Pass · step-up · hold", mark: "04" },
              ].map((node) => (
                <g key={node.mark}>
                  <circle
                    cx={node.x}
                    cy="82"
                    r="31"
                    fill="#162a3a"
                    stroke={node.mark === "04" ? "#38b6c3" : "#f472b6"}
                    strokeWidth="2"
                  />
                  <text
                    x={node.x}
                    y="87"
                    textAnchor="middle"
                    fill="#fff"
                    fontSize="14"
                    fontWeight="700"
                  >
                    {node.mark}
                  </text>
                  <text
                    x={node.x}
                    y="137"
                    textAnchor="middle"
                    fill="#fff"
                    fontSize="13"
                    fontWeight="700"
                  >
                    {node.title}
                  </text>
                  <text
                    x={node.x}
                    y="155"
                    textAnchor="middle"
                    fill="#b9c8d2"
                    fontSize="12"
                  >
                    {node.detail}
                  </text>
                </g>
              ))}
              <text x="280" y="25" textAnchor="middle" fill="#8fd6dc" fontSize="12" fontWeight="700" letterSpacing="2">
                THE POST-AUTHENTICATION LAYER
              </text>
            </svg>
          </div>
          <ol>
            <li>
              <strong>Start with a familiar transfer.</strong> Select a wallet
              and enter the recipient, amount, device, and network details.
            </li>
            <li>
              <strong>Complete PIN / OTP verification.</strong> This is the
              normal authentication step; the fraud check does not replace it.
            </li>
            <li>
              <strong>Compare behavior after authentication.</strong> The
              engine checks amount, timing, frequency, recipient history,
              device, and network signals.
            </li>
            <li>
              <strong>Apply a proportionate decision.</strong> Lower risk
              passes; medium risk requests a step-up; high risk is held or
              blocked and escalated for review.
            </li>
            <li>
              <strong>Keep the decision explainable.</strong> The admin console
              receives the risk tier, triggered reasons, and the transaction&apos;s
              IP / device details.
            </li>
          </ol>
          <section className="role-signal-guide">
            <h3>What the test signals mean</h3>
            <div>
              <article>
                <span>01</span>
                <p><strong>Behavior change</strong>Amount, timing, or daily frequency differs from the wallet&apos;s usual pattern.</p>
              </article>
              <article>
                <span>02</span>
                <p><strong>Recipient &amp; velocity</strong>A new destination or several rapid transfers may need a closer look.</p>
              </article>
              <article>
                <span>03</span>
                <p><strong>Security context</strong>A new device, IP mismatch, or simulated SIM-swap signal adds context.</p>
              </article>
            </div>
          </section>
          <p className="role-note">
            <strong>Try a safe scenario.</strong> Select a new device or IP
            mismatch, enter a fictional recipient, and compare the resulting
            reason codes. A signal is an investigation clue—not proof of fraud.
          </p>
        </aside>
      </div>
    </main>
  );
}
