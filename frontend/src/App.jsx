import { useEffect, useState } from "react";
import AdaptiveIntelligence from "./AdaptiveIntelligence.jsx";
import AdminDashboard from "./AdminDashboard.jsx";
import AuditLogs from "./AuditLogs.jsx";
import LiveDashboard from "./LiveDashboard.jsx";
import MomoSimulator from "./MomoSimulator.jsx";
import TransactionAssessment from "./TransactionAssessment.jsx";
import {
  clearAdminToken,
  fetchDemoUsers,
  getAdminToken,
  getCurrentUser,
  loginAdminUser,
  setCurrentUser,
  verifyAdminToken,
} from "./adminApi.js";
import "./App.css";

function AdminConsole({ currentUser }) {
  return (
    <div className="admin-console">
      <LiveDashboard />
      <AdminDashboard currentUser={currentUser} />
    </div>
  );
}

const PAGES = [
  { id: "dashboard", label: "Overview", component: AdminConsole },
  { id: "simulator", label: "MoMo simulator", component: MomoSimulator },
  {
    id: "adaptive",
    label: "Adaptive intelligence",
    component: AdaptiveIntelligence,
  },
  { id: "audit", label: "Audit trail", component: AuditLogs },
  {
    id: "assessment",
    label: "Risk assessment",
    component: TransactionAssessment,
  },
];

const PRESET_DEMO_CREDENTIALS = [
  {
    role: "admin",
    label: "Administrator",
    username: "admin",
    password: "Admin@MoMo2026!",
    badgeClass: "role-admin",
    tag: "Full Controls & Freeze",
  },
  {
    role: "analyst",
    label: "Lead Fraud Analyst",
    username: "analyst_kwame",
    password: "Analyst@MoMo2026!",
    badgeClass: "role-analyst",
    tag: "Case Investigations & Scoring",
  },
  {
    role: "viewer",
    label: "Compliance Viewer",
    username: "viewer_ama",
    password: "Viewer@MoMo2026!",
    badgeClass: "role-viewer",
    tag: "Read-Only & Audit Integrity",
  },
];

function App() {
  const [page, setPage] = useState("dashboard");
  const [menuOpen, setMenuOpen] = useState(false);
  const [adminToken, setAdminTokenState] = useState(getAdminToken);
  const [currentUser, setCurrentUserState] = useState(getCurrentUser);
  const [authMode, setAuthMode] = useState("named"); // "named" or "token"
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("Admin@MoMo2026!");
  const [loginToken, setLoginToken] = useState("");
  const [authError, setAuthError] = useState("");
  const [authLoading, setAuthLoading] = useState(false);

  const activePage = PAGES.find((item) => item.id === page);
  const Active = activePage.component;
  const needsAdmin = page !== "simulator";

  useEffect(() => {
    // If we have a token but no user object, fetch / verify it
    if (adminToken && !currentUser) {
      verifyAdminToken(adminToken)
        .then((payload) => {
          if (payload.user) {
            setCurrentUserState(payload.user);
          }
        })
        .catch(() => {
          clearAdminToken();
          setAdminTokenState("");
          setCurrentUserState(null);
        });
    }
  }, [adminToken, currentUser]);

  async function handleNamedLogin(event) {
    event.preventDefault();
    setAuthLoading(true);
    setAuthError("");
    try {
      const payload = await loginAdminUser(username, password);
      setAdminTokenState(payload.token);
      setCurrentUserState(payload.user);
      setPassword("");
    } catch (error) {
      setAuthError(error.message || "Unable to sign in.");
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleTokenLogin(event) {
    event.preventDefault();
    setAuthLoading(true);
    setAuthError("");
    try {
      const payload = await verifyAdminToken(loginToken);
      setAdminTokenState(loginToken);
      setCurrentUserState(payload.user || { username: "admin_master", role: "admin", display_name: "Admin Master" });
      setLoginToken("");
    } catch (error) {
      setAuthError(error.message || "Unable to authenticate admin token.");
    } finally {
      setAuthLoading(false);
    }
  }

  function handleQuickFill(preset) {
    setAuthMode("named");
    setUsername(preset.username);
    setPassword(preset.password);
    setAuthError("");
  }

  function logout() {
    clearAdminToken();
    setAdminTokenState("");
    setCurrentUserState(null);
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <button
          className="app-brand"
          type="button"
          onClick={() => {
            setPage("dashboard");
            setMenuOpen(false);
          }}
        >
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 40 40" role="img">
              <path
                d="M20 4.5 33 9v9.4c0 8.1-5.4 13.8-13 17.1-7.6-3.3-13-9-13-17.1V9l13-4.5Z"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinejoin="round"
              />
              <path
                d="m13.2 20.3 4.3 4.2 9.4-10"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="brand-copy">
            <strong>MoMo Sentinel</strong>
            <small>Fraud operations</small>
          </span>
        </button>
        <nav
          className={`app-nav ${menuOpen ? "is-open" : ""}`}
          id="main-navigation"
          aria-label="Main navigation"
        >
          {PAGES.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`app-nav-link ${page === item.id ? "active" : ""}`}
              aria-current={page === item.id ? "page" : undefined}
              onClick={() => {
                setPage(item.id);
                setMenuOpen(false);
              }}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <button
          className="app-menu-toggle"
          type="button"
          aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
          aria-expanded={menuOpen}
          aria-controls="main-navigation"
          onClick={() => setMenuOpen((isOpen) => !isOpen)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            {menuOpen ? (
              <path d="m6 6 12 12M18 6 6 18" />
            ) : (
              <path d="M4 7h16M4 12h16M4 17h16" />
            )}
          </svg>
        </button>
        {adminToken ? (
          <div className="operator-controls">
            <span className="operator-initials" aria-label="Admin">
              AD
            </span>
            {needsAdmin && (
              <button className="admin-logout" type="button" onClick={logout}>
                Sign out
              </button>
            )}
          </div>
        ) : (
          <span className="prototype-badge">
            <span /> Synthetic demo
          </span>
        )}
      </header>
      <div className="app-content">
        {needsAdmin && !adminToken ? (
          <main className="admin-login">
            <div className="admin-login-layout">
              <section className="login-story" aria-label="About MoMo Sentinel">
                <div className="login-illustration">
                  <svg viewBox="0 0 520 370" role="img" aria-labelledby="illustration-title">
                    <title id="illustration-title">A protected mobile money transaction</title>
                    <defs>
                      <linearGradient id="screenGlow" x1="0" y1="0" x2="1" y2="1">
                        <stop offset="0" stopColor="#fff4b2" />
                        <stop offset="1" stopColor="#ffe066" />
                      </linearGradient>
                    </defs>
                    <path d="M67 287c56-48 91-23 139-45 55-26 83-89 151-77 49 8 71 54 109 71" fill="none" stroke="#fff" strokeOpacity=".2" strokeWidth="2" strokeDasharray="5 9" />
                    <circle cx="93" cy="113" r="5" fill="#ffcc00" />
                    <circle cx="421" cy="244" r="6" fill="#ffcc00" />
                    <circle cx="355" cy="59" r="4" fill="#9ed8e8" />
                    <rect x="81" y="82" width="144" height="218" rx="22" fill="#fff" fillOpacity=".1" stroke="#fff" strokeOpacity=".3" strokeWidth="2" transform="rotate(-8 81 82)" />
                    <rect x="101" y="101" width="107" height="174" rx="14" fill="#f8faf4" transform="rotate(-8 101 101)" />
                    <rect x="121" y="128" width="64" height="7" rx="3.5" fill="#c4d3d2" transform="rotate(-8 121 128)" />
                    <rect x="119" y="148" width="72" height="45" rx="9" fill="url(#screenGlow)" transform="rotate(-8 119 148)" />
                    <path d="m139 167 10 9 19-21" fill="none" stroke="#073451" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" transform="rotate(-8 139 167)" />
                    <rect x="123" y="211" width="56" height="5" rx="2.5" fill="#d5dfdd" transform="rotate(-8 123 211)" />
                    <rect x="126" y="226" width="39" height="5" rx="2.5" fill="#d5dfdd" transform="rotate(-8 126 226)" />
                    <circle cx="336" cy="178" r="90" fill="#0c5272" stroke="#73b8c8" strokeOpacity=".6" strokeWidth="2" />
                    <circle cx="336" cy="178" r="69" fill="none" stroke="#9ed8e8" strokeOpacity=".4" strokeWidth="1.5" strokeDasharray="3 8" />
                    <path d="M336 112 381 128v31c0 29-19 49-45 60-26-11-45-31-45-60v-31l45-16Z" fill="#ffcc00" />
                    <path d="m315 159 15 15 30-32" fill="none" stroke="#073451" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M254 280h166" stroke="#fff" strokeOpacity=".22" strokeWidth="2" strokeLinecap="round" />
                    <rect x="286" y="294" width="100" height="30" rx="15" fill="#fff" fillOpacity=".1" stroke="#fff" strokeOpacity=".2" />
                    <circle cx="303" cy="309" r="4" fill="#8bddaa" />
                    <path d="M316 309h54" stroke="#e4f4f4" strokeOpacity=".8" strokeWidth="3" strokeLinecap="round" />
                  </svg>
                </div>
                <div className="login-story-copy">
                  <span className="story-tag"><span /> ROLE-BASED ACCESS & AUDIT INTEGRITY</span>
                  <h2>Every transfer<br />has a story.</h2>
                  <p>
                    A protected operational portal for Ghana mobile money.
                    Equipped with named-user authentication, granular RBAC roles,
                    and tamper-evident cryptographic audit logs.
                  </p>
                  <div className="story-footnote">
                    <span className="story-line" />
                    <span>Bank of Ghana FinTech Architecture · Production-Ready Controls</span>
                  </div>
                </div>
              </section>

              <div className="admin-login-card">
                <div className="login-card-heading">
                  <span className="login-kicker">OPERATIONS PORTAL</span>
                  <span className="secure-caption">
                    <svg viewBox="0 0 16 16" aria-hidden="true">
                      <rect x="3" y="7" width="10" height="7" rx="2" />
                      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
                    </svg>
                    RBAC Protected
                  </span>
                </div>
                <h1>Welcome back</h1>
                <p className="login-description">
                  Sign in with your assigned role to access fraud operations,
                  investigate alerts, or audit system transactions.
                </p>

                {/* Authentication Mode Switcher */}
                <div className="auth-mode-switch">
                  <button
                    type="button"
                    className={`auth-mode-btn ${authMode === "named" ? "active" : ""}`}
                    onClick={() => { setAuthMode("named"); setAuthError(""); }}
                  >
                    User Account (RBAC)
                  </button>
                  <button
                    type="button"
                    className={`auth-mode-btn ${authMode === "token" ? "active" : ""}`}
                    onClick={() => { setAuthMode("token"); setAuthError(""); }}
                  >
                    Direct Token
                  </button>
                </div>

                {authMode === "named" ? (
                  <form onSubmit={handleNamedLogin}>
                    {/* Role Quick Selector for easy testing */}
                    <div className="demo-role-quickselect">
                      <span className="demo-role-quickselect-label">
                        1-Click Role Login
                      </span>
                      {PRESET_DEMO_CREDENTIALS.map((preset) => (
                        <button
                          key={preset.role}
                          type="button"
                          className="demo-role-btn"
                          onClick={() => handleQuickFill(preset)}
                          title={`Select ${preset.label}`}
                        >
                          <span>
                            <strong>{preset.label}</strong>
                            <small style={{ display: "block", color: "#64748b", fontSize: "0.68rem" }}>
                              {preset.tag}
                            </small>
                          </span>
                          <span className={`role-badge ${preset.badgeClass}`}>
                            {preset.role}
                          </span>
                        </button>
                      ))}
                    </div>

                    <label htmlFor="admin-username">Username</label>
                    <input
                      id="admin-username"
                      type="text"
                      autoComplete="username"
                      value={username}
                      onChange={(event) => setUsername(event.target.value)}
                      placeholder="e.g. admin or analyst_kwame"
                      required
                    />

                    <label htmlFor="admin-password">Password</label>
                    <input
                      id="admin-password"
                      type="password"
                      autoComplete="current-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      placeholder="Enter password"
                      required
                    />

                    {authError && <p className="auth-error" role="alert">{authError}</p>}

                    <button type="submit" disabled={authLoading}>
                      {authLoading ? "Authenticating…" : "Sign in to console"}
                      {!authLoading && <span aria-hidden="true">→</span>}
                    </button>
                  </form>
                ) : (
                  <form onSubmit={handleTokenLogin}>
                    <label htmlFor="admin-token">Master access token</label>
                    <input
                      id="admin-token"
                      type="password"
                      autoComplete="current-password"
                      value={loginToken}
                      onChange={(event) => setLoginToken(event.target.value)}
                      placeholder="Enter MOMO_ADMIN_TOKEN"
                      required
                    />

                    {authError && <p className="auth-error" role="alert">{authError}</p>}

                    <button type="submit" disabled={authLoading}>
                      {authLoading ? "Checking access…" : "Continue with token"}
                      {!authLoading && <span aria-hidden="true">→</span>}
                    </button>
                  </form>
                )}

                <div className="login-divider"><span>DEMO ENVIRONMENT</span></div>
                <p className="login-note">
                  Pre-configured demo users enforce role privileges (Admin, Analyst,
                  Viewer) with PBKDF2 hashing, session tokens, and append-only HMAC
                  audit trails.
                </p>
              </div>
            </div>
          </main>
        ) : (
          <Active currentUser={currentUser} />
        )}
      </div>
      <footer className="app-footer">
        <span className="footer-brand">
          <span className="footer-mark">M</span>
          MoMo Sentinel <span className="footer-separator">/</span> Ghana
        </span>
        <span className="footer-disclaimer">
          <span className="footer-status" /> Prototype workspace
          <span className="footer-separator">·</span>
          Fictional transaction data only
        </span>
      </footer>
    </div>
  );
}

export default App;