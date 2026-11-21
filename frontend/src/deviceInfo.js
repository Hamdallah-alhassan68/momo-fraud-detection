// Rich device and hardware detection with actual brand and model identification

const COMMON_MODELS = {
  // Samsung Galaxy S series
  "SM-S928": "Samsung Galaxy S24 Ultra",
  "SM-S926": "Samsung Galaxy S24+",
  "SM-S921": "Samsung Galaxy S24",
  "SM-S918": "Samsung Galaxy S23 Ultra",
  "SM-S916": "Samsung Galaxy S23+",
  "SM-S911": "Samsung Galaxy S23",
  "SM-S908": "Samsung Galaxy S22 Ultra",
  "SM-S906": "Samsung Galaxy S22+",
  "SM-S901": "Samsung Galaxy S22",
  "SM-G998": "Samsung Galaxy S21 Ultra",
  "SM-G996": "Samsung Galaxy S21+",
  "SM-G991": "Samsung Galaxy S21",
  "SM-G990": "Samsung Galaxy S21 FE",
  "SM-G988": "Samsung Galaxy S20 Ultra",
  "SM-G980": "Samsung Galaxy S20",
  "SM-G780": "Samsung Galaxy S20 FE",
  // Samsung Galaxy A series (widely used in Ghana / West Africa)
  "SM-A546": "Samsung Galaxy A54 5G",
  "SM-A536": "Samsung Galaxy A53 5G",
  "SM-A528": "Samsung Galaxy A52s 5G",
  "SM-A525": "Samsung Galaxy A52",
  "SM-A346": "Samsung Galaxy A34 5G",
  "SM-A336": "Samsung Galaxy A33 5G",
  "SM-A245": "Samsung Galaxy A24",
  "SM-A235": "Samsung Galaxy A23",
  "SM-A156": "Samsung Galaxy A15 5G",
  "SM-A155": "Samsung Galaxy A15",
  "SM-A146": "Samsung Galaxy A14 5G",
  "SM-A145": "Samsung Galaxy A14",
  "SM-A137": "Samsung Galaxy A13",
  "SM-A135": "Samsung Galaxy A13",
  "SM-A125": "Samsung Galaxy A12",
  "SM-A057": "Samsung Galaxy A05s",
  "SM-A055": "Samsung Galaxy A05",
  "SM-A047": "Samsung Galaxy A04s",
  "SM-A035": "Samsung Galaxy A03",
  // Tecno Mobile (widely used in Ghana)
  "CK7N": "Tecno Camon 20 Pro",
  "CK8N": "Tecno Camon 20 Premier",
  "CK6": "Tecno Camon 20",
  "CI6": "Tecno Camon 19",
  "CI8": "Tecno Camon 19 Pro",
  "KJ6": "Tecno Spark 20",
  "KJ5": "Tecno Spark 20C",
  "KI7": "Tecno Spark 10 Pro",
  "KI5": "Tecno Spark 10",
  "KL6": "Tecno Spark 9 Pro",
  "KG5": "Tecno Spark 8C",
  "LH7": "Tecno Pova 5",
  "LG7": "Tecno Pova Neo 2",
  // Infinix Mobile (widely used in Ghana)
  "X6835": "Infinix Hot 30",
  "X6836": "Infinix Hot 30 Play",
  "X6817": "Infinix Hot 12 Play",
  "X6812": "Infinix Hot 11",
  "X688": "Infinix Hot 10 Play",
  "X6711": "Infinix Note 30",
  "X6710": "Infinix Note 30 Pro",
  "X676": "Infinix Note 12",
  "X669": "Infinix Smart 7",
  "X6515": "Infinix Smart 8",
  // Xiaomi & Redmi
  "2312DRA50G": "Redmi Note 13 Pro",
  "23021RAAEG": "Redmi Note 12",
  "2201117TY": "Redmi Note 11",
  "M2101K6G": "Redmi Note 10 Pro",
  // Google Pixel
  "PIXEL 8 PRO": "Google Pixel 8 Pro",
  "PIXEL 8": "Google Pixel 8",
  "PIXEL 7A": "Google Pixel 7a",
  "PIXEL 7 PRO": "Google Pixel 7 Pro",
  "PIXEL 7": "Google Pixel 7",
  "PIXEL 6A": "Google Pixel 6a",
  "PIXEL 6": "Google Pixel 6",
};

function resolveModelCode(rawModel) {
  if (!rawModel) return "";
  const cleaned = rawModel.trim();
  const upper = cleaned.toUpperCase();

  // Exact match
  if (COMMON_MODELS[upper]) {
    return COMMON_MODELS[upper];
  }

  // Prefix match (e.g. SM-A536B -> SM-A536)
  for (const [code, name] of Object.entries(COMMON_MODELS)) {
    if (upper.startsWith(code)) {
      return name;
    }
  }

  // Brand-specific prefixes
  if (upper.startsWith("SM-")) {
    return `Samsung Galaxy (${cleaned})`;
  }
  if (upper.startsWith("TECNO") || /^(CK|CI|KJ|KI|KL|KG|LH|LG)\d/i.test(upper)) {
    return upper.startsWith("TECNO") ? cleaned : `Tecno Mobile (${cleaned})`;
  }
  if (upper.startsWith("INFINIX") || /^X\d{3,4}/i.test(upper)) {
    return upper.startsWith("INFINIX") ? cleaned : `Infinix Mobile (${cleaned})`;
  }
  if (/^REDMI/i.test(upper) || /^POCO/i.test(upper) || /^MI\s/i.test(upper)) {
    return cleaned;
  }
  if (upper.startsWith("PIXEL")) {
    return `Google ${cleaned}`;
  }

  return cleaned;
}

function detectIPhoneModel() {
  if (typeof window === "undefined" || !window.screen) return "Apple iPhone";
  const w = window.screen.width;
  const h = window.screen.height;
  const ratio = window.devicePixelRatio || 1;
  const resKey = `${Math.min(w, h) * ratio}x${Math.max(w, h) * ratio}`;

  const iphoneResMap = {
    "1290x2796": "Apple iPhone 15 Pro Max / 16 Plus",
    "1179x2556": "Apple iPhone 15 / 15 Pro / 16",
    "1284x2778": "Apple iPhone 14 Plus / 13 Pro Max",
    "1170x2532": "Apple iPhone 14 / 13 / 13 Pro / 12 Pro",
    "1125x2436": "Apple iPhone 11 Pro / XS / X",
    "828x1792": "Apple iPhone 11 / XR",
    "1242x2688": "Apple iPhone 11 Pro Max / XS Max",
    "750x1334": "Apple iPhone SE / 8 / 7",
    "1080x1920": "Apple iPhone 8 Plus / 7 Plus",
  };

  return iphoneResMap[resKey] || "Apple iPhone (iOS)";
}

export async function detectActualDeviceName() {
  const userAgent = typeof navigator !== "undefined" ? navigator.userAgent || "" : "";
  const userAgentData = typeof navigator !== "undefined" ? navigator.userAgentData : null;
  let rawModel = "";

  // 1. High-Entropy Client Hints (Chrome / Edge / Chromium on Android)
  if (userAgentData?.getHighEntropyValues) {
    try {
      const details = await userAgentData.getHighEntropyValues(["model", "platform"]);
      rawModel = details.model?.trim() || "";
    } catch {
      rawModel = "";
    }
  }

  // 2. User-Agent parsing fallback for Android builds
  if (!rawModel && /Android/i.test(userAgent)) {
    const buildMatch = userAgent.match(/;\s*([A-Za-z0-9_\-\s]+)\s+Build\//i);
    if (buildMatch && buildMatch[1]) {
      rawModel = buildMatch[1].trim();
    }
  }

  // 3. Resolve parsed model code
  if (rawModel) {
    const resolved = resolveModelCode(rawModel);
    if (resolved) return resolved;
  }

  // 4. iOS Apple devices
  if (/iPad/i.test(userAgent)) {
    return "Apple iPad";
  }
  if (/iPhone/i.test(userAgent)) {
    return detectIPhoneModel();
  }

  // 5. Desktop platforms
  const platform = userAgentData?.platform || (typeof navigator !== "undefined" ? navigator.platform : "") || "";
  const fullCheck = platform + " " + userAgent;

  if (/Win/i.test(fullCheck)) {
    return /Windows NT 10.0/i.test(userAgent) ? "Windows 11/10 PC" : "Windows PC";
  }
  if (/Mac/i.test(fullCheck)) {
    return "Apple Mac";
  }
  if (/Linux/i.test(fullCheck) && !/Android/i.test(fullCheck)) {
    return "Linux PC";
  }
  if (/Android/i.test(userAgent)) {
    return "Android Smartphone";
  }

  return "Mobile Device";
}

export async function detectDeviceLabel() {
  const userAgent = typeof navigator !== "undefined" ? navigator.userAgent || "" : "";
  const deviceName = await detectActualDeviceName();

  const browser = /Edg\//i.test(userAgent)
    ? "Edge"
    : /OPR\//i.test(userAgent)
      ? "Opera"
      : /SamsungBrowser\//i.test(userAgent)
        ? "Samsung Internet"
        : /Firefox\//i.test(userAgent)
          ? "Firefox"
          : /Chrome\//i.test(userAgent)
            ? "Chrome"
            : /Safari\//i.test(userAgent)
              ? "Safari"
              : "Browser";

  return `${deviceName} · ${browser}`.slice(0, 120);
}

export function formatDeviceDisplayName(deviceId, deviceLabel) {
  if (deviceLabel && typeof deviceLabel === "string" && deviceLabel.trim()) {
    return deviceLabel.trim();
  }

  if (!deviceId) return "—";
  const id = String(deviceId).trim();

  // Known mock or legacy identifiers from data sets
  const MOCK_MAP = {
    "MOBILE-AND-001": "Samsung Galaxy A53 (Android)",
    "MOBILE-AND-002": "Tecno Camon 20 (Android)",
    "MOBILE-AND-005": "Infinix Hot 30 (Android)",
    "MOBILE-AND-006": "Infinix Note 30 (Android)",
    "MOBILE-AND-018": "Samsung Galaxy A14 (Android)",
    "MOBILE-AND-031": "Tecno Spark 10 (Android)",
    "MOBILE-AND-042": "Xiaomi Redmi Note 12 (Android)",
    "MOBILE-AND-055": "Samsung Galaxy A24 (Android)",
    "MOBILE-AND-068": "Tecno Spark 20 (Android)",
    "MOBILE-AND-075": "Infinix Hot 12 (Android)",
    "APPLE-IOS-013": "Apple iPhone 14 (iOS)",
    "APPLE-IOS-020": "Apple iPhone 13 (iOS)",
    "APPLE-IOS-045": "Apple iPhone 14 Pro (iOS)",
    "APPLE-IOS-050": "Apple iPhone 15 (iOS)",
    "APPLE-IOS-062": "Apple iPhone 12 (iOS)",
    "APPLE-IOS-078": "Apple iPhone 11 (iOS)",
    "CUSTOM-DEVICE-001": "Primary Smartphone (Mobile)",
    "HACKER-PHONE-999": "Unrecognized Device (Flagged)",
  };

  if (MOCK_MAP[id]) {
    return MOCK_MAP[id];
  }

  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i.test(id)) {
    return "Registered Device";
  }

  return resolveModelCode(id) || id;
}

export function formatLocationDisplayName(location) {
  if (!location) return "USIU-A (Qwetu), Nairobi";
  const str = String(location).trim();

  // Specifically check for USIU-Africa / Qwetu coordinates or names
  if (
    str.includes("-1.22") ||
    str.includes("36.88") ||
    /usiu/i.test(str) ||
    /qwetu/i.test(str)
  ) {
    return "USIU-A (Qwetu), Nairobi";
  }

  // Parse raw coordinate patterns like "Location (-1.22°, 36.88°)" or "(-1.22, 36.88)"
  const match = str.match(/Location\s*\(\s*(-?\d+\.?\d*)[°\s]*,\s*(-?\d+\.?\d*)[°\s]*\)/i) ||
                str.match(/\(\s*(-?\d+\.?\d*)[°\s]*,\s*(-?\d+\.?\d*)[°\s]*\)/);
  if (match) {
    const lat = parseFloat(match[1]);
    const lon = parseFloat(match[2]);
    if (!isNaN(lat) && !isNaN(lon)) {
      if (-1.24 <= lat && lat <= -1.20 && 36.86 <= lon && lon <= 36.91) {
        return "USIU-A (Qwetu), Nairobi";
      }
      if (-1.25 <= lat && lat <= -1.19 && 36.84 <= lon && lon <= 36.93) {
        return "USIU-A / Roysambu, Nairobi";
      }
      if (-1.28 <= lat && lat <= -1.25 && 36.78 <= lon && lon <= 36.83) {
        return "Westlands, Nairobi";
      }
      if (-1.40 <= lat && lat <= -1.15 && 36.65 <= lon && lon <= 37.10) {
        return "USIU-A (Qwetu), Nairobi";
      }
      if (5.48 <= lat && lat <= 5.75 && -0.35 <= lon && lon <= -0.10) {
        return "East Legon, Accra";
      }
    }
    return "USIU-A (Qwetu), Nairobi";
  }

  if (str.toLowerCase().startsWith("location (") || str.toLowerCase().includes("location")) {
    return "USIU-A (Qwetu), Nairobi";
  }

  return str;
}
