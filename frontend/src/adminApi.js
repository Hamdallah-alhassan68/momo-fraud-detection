const API_URL = "http://127.0.0.1:5001";

export function getAdminToken() {
  return sessionStorage.getItem("momo-admin-token") || "";
}

export function setAdminToken(token) {
  sessionStorage.setItem("momo-admin-token", token);
}

export function getCurrentUser() {
  const raw = sessionStorage.getItem("momo-admin-user");
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function setCurrentUser(user) {
  if (!user) {
    sessionStorage.removeItem("momo-admin-user");
  } else {
    sessionStorage.setItem("momo-admin-user", JSON.stringify(user));
  }
}

export function clearAdminToken() {
  sessionStorage.removeItem("momo-admin-token");
  sessionStorage.removeItem("momo-admin-user");
}

export async function loginAdminUser(username, password) {
  const response = await fetch(`${API_URL}/api/admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Authentication failed. Check your credentials.");
  }
  setAdminToken(payload.token);
  setCurrentUser(payload.user);
  return payload;
}

export async function verifyAdminToken(token) {
  const response = await fetch(`${API_URL}/api/admin/auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Admin authentication failed.");
  }
  if (payload.user) {
    setCurrentUser(payload.user);
  }
  return payload;
}

export async function fetchDemoUsers() {
  const response = await fetch(`${API_URL}/api/admin/demo-users`);
  if (!response.ok) {
    return [];
  }
  const payload = await response.json();
  return payload.demo_users || [];
}

export async function verifyAuditChain() {
  const response = await adminFetch("/api/admin/audit-logs/verify");
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "Unable to verify audit log integrity.");
  }
  return response.json();
}

export function adminFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getAdminToken();
  if (token) {
    headers.set("X-Admin-Token", token);
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(`${API_URL}${path}`, { ...options, headers });
}

export function subscribeAdminEvents(onEvent, onConnectionChange = () => {}) {
  const controller = new AbortController();
  let stopped = false;

  async function streamEvents() {
    while (!stopped) {
      try {
        const response = await fetch(`${API_URL}/api/events/stream`, {
          headers: {
            "X-Admin-Token": getAdminToken(),
            Authorization: `Bearer ${getAdminToken()}`,
          },
          signal: controller.signal,
        });
        if (response.status === 401 || response.status === 403 || response.status === 503) {
          onConnectionChange(false);
          return;
        }
        if (!response.ok || !response.body) {
          throw new Error("Admin event stream is unavailable.");
        }

        onConnectionChange(true);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (!stopped) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          const frames = buffer.split("\n\n");
          buffer = frames.pop() || "";
          for (const frame of frames) {
            const data = frame
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trim())
              .join("\n");
            if (!data) continue;
            try {
              onEvent(JSON.parse(data));
            } catch {
              onConnectionChange(false);
            }
          }
        }
        onConnectionChange(false);
      } catch (error) {
        if (error.name === "AbortError") return;
        onConnectionChange(false);
      }
      if (!stopped) await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }

  streamEvents();
  return () => {
    stopped = true;
    controller.abort();
  };
}

export { API_URL };
