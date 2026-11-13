import { useCallback, useEffect, useState } from "react";
import { adminFetch, API_URL } from "./adminApi.js";
import "./MTNDisbursement.css";

// Clean SVG Icons (replacing all emojis for enterprise-grade styling)
function SendIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function CashoutIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="6" width="20" height="12" rx="2" />
      <circle cx="12" cy="12" r="3" />
      <line x1="6" y1="12" x2="6.01" y2="12" />
      <line x1="18" y1="12" x2="18.01" y2="12" />
    </svg>
  );
}

function BankIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="3" y1="21" x2="21" y2="21" />
      <line x1="3" y1="10" x2="21" y2="10" />
      <polyline points="5 6 12 3 19 6" />
      <line x1="4" y1="10" x2="4" y2="21" />
      <line x1="20" y1="10" x2="20" y2="21" />
      <line x1="8" y1="14" x2="8" y2="17" />
      <line x1="12" y1="14" x2="12" y2="17" />
      <line x1="16" y1="14" x2="16" y2="17" />
    </svg>
  );
}

function StatementIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <polyline points="10 9 9 9 8 9" />
    </svg>
  );
}

function AlertIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function QrIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
    </svg>
  );
}

const TRANSACTION_CHANNELS = [
  { id: "TRANSFER", label: "Wallet to Wallet", sub: "Direct MoMo transfer" },
  { id: "CASH_OUT", label: "Wallet to Bank", sub: "Bank account transfer" },
  { id: "CASH_IN", label: "Bank to Wallet", sub: "Top-up from linked bank" },
];

function getBrowserDeviceId() {
  try {
    let deviceId = localStorage.getItem("momo-sandbox-device-id");
    if (!deviceId) {
      deviceId = crypto.randomUUID();
      localStorage.setItem("momo-sandbox-device-id", deviceId);
    }
    return deviceId;
  } catch {
    return `TEMP-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }
}

export default function MTNDisbursement() {
  // Navigation tabs: 'momo' | 'home' | 'buy' | 'pulse' | 'more'
  const [activeTab, setActiveTab] = useState("momo");

  // Balance privacy state
  const [showBalance, setShowBalance] = useState(false);

  // Active Modals: null | 'send' | 'approvals' | 'statements' | 'freeze'
  const [activeModal, setActiveModal] = useState("send");

  // Transfer steps: 'form' | 'auth' | 'evaluating' | 'stepup' | 'receipt'
  const [step, setStep] = useState("form");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Sandbox customer identity is established once; transfer forms only ask for the recipient.
  const [customerToken, setCustomerToken] = useState(
    () => sessionStorage.getItem("momo-sandbox-customer-token") || "",
  );
  const [customerAccount, setCustomerAccount] = useState(null);
  const [customerView, setCustomerView] = useState("signin");
  const [loginNumber, setLoginNumber] = useState("");
  const [loginPin, setLoginPin] = useState("");
  const [registration, setRegistration] = useState({
    phone_number: "",
    full_name: "",
    id_type: "PASSPORT",
    id_number: "",
    email: "",
    pin: "",
  });
  const [privacyNoticeAccepted, setPrivacyNoticeAccepted] = useState(false);
  const [shareCoarseLocation, setShareCoarseLocation] = useState(false);
  const [accountNotice, setAccountNotice] = useState("");
  const [senderNumber, setSenderNumber] = useState("");
  const [recipientNumber, setRecipientNumber] = useState("");
  const [amount, setAmount] = useState("5.00");
  const [transactionType, setTransactionType] = useState("TRANSFER");

  // Live Telemetry (Device, IP, and Location detection)
  const [telemetry, setTelemetry] = useState(() => ({
    device_id: getBrowserDeviceId(),
    gps_coordinates: null,
  }));
  const [locationStatus, setLocationStatus] = useState("prompt");
  const [locationUpdatedAt, setLocationUpdatedAt] = useState(0);

  // Verified Recipient Inquiry (standard MoMo name inquiry without private ID leakage)
  const [recipientName, setRecipientName] = useState("");
  const [recipientSearching, setRecipientSearching] = useState(false);

  // Authentication & Fraud Enforcement
  const [session, setSession] = useState(null);
  const [authPin, setAuthPin] = useState("");
  const [stepUpInput, setStepUpInput] = useState("");
  const [stepUpMethod, setStepUpMethod] = useState("");
  const [stepUpEmailSent, setStepUpEmailSent] = useState(false);
  const [stepUpEmailMessage, setStepUpEmailMessage] = useState("");
  const [stepUpCodeExpiresAt, setStepUpCodeExpiresAt] = useState(null);
  const [stepUpResendAt, setStepUpResendAt] = useState(null);
  const [stepUpClockNow, setStepUpClockNow] = useState(0);
  const [stepUpChallenge, setStepUpChallenge] = useState(null);
  const [receipt, setReceipt] = useState(null);

  // Emergency Freeze State
  const [walletFrozen, setWalletFrozen] = useState(false);
  const [freezeMessage, setFreezeMessage] = useState("");

  // Statements History
  const [recentTransactions, setRecentTransactions] = useState([
    {
      id: "MOMO-9812401",
      date: "Today, 10:24 AM",
      recipient: "Ama Serwaa (0242002002)",
      amount: "5.00 GHS",
      status: "APPROVED",
      risk_score: "0.14",
    },
    {
      id: "MOMO-9742119",
      date: "Yesterday, 4:18 PM",
      recipient: "Merchant Payout (233241234567)",
      amount: "25.00 GHS",
      status: "APPROVED",
      risk_score: "0.18",
    },
  ]);

  // Configuration
  const [configuration, setConfiguration] = useState({
    currency: "GHS",
    environment: "sandbox",
    maximumAmount: null,
  });
  const displayCurrency =
    transactionType === "TRANSFER" ? configuration.currency : "GHS";
  const stepUpSecondsLeft = stepUpCodeExpiresAt
    ? Math.max(0, Math.ceil((stepUpCodeExpiresAt - stepUpClockNow) / 1000))
    : 0;
  const stepUpResendSecondsLeft = stepUpResendAt
    ? Math.max(0, Math.ceil((stepUpResendAt - stepUpClockNow) / 1000))
    : 0;
  const stepUpCodeCountdown = `${String(Math.floor(stepUpSecondsLeft / 60)).padStart(2, "0")}:${String(stepUpSecondsLeft % 60).padStart(2, "0")}`;
  const stepUpResendCountdown = `${String(Math.floor(stepUpResendSecondsLeft / 60)).padStart(2, "0")}:${String(stepUpResendSecondsLeft % 60).padStart(2, "0")}`;

  useEffect(() => {
    if (step !== "stepup" || !stepUpCodeExpiresAt) return undefined;
    const timer = window.setInterval(() => setStepUpClockNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [step, stepUpCodeExpiresAt]);

  // Recipient live lookup as user types
  useEffect(() => {
    const clean = recipientNumber.replace(/\D/g, "");
    if (clean.length < 8) {
      setRecipientName("");
      return;
    }

    const timer = setTimeout(async () => {
      setRecipientSearching(true);
      try {
        const res = await fetch(`${API_URL}/api/momo/kyc/lookup?query=${encodeURIComponent(clean)}`);
        if (res.ok) {
          const data = await res.json();
          setRecipientName(data.subscriber?.full_name || "");
        } else {
          setRecipientName("");
        }
      } catch {
        setRecipientName("");
      } finally {
        setRecipientSearching(false);
      }
    }, 350);

    return () => clearTimeout(timer);
  }, [recipientNumber]);

  // Check location permission state without requesting access.
  const detectClientTelemetry = useCallback(async () => {
    if (!navigator.permissions?.query) return;
    try {
      const permission = await navigator.permissions.query({
        name: "geolocation",
      });
      setLocationStatus(permission.state);
    } catch {
      setLocationStatus("prompt");
    }
  }, []);

  function requestDeviceLocation() {
    if (!navigator.geolocation) {
      setLocationStatus("unavailable");
      return Promise.resolve(null);
    }
    setLocationStatus("requesting");
    return new Promise((resolve) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const coordinates = {
            latitude: Number(position.coords.latitude.toFixed(2)),
            longitude: Number(position.coords.longitude.toFixed(2)),
          };
          setTelemetry((current) => ({
            ...current,
            gps_coordinates: coordinates,
          }));
          setLocationUpdatedAt(Date.now());
          setLocationStatus("granted");
          resolve(coordinates);
        },
        (error) => {
          setTelemetry((current) => ({
            ...current,
            gps_coordinates: null,
          }));
          setLocationStatus(
            error.code === error.PERMISSION_DENIED ? "denied" : "unavailable",
          );
          resolve(null);
        },
        { timeout: 10000, maximumAge: 0 },
      );
    });
  }

  async function ensureCurrentLocation() {
    if (
      telemetry.gps_coordinates &&
      Date.now() - locationUpdatedAt < 5 * 60 * 1000
    ) {
      return telemetry.gps_coordinates;
    }
    return requestDeviceLocation();
  }

  const loadGatewayConfig = useCallback(async () => {
    try {
      const res = await adminFetch("/api/momo/mtn/disbursement/configuration");
      if (res.ok) {
        const data = await res.json();
        setConfiguration({
          currency: data.currency || "GHS",
          environment: data.environment || "production",
          maximumAmount: data.maximum_amount,
        });
      }
    } catch {
      // use defaults
    }
  }, []);

  useEffect(() => {
    detectClientTelemetry();
    loadGatewayConfig();
    if (!customerToken) return;

    fetch(`${API_URL}/api/momo/customer/me`, {
      headers: { Authorization: `Bearer ${customerToken}` },
    })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Sandbox session expired.");
        setCustomerAccount(data);
        setSenderNumber(data.phone_number);
      })
      .catch(() => {
        sessionStorage.removeItem("momo-sandbox-customer-token");
        setCustomerToken("");
        setCustomerAccount(null);
        setSenderNumber("");
      });
  }, [customerToken, detectClientTelemetry, loadGatewayConfig]);

  async function handleCustomerLogin(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/momo/customer/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          phone_number: loginNumber,
          pin: loginPin,
          device_id: getBrowserDeviceId(),
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Sandbox sign-in failed.");
      sessionStorage.setItem("momo-sandbox-customer-token", data.token);
      setCustomerToken(data.token);
      setCustomerAccount(data.customer);
      setSenderNumber(data.customer.phone_number);
      setLoginPin("");
      setCustomerView("signin");
    } catch (loginError) {
      setError(loginError.message || "Sandbox sign-in failed.");
    } finally {
      setLoading(false);
    }
  }

  async function handleRegistrationSubmit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setAccountNotice("");
    try {
      const location = shareCoarseLocation
        ? await ensureCurrentLocation()
        : null;
      const response = await fetch(
        `${API_URL}/api/momo/customer/register`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...registration,
            privacy_notice_accepted: privacyNoticeAccepted,
            location_consent: shareCoarseLocation,
            device_id: getBrowserDeviceId(),
            ...(location && {
              location_latitude: location.latitude,
              location_longitude: location.longitude,
            }),
          }),
        },
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not create sandbox wallet.");
      sessionStorage.setItem("momo-sandbox-customer-token", data.token);
      setCustomerToken(data.token);
      setCustomerAccount(data.customer);
      setSenderNumber(data.customer.phone_number);
      setRegistration({
        phone_number: "",
        full_name: "",
        id_type: "PASSPORT",
        id_number: "",
        email: "",
        pin: "",
          });
      setPrivacyNoticeAccepted(false);
      setShareCoarseLocation(false);
      setCustomerView("signin");
    } catch (registrationError) {
      setError(registrationError.message || "Could not create sandbox wallet.");
    } finally {
      setLoading(false);
    }
  }

  function signOutCustomer() {
    sessionStorage.removeItem("momo-sandbox-customer-token");
    setCustomerToken("");
    setCustomerAccount(null);
    setSenderNumber("");
    setSession(null);
    setStep("form");
    setPrivacyNoticeAccepted(false);
    setShareCoarseLocation(false);
    setError("");
    setVerificationEmail("");
    setVerificationCode("");
    setEmailChallengeStarted(false);
    setEmailVerificationMessage("");
  }

  async function deleteSandboxAccount() {
    if (
      !window.confirm(
        "Delete this sandbox profile and its security signals? Historical transaction and audit records remain until the pilot data wipe.",
      )
    ) {
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/momo/customer/me`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Could not delete the sandbox account.");
      }
      sessionStorage.removeItem("momo-sandbox-customer-token");
      setCustomerToken("");
      setCustomerAccount(null);
      setSenderNumber("");
      setSession(null);
      setStep("form");
      setShareCoarseLocation(false);
      setAccountNotice(data.message);
    } catch (deleteError) {
      setError(deleteError.message || "Could not delete the sandbox account.");
    } finally {
      setLoading(false);
    }
  }

  // Step 1: Proceed to PIN Authorization
  async function handleProceedToAuth(e) {
    e.preventDefault();
    setError("");

    const parsedAmount = parseFloat(amount);
    if (isNaN(parsedAmount) || parsedAmount <= 0) {
      setError("Please enter a valid transfer amount.");
      return;
    }
    if (!customerToken || !customerAccount) {
      setError("Sign in to a sandbox wallet before starting a transfer.");
      return;
    }
    if (!recipientNumber.trim() || recipientNumber.trim().length < 8) {
      setError("Please enter a valid recipient phone number.");
      return;
    }

    setLoading(true);
    try {
      const location = shareCoarseLocation
        ? await ensureCurrentLocation()
        : null;
      const payload = {
        transaction_type: transactionType,
        amount: parsedAmount,
        recipient: recipientNumber.trim(),
        location_consent: shareCoarseLocation,
        device_id: getBrowserDeviceId(),
        scenario: "NORMAL",
        live_mtn:
          transactionType === "TRANSFER" &&
          customerAccount.transfer_mode !== "SIMULATION_ONLY",
      };
      if (location) {
        payload.location_latitude = location.latitude;
        payload.location_longitude = location.longitude;
      }

      const res = await fetch(`${API_URL}/api/momo/transaction/initiate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${customerToken}`,
        },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Unable to initiate transfer.");

      setSession(data);
      setAuthPin("");
      setStep("auth");
    } catch (err) {
      setError(err.message || "Failed to initiate transfer.");
    } finally {
      setLoading(false);
    }
  }

  // Step 2: Authorize PIN & Run AI Fraud Layer
  async function handleAuthorizePayment() {
    const requiredLength = transactionType === "TRANSFER" ? 4 : 6;
    if (authPin.length !== requiredLength) {
      setError(
        transactionType === "TRANSFER"
          ? "Please enter your 4-digit MoMo PIN."
          : "Please enter the 6-digit demo OTP.",
      );
      return;
    }

    setLoading(true);
    setError("");
    setStep("evaluating");

    try {
      const res = await fetch(`${API_URL}/api/momo/transaction/verify`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${customerToken}`,
        },
        body: JSON.stringify({
          session_id: session.session_id,
          code: authPin.trim(),
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "PIN verification failed.");

      if (data.status === "STEP_UP_REQUIRED") {
        setStepUpChallenge(data);
        setStepUpInput("");
        setStepUpMethod("");
        setStepUpEmailSent(false);
        setStepUpEmailMessage("");
        setStepUpCodeExpiresAt(null);
        setStepUpResendAt(null);
        setStep("stepup");
      } else {
        setReceipt(data);
        setStep("receipt");
        if (data.status === "APPROVED") {
          setRecentTransactions((prev) => [
            {
              id: data.reference_id || `MOMO-${Date.now().toString().slice(-7)}`,
              date: "Just now",
              recipient: recipientName ? `${recipientName} (${recipientNumber})` : recipientNumber,
              amount: `${amount} ${displayCurrency}`,
              status: "APPROVED",
              risk_score: (data.risk_score || 0.12).toFixed(2),
            },
            ...prev,
          ]);
        }
      }
    } catch (err) {
      setError(err.message || "Incorrect PIN or authorization failure.");
      setStep("auth");
    } finally {
      setLoading(false);
    }
  }

  // Step 3: Complete MEDIUM-risk step-up challenge.
  async function handleChooseStepUpMethod(method, forceResend = false) {
    setStepUpMethod(method);
    setStepUpInput("");
    setError("");

    if (method !== "EMAIL_OTP") return;
    if (
      !forceResend &&
      stepUpEmailSent &&
      stepUpSecondsLeft > 0
    ) {
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(
        `${API_URL}/api/momo/transaction/${session.session_id}/step-up/email`,
        {
          method: "POST",
          headers: { Authorization: `******` },
        },
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Could not send the verification email.");
      }
      setStepUpEmailSent(true);
      setStepUpEmailMessage(data.message);
      const sentAt = Date.now();
      setStepUpClockNow(sentAt);
      setStepUpCodeExpiresAt(
        sentAt + Number(data.expires_in_seconds || 600) * 1000,
      );
      setStepUpResendAt(
        sentAt + Number(data.resend_after_seconds || 60) * 1000,
      );
    } catch (emailError) {
      setError(emailError.message || "Could not send the verification email.");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyStepUp(e) {
    if (e) e.preventDefault();
    if (!stepUpMethod) {
      setError("Choose email or passport/ID verification.");
      return;
    }
    if (
      stepUpMethod === "EMAIL_OTP" &&
      (!stepUpEmailSent || stepUpSecondsLeft <= 0)
    ) {
      setError("Request a new email code before entering it.");
      return;
    }
    if (!stepUpInput.trim()) {
      setError(
        stepUpMethod === "EMAIL_OTP"
          ? "Enter the six-digit code sent to your email."
          : "Enter the passport or ID number saved on your wallet.",
      );
      return;
    }

    setLoading(true);
    setError("");

    try {
      const res = await fetch(`${API_URL}/api/momo/transaction/verify`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${customerToken}`,
        },
        body: JSON.stringify({
          session_id: session.session_id,
          code: stepUpInput.trim(),
          step_up: true,
          step_up_method: stepUpMethod,
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Step-up verification failed.");

      setReceipt(data);
      setStep("receipt");
      if (data.status === "APPROVED") {
        setRecentTransactions((prev) => [
          {
            id: data.reference_id || `MOMO-${Date.now().toString().slice(-7)}`,
            date: "Just now",
            recipient: recipientName ? `${recipientName} (${recipientNumber})` : recipientNumber,
            amount: `${amount} ${displayCurrency}`,
            status: "APPROVED (STEP-UP)",
            risk_score: (data.risk_score || 0.42).toFixed(2),
          },
          ...prev,
        ]);
      }
    } catch (err) {
      setError(err.message || "Security verification failed.");
    } finally {
      setLoading(false);
    }
  }

  async function checkMtnTransferStatus() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(
        `${API_URL}/api/momo/transaction/${session.session_id}/status`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${customerToken}` },
        },
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not check MTN status.");
      setReceipt((current) => ({ ...current, ...data }));
    } catch (statusError) {
      setError(statusError.message || "Could not check MTN status.");
    } finally {
      setLoading(false);
    }
  }

  // Keypad Handlers
  function handleKeypadPress(num) {
    if (authPin.length < 4) {
      setAuthPin((prev) => prev + num);
    }
  }

  function handleKeypadBackspace() {
    setAuthPin((prev) => prev.slice(0, -1));
  }

  function handleKeypadClear() {
    setAuthPin("");
  }

  // Emergency Freeze Handler
  async function handleEmergencyFreeze() {
    if (!window.confirm("Confirm emergency account freeze? This will block all incoming and outgoing transactions immediately.")) {
      return;
    }

    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/momo/account/freeze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customer_ref: senderNumber,
          code: "2233",
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setWalletFrozen(true);
        setFreezeMessage(data.message || "Your MoMo wallet has been locked. Contact security operations.");
      } else {
        setError(data.detail || "Unable to execute emergency freeze.");
      }
    } catch (err) {
      setError(err.message || "Network error while locking account.");
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setStep("form");
    setSession(null);
    setAuthPin("");
    setStepUpInput("");
    setStepUpMethod("");
    setStepUpEmailSent(false);
    setStepUpEmailMessage("");
    setStepUpCodeExpiresAt(null);
    setStepUpResendAt(null);
    setStepUpChallenge(null);
    setReceipt(null);
    setError("");
  }

  return (
    <div className="momo-container">
      {/* Top Security & Telecom Status Banner */}
      <header className="momo-nav-header">
        <div className="momo-brand-block">
          <span className="momo-logo-box">MTN MoMo</span>
          <span className="momo-engine-badge">
            <span className="pulse-indicator" />
            Protected by AI Fraud Detection Engine
          </span>
        </div>
      </header>

      {/* Main Center Mobile Phone Screen */}
      <div className="momo-app-wrapper">
        <div className="phone-chassis">
          <div className="phone-screen-inner">
            {/* Native Status Bar */}
            <div className="screen-status-bar">
              <span className="time-display">09:41</span>
              <div className="status-icons-group">
                <span>4G LTE</span>
                <span>98%</span>
              </div>
            </div>

            {/* MTN Curved Yellow Header */}
            <div className="yellow-header-card">
              <div className="curved-bg-motif" />

              <div className="header-top-row">
                <div className="subscriber-greeting">
                  <span className="greeting-small">Subscriber Account</span>
                  <h2 className="sender-number-display">
                    {senderNumber || "Sign in required"}
                  </h2>
                  <span className="account-status-tag">
                    {customerAccount
                      ? customerAccount.transfer_mode === "SIMULATION_ONLY"
                        ? "Simulation-only test wallet"
                        : "Sandbox demo wallet"
                      : "No wallet signed in"}
                  </span>
                </div>
                <div className="momo-pay-badge">
                  <QrIcon />
                  <span>MoMo Pay</span>
                </div>
              </div>

              {/* Balance Box with Toggle */}
              <div className="balance-surface">
                <div className="balance-top-labels">
                  <span className="balance-label-text">Available Balance</span>
                  <button
                    type="button"
                    className="balance-visibility-btn"
                    onClick={() => setShowBalance(!showBalance)}
                  >
                    {showBalance ? "Hide" : "Show"}
                  </button>
                </div>
                <div className="balance-amount-display">
                  <span className="currency-symbol">{displayCurrency}</span>
                  <strong className="balance-figures">
                    {showBalance ? "5,000.00" : "••••••••"}
                  </strong>
                </div>
              </div>
            </div>

            {/* 4 Circular Action Buttons */}
            <div className="quick-actions-bar">
              <button
                type="button"
                className={`quick-action-unit ${activeModal === "send" ? "selected" : ""}`}
                onClick={() => {
                  handleReset();
                  setActiveModal("send");
                }}
              >
                <div className="action-circle">
                  <SendIcon />
                </div>
                <span className="action-name">Send MoMo</span>
              </button>

              <button
                type="button"
                className={`quick-action-unit ${activeModal === "approvals" ? "selected" : ""}`}
                onClick={() => setActiveModal("approvals")}
              >
                <div className="action-circle">
                  <ShieldIcon />
                </div>
                <span className="action-name">Approvals</span>
              </button>

              <button
                type="button"
                className="quick-action-unit"
                onClick={() => {
                  setTransactionType("CASH_OUT");
                  setRecipientNumber("BANK-ACC-7001");
                  setActiveModal("send");
                }}
              >
                <div className="action-circle">
                  <CashoutIcon />
                </div>
                <span className="action-name">Cashout</span>
              </button>

              <button
                type="button"
                className="quick-action-unit"
                onClick={() => {
                  setTransactionType("CASH_IN");
                  setRecipientNumber("BANK-ACC-7001");
                  setActiveModal("send");
                }}
              >
                <div className="action-circle">
                  <BankIcon />
                </div>
                <span className="action-name">Bank Transfer</span>
              </button>
            </div>

            {/* App Scrollable Content */}
            <div className="phone-body-content">
              {walletFrozen && (
                <div className="status-banner banner-danger">
                  <LockIcon />
                  <div>
                    <strong>Wallet Frozen</strong>
                    <p>{freezeMessage}</p>
                  </div>
                </div>
              )}

              {/* Service Navigation List */}
              <div className="service-nav-list">
                <button
                  type="button"
                  className="service-row-btn"
                  onClick={() => setActiveModal("statements")}
                >
                  <div className="service-row-left">
                    <div className="service-icon-wrapper">
                      <StatementIcon />
                    </div>
                    <div>
                      <strong>Account Statements</strong>
                      <span>Review recent transaction logs &amp; risk scores</span>
                    </div>
                  </div>
                  <span className="row-chevron">›</span>
                </button>

                <button
                  type="button"
                  className="service-row-btn"
                  onClick={() => setActiveModal("freeze")}
                >
                  <div className="service-row-left">
                    <div className="service-icon-wrapper icon-danger">
                      <AlertIcon />
                    </div>
                    <div>
                      <strong>Emergency Security Freeze</strong>
                      <span>Lock wallet immediately if suspicious activity occurs</span>
                    </div>
                  </div>
                  <span className="row-chevron">›</span>
                </button>
              </div>

              {/* Security Telemetry Verification Notice */}
              <div className="security-notice-card">
                <div className="notice-header">
                  <ShieldIcon />
                  <strong>Real-Time Behavioral Security</strong>
                </div>
                <p>
                  Transactions are continuously monitored for hardware identity changes, IP network
                  anomalies, unusual locations, and abnormal transaction patterns.
                </p>
              </div>
            </div>

            {/* Bottom App Navigation */}
            <nav className="phone-bottom-tabs">
              <button
                type="button"
                className={`tab-item ${activeTab === "home" ? "active" : ""}`}
                onClick={() => setActiveTab("home")}
              >
                <span className="tab-label">Home</span>
              </button>

              <button
                type="button"
                className={`tab-item ${activeTab === "buy" ? "active" : ""}`}
                onClick={() => setActiveTab("buy")}
              >
                <span className="tab-label">Buy</span>
              </button>

              <div className="pulse-floating-container">
                <button
                  type="button"
                  className="pulse-floating-btn"
                  onClick={() => {
                    handleReset();
                    setActiveModal("send");
                  }}
                  title="Direct MoMo Transfer"
                >
                  <SendIcon />
                </button>
                <span className="pulse-text">MoMo</span>
              </div>

              <button
                type="button"
                className={`tab-item ${activeTab === "momo" ? "active" : ""}`}
                onClick={() => {
                  handleReset();
                  setActiveModal("send");
                }}
              >
                <span className="tab-label">Transfer</span>
              </button>

              <button
                type="button"
                className={`tab-item ${activeTab === "more" ? "active" : ""}`}
                onClick={() => setActiveModal("statements")}
              >
                <span className="tab-label">More</span>
              </button>
            </nav>
          </div>
        </div>
      </div>

      {/* ========================================================= */}
      {/* MODAL 1: SEND MOMO (ENTER OWN NUMBER, RECIPIENT, AMOUNT)  */}
      {/* ========================================================= */}
      {activeModal === "send" && (
        <div className="modal-backdrop" onClick={() => setActiveModal(null)}>
          <div className="modal-sheet-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-header-titles">
                <span className="network-tag">MTN Ghana MoMo</span>
                <h3>Transfer Funds</h3>
              </div>
              <button
                type="button"
                className="close-panel-btn"
                onClick={() => setActiveModal(null)}
              >
                <CrossIcon />
              </button>
            </div>

            {error && (
              <div className="form-error-alert">
                <AlertIcon />
                <span>{error}</span>
              </div>
            )}
            {accountNotice && (
              <div className="privacy-signal-note" role="status">
                {accountNotice}
              </div>
            )}
            {customerAccount?.transfer_mode === "SIMULATION_ONLY" && (
              <div className="privacy-signal-note" role="status">
                Simulation-only pilot: transactions update demo balances only.
                No request is sent to MTN and no money moves.
              </div>
            )}
            {/* STEP 1: TRANSACTION DETAILS FORM */}
            {step === "form" && !customerAccount && (
              <div className="transaction-form">
                <div className="challenge-mode-tabs">
                  <button
                    type="button"
                    className={`mode-tab ${customerView === "signin" ? "active" : ""}`}
                    onClick={() => setCustomerView("signin")}
                  >
                    Sign in
                  </button>
                  <button
                    type="button"
                    className={`mode-tab ${customerView === "register" ? "active" : ""}`}
                    onClick={() => setCustomerView("register")}
                  >
                    Create sandbox wallet
                  </button>
                </div>

                <div className="privacy-signal-note">
                  Sandbox only. Use a fictional name and identity number, and
                  an MTN-provided test wallet number. Never enter a real
                  passport, Ghana Card, or another person’s data. We store the
                  test profile, email, device ID, and server-observed IP;
                  identity numbers and PINs are salted hashes. Location is
                  optional and, if you opt in, is rounded to about 1 km.
                  Profile and security-signal deletion is immediate; linked
                  transaction/audit records remain until the operator-managed
                  pilot data wipe, no later than 30 days after the pilot ends.
                  The wipe is
                  not automatic.
                </div>

                {customerView === "signin" && (
                  <form onSubmit={handleCustomerLogin} className="transaction-form">
                    <div className="field-block">
                      <label htmlFor="sandbox-login-phone" className="field-label">
                        Ghana mobile number
                      </label>
                      <input
                        id="sandbox-login-phone"
                        type="tel"
                        required
                        autoComplete="username"
                        value={loginNumber}
                        onChange={(event) => setLoginNumber(event.target.value)}
                        placeholder="0241234567"
                        className="standard-input"
                      />
                    </div>
                    <div className="field-block">
                      <label htmlFor="sandbox-login-pin" className="field-label">
                        Four-digit wallet PIN
                      </label>
                      <input
                        id="sandbox-login-pin"
                        type="password"
                        required
                        inputMode="numeric"
                        autoComplete="current-password"
                        maxLength={4}
                        value={loginPin}
                        onChange={(event) =>
                          setLoginPin(event.target.value.replace(/\D/g, "").slice(0, 4))
                        }
                        className="standard-input"
                      />
                    </div>
                    <p className="field-hint">
                      Existing demo registry numbers still work. If your number
                      is not registered, create a sandbox wallet first.
                    </p>
                    <button type="submit" className="primary-action-btn" disabled={loading}>
                      {loading ? "Signing in…" : "Sign in"}
                    </button>
                  </form>
                )}

                {customerView === "register" && (
                  <form
                    onSubmit={handleRegistrationSubmit}
                    className="transaction-form"
                  >
                    <div className="field-block">
                      <label htmlFor="register-name" className="field-label">
                        Full name
                      </label>
                      <input
                        id="register-name"
                        required
                        autoComplete="name"
                        value={registration.full_name}
                        onChange={(event) =>
                          setRegistration((current) => ({
                            ...current,
                            full_name: event.target.value,
                          }))
                        }
                        className="standard-input"
                      />
                    </div>
                    <div className="field-block">
                      <label htmlFor="register-phone" className="field-label">
                        MTN sandbox test number
                      </label>
                      <input
                        id="register-phone"
                        type="tel"
                        required
                        autoComplete="tel"
                        value={registration.phone_number}
                        onChange={(event) =>
                          setRegistration((current) => ({
                            ...current,
                            phone_number: event.target.value,
                          }))
                        }
                        placeholder="Number provided by MTN for sandbox testing"
                        className="standard-input"
                      />
                      <p className="field-hint">
                        Use only the test number assigned for your MTN sandbox
                wallet; do not enter a personal number.
                      </p>
                    </div>
                    <div className="field-block">
                      <label htmlFor="register-id-type" className="field-label">
                        Synthetic identity document
                      </label>
                      <select
                        id="register-id-type"
                        value={registration.id_type}
                        onChange={(event) =>
                          setRegistration((current) => ({
                            ...current,
                            id_type: event.target.value,
                          }))
                        }
                        className="standard-input"
                      >
                        <option value="PASSPORT">Passport</option>
                        <option value="GHANA_CARD">Ghana Card</option>
                      </select>
                      <input
                        id="register-id-number"
                        required
                        autoComplete="off"
                        value={registration.id_number}
                        onChange={(event) =>
                          setRegistration((current) => ({
                            ...current,
                            id_number: event.target.value,
                          }))
                        }
                        placeholder="TEST-ID-0001"
                        className="standard-input"
                      />
                    </div>
                    <div className="field-block">
                      <label htmlFor="register-email" className="field-label">
                        Controlled test email
                      </label>
                      <input
                        id="register-email"
                        type="email"
                        required
                        autoComplete="email"
                        value={registration.email}
                        onChange={(event) =>
                          setRegistration((current) => ({
                            ...current,
                            email: event.target.value,
                          }))
                        }
                        className="standard-input"
                      />
                      <p className="field-hint">
                        Verify this address before you use MEDIUM-risk transactions.
                      </p>
                    </div>
                    <div className="field-block">
                      <label htmlFor="register-pin" className="field-label">
                        Choose a four-digit demo wallet PIN
                      </label>
                      <input
                        id="register-pin"
                        type="password"
                        required
                        inputMode="numeric"
                        maxLength={4}
                        value={registration.pin}
                        onChange={(event) =>
                          setRegistration((current) => ({
                            ...current,
                            pin: event.target.value.replace(/\D/g, "").slice(0, 4),
                          }))
                        }
                        className="standard-input"
                      />
                    </div>
                    <label className="location-required-note pilot-consent-control">
                      <input
                        type="checkbox"
                        checked={privacyNoticeAccepted}
                        onChange={(event) =>
                          setPrivacyNoticeAccepted(event.target.checked)
                        }
                        required
                      />
                      <span>
                        I accept the sandbox notice. I will use fictional
                        identity details and an MTN sandbox test number. I will
                        use only an email inbox I control if I need verification
                        codes.
                      </span>
                    </label>
                    <label className="privacy-signal-note pilot-consent-control">
                      <input
                        type="checkbox"
                        checked={shareCoarseLocation}
                        onChange={(event) =>
                          setShareCoarseLocation(event.target.checked)
                        }
                      />
                      <span>
                        Optional: share coarse location for this enrollment to
                        test location-risk signals. If selected, the browser
                        will request location permission; otherwise no GPS
                        location is requested or sent.
                      </span>
                    </label>
                    <button type="submit" className="primary-action-btn" disabled={loading}>
                      {loading ? "Creating wallet…" : "Create sandbox wallet"}
                    </button>
                  </form>
                )}
              </div>
            )}

            {step === "form" && customerAccount && (
              <form onSubmit={handleProceedToAuth} className="transaction-form">
                {/* Transfer Channel Selector */}
                <div className="field-block">
                  <label className="field-label">Transfer Channel</label>
                  <div className="channel-selector-row">
                    {TRANSACTION_CHANNELS.map((ch) => (
                      <button
                        key={ch.id}
                        type="button"
                        className={`channel-btn ${transactionType === ch.id ? "active" : ""}`}
                        onClick={() => {
                          setTransactionType(ch.id);
                          if (ch.id === "CASH_OUT" || ch.id === "CASH_IN") {
                            setRecipientNumber("BANK-ACC-7001");
                          } else {
                            setRecipientNumber("");
                          }
                        }}
                      >
                        <strong>{ch.label}</strong>
                        <small>{ch.sub}</small>
                      </button>
                    ))}
                  </div>
                </div>
                {transactionType !== "TRANSFER" && (
                  <p className="field-hint">
                    Bank-to-wallet and wallet-to-bank are local demo scenarios.
                    Only wallet-to-wallet sends a request to the MTN sandbox.
                  </p>
                )}

                <div className="field-block account-session-card">
                  <span className="field-label">Signed-in sandbox wallet</span>
                  <strong>{customerAccount.name} · {senderNumber}</strong>
                  <div className="account-session-actions">
                    <button
                      type="button"
                      className="text-button"
                      onClick={signOutCustomer}
                    >
                      Sign out
                    </button>
                    <button
                      type="button"
                      className="text-button account-delete-button"
                      onClick={deleteSandboxAccount}
                      disabled={loading}
                    >
                      Delete sandbox account
                    </button>
                  </div>
                </div>

                {/* RECIPIENT NUMBER */}
                <div className="field-block">
                  <label htmlFor="recipient-input" className="field-label">
                    {transactionType === "TRANSFER"
                      ? "Recipient Mobile Number"
                      : "Demo Bank Account"}
                  </label>
                  <div className="input-with-loader">
                    <input
                      id="recipient-input"
                      type="text"
                      required
                      value={recipientNumber}
                      onChange={(e) => setRecipientNumber(e.target.value)}
                      placeholder={
                        transactionType === "TRANSFER"
                          ? "e.g. 0242002002"
                          : "e.g. BANK-ACC-7001"
                      }
                      className="standard-input"
                    />
                    {recipientSearching && <span className="input-spinner" />}
                  </div>

                  {/* Standard Recipient Inquiry Confirmation */}
                  {recipientName && (
                    <div className="recipient-verified-badge">
                      <CheckIcon />
                      <span>Verified Recipient: <strong>{recipientName}</strong></span>
                    </div>
                  )}
                </div>

                {/* AMOUNT INPUT */}
                <div className="field-block">
                  <label htmlFor="amount-input" className="field-label">
                    Amount ({displayCurrency})
                  </label>
                  <div className="amount-input-group">
                    <span className="currency-prefix">{displayCurrency}</span>
                    <input
                      id="amount-input"
                      type="number"
                      step="0.01"
                      min="0.10"
                      required
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      placeholder="0.00"
                      className="amount-input-field"
                    />
                  </div>
                </div>

                <label className="privacy-signal-note pilot-consent-control">
                  <input
                    type="checkbox"
                    checked={shareCoarseLocation}
                    onChange={(event) =>
                      setShareCoarseLocation(event.target.checked)
                    }
                  />
                  <span>
                    Optional: share coarse location for this transaction to
                    test location-risk signals. If selected, the browser will
                    request location permission; otherwise no GPS location is
                    requested or sent.
                    {locationStatus === "denied" &&
                      " Location access is blocked; you can continue without it."}
                  </span>
                </label>

                <button
                  type="submit"
                  disabled={loading}
                  className="primary-action-btn"
                >
                  {loading ? "Processing..." : "Continue to Authorization"}
                </button>
              </form>
            )}

            {/* STEP 2: MO MO PIN KEYPAD SCREEN */}
            {step === "auth" && (
              <div className="pin-screen">
                <div className="pin-screen-header">
                  <LockIcon />
                  <h4>
                    {transactionType === "TRANSFER"
                      ? "Enter 4-Digit MoMo PIN"
                      : "Enter 6-Digit Demo OTP"}
                  </h4>
                  <p>
                    Authorizing transfer of <strong>{displayCurrency} {parseFloat(amount).toFixed(2)}</strong> to{" "}
                    <strong>{recipientName ? `${recipientName} (${recipientNumber})` : recipientNumber}</strong>
                  </p>
                </div>

                <div className="pin-dots-display">
                  {Array.from(
                    { length: transactionType === "TRANSFER" ? 4 : 6 },
                    (_, idx) => (
                    <div
                      key={idx}
                      className={`pin-circle ${authPin.length > idx ? "active" : ""}`}
                    />
                    ),
                  )}
                </div>

                {transactionType === "TRANSFER" ? (
                  <div className="keypad-layout">
                    {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((digit) => (
                      <button
                        key={digit}
                        type="button"
                        className="keypad-button"
                        onClick={() => handleKeypadPress(digit.toString())}
                      >
                        {digit}
                      </button>
                    ))}
                    <button
                      type="button"
                      className="keypad-button keypad-btn-func"
                      onClick={handleKeypadClear}
                    >
                      Clear
                    </button>
                    <button
                      type="button"
                      className="keypad-button"
                      onClick={() => handleKeypadPress("0")}
                    >
                      0
                    </button>
                    <button
                      type="button"
                      className="keypad-button keypad-btn-func"
                      onClick={handleKeypadBackspace}
                    >
                      Delete
                    </button>
                  </div>
                ) : (
                  <div className="field-block">
                    <label htmlFor="demo-otp-input" className="field-label">
                      Six-digit code sent by SMS
                    </label>
                    <input
                      id="demo-otp-input"
                      type="password"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      maxLength={6}
                      value={authPin}
                      onChange={(event) =>
                        setAuthPin(event.target.value.replace(/\D/g, "").slice(0, 6))
                      }
                      className="large-code-input"
                    />
                  </div>
                )}

                <div className="form-button-row">
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={() => setStep("form")}
                    disabled={loading}
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    className="primary-action-btn"
                    onClick={handleAuthorizePayment}
                    disabled={
                      loading ||
                      authPin.length !== (transactionType === "TRANSFER" ? 4 : 6)
                    }
                  >
                    {loading ? "Authorizing..." : "Confirm & Send"}
                  </button>
                </div>
              </div>
            )}

            {/* STEP 2.5: AI FRAUD RADAR SCANNING */}
            {step === "evaluating" && (
              <div className="scanning-view">
                <div className="scanner-spinner" />
                <h4>Analyzing Real-Time Fraud Signals</h4>
                <p>
                  Evaluating transaction velocity, device hardware signature, and IP location with
                  the Adaptive Isolation Forest model.
                </p>
              </div>
            )}

            {/* STEP 3: STEP-UP IDENTITY CHALLENGE */}
            {step === "stepup" && stepUpChallenge && (
              <div className="stepup-view">
                <div className="stepup-header-box">
                  <ShieldIcon />
                  <div>
                    <h4>Identity Verification Required</h4>
                    <p>
                      This transaction is pending additional verification, not declined.
                      Risk tier: MEDIUM
                      {stepUpChallenge.risk_score != null &&
                        ` (${(Number(stepUpChallenge.risk_score) * 100).toFixed(0)}%)`}.
                      Choose one method to continue.
                    </p>
                  </div>
                </div>

                {stepUpChallenge.reasons?.length > 0 && (
                  <div className="reasons-list-card">
                    <span>Detected Security Signals:</span>
                    <ul>
                      {stepUpChallenge.reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="stepup-method-options" role="group" aria-label="Choose verification method">
                  <button
                    type="button"
                    className={`mode-tab ${stepUpMethod === "EMAIL_OTP" ? "active" : ""}`}
                    disabled={loading || !stepUpChallenge.email_available}
                    onClick={() => handleChooseStepUpMethod("EMAIL_OTP")}
                  >
                    {stepUpEmailSent ? "Email code sent" : "Verify by email"}
                  </button>
                  <button
                    type="button"
                    className={`mode-tab ${stepUpMethod === "PASSPORT" ? "active" : ""}`}
                    disabled={loading || !stepUpChallenge.passport_available}
                    onClick={() => handleChooseStepUpMethod("PASSPORT")}
                  >
                    Verify with passport / ID
                  </button>
                </div>
                {!stepUpChallenge.email_available && (
                  <p className="stepup-method-note">
                    Email verification is unavailable for this wallet. Use its saved passport/ID.
                  </p>
                )}
                {!stepUpChallenge.passport_available && (
                  <p className="stepup-method-note">
                    No passport/ID is saved for this wallet. Use email verification.
                  </p>
                )}
                {stepUpMethod === "EMAIL_OTP" && (
                  <form onSubmit={handleVerifyStepUp} className="stepup-form-body">
                    <p className="stepup-method-note" role="status">
                      {stepUpEmailMessage ||
                        `The code will be sent to ${stepUpChallenge.masked_email || "the email saved on your wallet"}.`}
                    </p>
                    {stepUpEmailSent && (
                      <p
                        className="stepup-method-note"
                        aria-live={stepUpSecondsLeft <= 0 ? "polite" : "off"}
                      >
                        {stepUpSecondsLeft > 0
                          ? `Code expires in ${stepUpCodeCountdown}.`
                          : "This code has expired. Request a new code to continue."}
                        {stepUpResendSecondsLeft > 0 &&
                          ` You can request another in ${stepUpResendCountdown}.`}
                      </p>
                    )}
                    <div className="field-block">
                      <label htmlFor="stepup-credential" className="field-label">
                        Six-digit email code
                      </label>
                      <input
                        id="stepup-credential"
                        type="password"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        maxLength={6}
                        value={stepUpInput}
                        onChange={(e) =>
                          setStepUpInput(e.target.value.replace(/\D/g, "").slice(0, 6))
                        }
                        placeholder="Six-digit email code"
                        className="large-code-input"
                        disabled={!stepUpEmailSent || stepUpSecondsLeft <= 0}
                      />
                    </div>
                    <button
                      type="button"
                      className="secondary-btn"
                      onClick={() => handleChooseStepUpMethod("EMAIL_OTP", true)}
                      disabled={loading || stepUpResendSecondsLeft > 0}
                    >
                      {stepUpEmailSent ? "Resend code" : "Send code"}
                    </button>
                    <button
                      type="submit"
                      className="primary-action-btn"
                      disabled={
                        loading ||
                        !stepUpEmailSent ||
                        stepUpSecondsLeft <= 0 ||
                        stepUpInput.length !== 6
                      }
                    >
                      {loading ? "Verifying..." : "Verify & Authorize"}
                    </button>
                  </form>
                )}
                {stepUpMethod === "PASSPORT" && (
                  <form onSubmit={handleVerifyStepUp} className="stepup-form-body">
                    <div className="field-block">
                      <label htmlFor="stepup-credential" className="field-label">
                        Passport or ID number saved on your wallet
                      </label>
                      <input
                        id="stepup-credential"
                        type="password"
                        autoComplete="off"
                        value={stepUpInput}
                        onChange={(e) => setStepUpInput(e.target.value)}
                        placeholder="Enter your passport / ID number"
                        className="large-code-input"
                      />
                    </div>
                    <button
                      type="submit"
                      className="primary-action-btn"
                      disabled={loading || !stepUpInput.trim()}
                    >
                      {loading ? "Verifying..." : "Verify & Authorize"}
                    </button>
                  </form>
                )}
                {error && <div className="form-error-alert" role="alert">{error}</div>}
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={handleReset}
                  disabled={loading}
                >
                  Cancel transfer
                </button>
              </div>
            )}

            {/* STEP 4: DIGITAL RECEIPT */}
            {step === "receipt" && receipt && (
              <div className="receipt-view">
                {["APPROVED", "PENDING", "COMPLETED"].includes(receipt.status) ? (
                  <div>
                    <div className="receipt-success-banner">
                      <div className="receipt-badge-circle">
                        <CheckIcon />
                      </div>
                      <h4>
                        {receipt.status === "PENDING"
                          ? "MTN Status Pending"
                          : receipt.status === "COMPLETED"
                            ? "Transfer Confirmed"
                            : "Transaction Approved"}
                      </h4>
                      <p>
                        {receipt.status === "PENDING"
                          ? "MTN accepted the request, but the final outcome is not confirmed. Check the status before retrying."
                          : receipt.status === "COMPLETED"
                            ? "MTN confirmed that the transfer completed."
                            : receipt.live_mtn
                              ? "Transfer accepted by MTN."
                              : "Demo transaction completed successfully."}
                      </p>
                    </div>

                    <div className="receipt-table">
                      <div className="receipt-data-row">
                        <span>Transaction Reference:</span>
                        <code>{receipt.transaction_reference}</code>
                      </div>
                      {receipt.financialTransactionId && (
                        <div className="receipt-data-row row-highlight">
                          <span>MTN Financial ID:</span>
                          <strong>{receipt.financialTransactionId}</strong>
                        </div>
                      )}
                      <div className="receipt-data-row">
                        <span>Amount Transferred:</span>
                        <strong className="amount-text">{displayCurrency} {parseFloat(amount).toFixed(2)}</strong>
                      </div>
                      <div className="receipt-data-row">
                        <span>Sender Number:</span>
                        <span>{senderNumber}</span>
                      </div>
                      <div className="receipt-data-row">
                        <span>Recipient:</span>
                        <strong>{recipientName ? `${recipientName} (${recipientNumber})` : recipientNumber}</strong>
                      </div>
                      <div className="receipt-data-row">
                        <span>Fraud Risk Assessment:</span>
                        <span className="risk-tag risk-low">
                          {receipt.risk_level || "LOW"} (Score: {receipt.risk_score ? (receipt.risk_score * 100).toFixed(0) : "12"}%)
                        </span>
                      </div>
                    </div>
                    {receipt.status === "PENDING" && (
                      <button
                        type="button"
                        className="primary-action-btn"
                        onClick={checkMtnTransferStatus}
                        disabled={loading}
                      >
                        {loading ? "Checking..." : "Check MTN transfer status"}
                      </button>
                    )}
                  </div>
                ) : (
                  <div>
                    <div className="receipt-declined-banner">
                      <div className="receipt-declined-circle">
                        <CrossIcon />
                      </div>
                      <h4>
                        {receipt.status === "NOT_SUBMITTED"
                          ? "Transfer Not Sent"
                          : receipt.status === "FAILED"
                            ? "Transfer Failed"
                            : "Transaction Declined"}
                      </h4>
                      <p>
                        {receipt.message ||
                          (receipt.status === "FAILED"
                            ? "MTN did not accept the transfer."
                            : "Blocked by fraud defense engine. Money did not leave your wallet.")}
                      </p>
                    </div>

                    <div className="receipt-table">
                      <div className="receipt-data-row">
                        <span>Decision:</span>
                        <strong className="text-danger">{receipt.decision || "BLOCKED"}</strong>
                      </div>
                      <div className="receipt-data-row">
                        <span>Risk Tier:</span>
                        <span
                          className={`risk-tag risk-${String(
                            receipt.risk_level || "unknown",
                          ).toLowerCase()}`}
                        >
                          {receipt.risk_level || "UNKNOWN"}
                        </span>
                      </div>
                      <div className="receipt-data-row">
                        <span>Risk Score:</span>
                        <strong>
                          {receipt.risk_score != null
                            ? `${(Number(receipt.risk_score) * 100).toFixed(0)}%`
                            : "Unavailable"}
                        </strong>
                      </div>
                      {receipt.reasons?.length > 0 && (
                        <div className="reasons-block">
                          <span>Reasons Flagged:</span>
                          <ul>
                            {receipt.reasons.map((r, i) => (
                              <li key={i}>{r}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <div className="form-button-row">
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={() => setActiveModal(null)}
                  >
                    Close
                  </button>
                  <button
                    type="button"
                    className="primary-action-btn"
                    onClick={handleReset}
                  >
                    New Transfer
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ========================================================= */}
      {/* MODAL 2: ACCOUNT STATEMENTS                               */}
      {/* ========================================================= */}
      {activeModal === "statements" && (
        <div className="modal-backdrop" onClick={() => setActiveModal(null)}>
          <div className="modal-sheet-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-header-titles">
                <span className="network-tag">Transaction History</span>
                <h3>Account Statements</h3>
              </div>
              <button
                type="button"
                className="close-panel-btn"
                onClick={() => setActiveModal(null)}
              >
                <CrossIcon />
              </button>
            </div>

            <div className="statements-feed">
              {recentTransactions.map((tx) => (
                <div key={tx.id} className="statement-row-card">
                  <div className="statement-icon-box">
                    <SendIcon />
                  </div>
                  <div className="statement-info">
                    <strong>{tx.recipient}</strong>
                    <span>{tx.date}</span>
                    <small>Ref: {tx.id}</small>
                  </div>
                  <div className="statement-right">
                    <strong>-{tx.amount}</strong>
                    <span className="statement-status-pill">{tx.status}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ========================================================= */}
      {/* MODAL 3: EMERGENCY FREEZE WALLET                          */}
      {/* ========================================================= */}
      {activeModal === "freeze" && (
        <div className="modal-backdrop" onClick={() => setActiveModal(null)}>
          <div className="modal-sheet-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-header-titles">
                <span className="network-tag tag-danger">Security Operations</span>
                <h3>Emergency Account Lock</h3>
              </div>
              <button
                type="button"
                className="close-panel-btn"
                onClick={() => setActiveModal(null)}
              >
                <CrossIcon />
              </button>
            </div>

            <div className="freeze-dialog-content">
              <div className="freeze-alert-box">
                <AlertIcon />
                <div>
                  <strong>Suspected Fraud or Stolen SIM?</strong>
                  <p>
                    Activating an emergency freeze immediately halts all transfers from this number
                    and triggers an incident review in the Security Operations Center.
                  </p>
                </div>
              </div>

              {freezeMessage && (
                <div className="form-success-alert">
                  <CheckIcon />
                  <span>{freezeMessage}</span>
                </div>
              )}

              <button
                type="button"
                className="freeze-action-btn"
                disabled={loading || walletFrozen}
                onClick={handleEmergencyFreeze}
              >
                {loading ? "Locking..." : walletFrozen ? "Wallet Is Locked" : "Lock My MoMo Wallet Immediately"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
