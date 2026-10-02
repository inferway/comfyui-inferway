// Tells the user, once per release, that a newer Inferway node pack exists.
//
// The Comfy Registry flags most new versions (2026-09), so Manager mostly
// installs the public repository's main branch; its pyproject.toml is
// therefore the version users actually get. The check runs in the browser at
// most once a day, sends no cookie, referrer or key, and stays silent on any
// failure. Settings → Inferway → Updates turns it off.

import { app } from "../../scripts/app.js";

// Kept equal to pyproject.toml; tests/check_ux.py pins it.
const PLUGIN_VERSION = "0.1.6";
const LATEST_URL =
  "https://raw.githubusercontent.com/inferway/comfyui-inferway/main/pyproject.toml";
const SETTING_ID = "Inferway.UpdateCheck";
const CHECKED_AT_KEY = "inferway.update.checkedAt";
const LATEST_KEY = "inferway.update.latest";
const NOTIFIED_KEY = "inferway.update.notified";
const CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000;
const FETCH_TIMEOUT_MS = 8000;
const VERSION_RE = /^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"\s*$/m;

function isChineseLocale() {
  let locale = "";
  try {
    const fromSettings = app?.extensionManager?.setting?.get("Comfy.Locale");
    if (typeof fromSettings === "string" && fromSettings) locale = fromSettings;
  } catch (err) {
    locale = "";
  }
  if (!locale && typeof navigator !== "undefined") locale = navigator.language || "";
  return String(locale).toLowerCase().startsWith("zh");
}

function strings() {
  if (isChineseLocale()) {
    return {
      settingName: "有新版本时提醒我",
      summary: (latest) => `Inferway 插件有新版本 ${latest}`,
      detail: (current) =>
        `你现在用的是 ${current}。在 ComfyUI-Manager 里更新 Inferway H3（手动克隆的，在插件目录运行 git pull），然后重启 ComfyUI。`,
    };
  }
  return {
    settingName: "Tell me when a new version is out",
    summary: (latest) => `Inferway nodes ${latest} is available`,
    detail: (current) =>
      `You have ${current}. Update Inferway H3 in ComfyUI-Manager (or run git pull in the node folder if you cloned it), then restart ComfyUI.`,
  };
}

// [major, minor, patch] from a pyproject.toml body, or null.
function parseVersion(text) {
  const match = VERSION_RE.exec(String(text));
  if (!match) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

function isNewer(latest, current) {
  for (let i = 0; i < 3; i += 1) {
    if (latest[i] !== current[i]) return latest[i] > current[i];
  }
  return false;
}

function readStored(key) {
  try {
    return localStorage.getItem(key);
  } catch (err) {
    return null;
  }
}

function writeStored(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (err) {
    // Storage blocked: the check still works, it just repeats next session.
  }
}

async function fetchLatest() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(LATEST_URL, {
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const version = parseVersion(await response.text());
    return version ? version.join(".") : null;
  } catch (err) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// The newest published version, from a cache younger than a day or the network.
async function latestVersion() {
  const checkedAt = Number(readStored(CHECKED_AT_KEY));
  const cached = readStored(LATEST_KEY);
  if (cached && Number.isFinite(checkedAt) && Date.now() - checkedAt < CHECK_INTERVAL_MS) {
    return cached;
  }
  const latest = await fetchLatest();
  if (latest) {
    writeStored(LATEST_KEY, latest);
    writeStored(CHECKED_AT_KEY, String(Date.now()));
  }
  return latest;
}

function checkEnabled() {
  try {
    return app?.extensionManager?.setting?.get(SETTING_ID) !== false;
  } catch (err) {
    return true;
  }
}

async function checkForUpdate() {
  if (!checkEnabled()) return;
  const latest = await latestVersion();
  const latestParts = latest ? parseVersion(`version = "${latest}"`) : null;
  const current = parseVersion(`version = "${PLUGIN_VERSION}"`);
  if (!latestParts || !current || !isNewer(latestParts, current)) return;
  if (readStored(NOTIFIED_KEY) === latest) return;
  const manager = app?.extensionManager?.toast;
  if (!manager || typeof manager.add !== "function") return;
  const t = strings();
  manager.add({
    severity: "info",
    summary: t.summary(latest),
    detail: t.detail(PLUGIN_VERSION),
    life: 15000,
  });
  writeStored(NOTIFIED_KEY, latest);
}

app.registerExtension({
  name: "Inferway.UpdateCheck",
  settings: [
    {
      id: SETTING_ID,
      category: ["Inferway", "Updates", "Check"],
      get name() {
        return strings().settingName;
      },
      type: "boolean",
      defaultValue: true,
    },
  ],
  async setup() {
    void checkForUpdate();
  },
});
