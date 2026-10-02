// Inferway API key management for ComfyUI.
//
// The key itself only ever exists in this file's POST body: it is never stored
// in ComfyUI settings, never written into workflow JSON or PNG metadata, and
// never logged. The backend keeps it in a private machine-local file under
// ComfyUI's system-user directory, which no HTTP route serves.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SETTINGS_ID = "Inferway.ApiKey";
const COMMAND_ID = "Inferway.ManageApiKey";
const GET_KEY_URL = "https://inferway.ai/console/keys";
const TOAST_SESSION_KEY = "inferway.apikey.toasted";
const STYLE_ID = "inferway-apikey-style";
const PROFILE_INPUT_NAME = "inferway_profile";
const KEY_INPUT_NAME = "inferway_api_key";
const PROFILE_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
// After trim(): 8..512 printable ASCII characters with no blank anywhere.
const KEY_RE = /^[\x21-\x7e]{8,512}$/;

// The dialog is built from class names only, so its stylesheet is injected
// here (the release allowlist ships web/*.js, not .css). Injected exactly once.
const STYLESHEET = `
.inferway-apikey-overlay {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
  font-family: inherit;
}
.inferway-apikey-panel {
  box-sizing: border-box;
  width: min(460px, calc(100vw - 32px));
  max-height: calc(100vh - 48px);
  overflow-y: auto;
  padding: 20px 22px;
  border: 1px solid var(--border-color, rgba(255, 255, 255, 0.12));
  border-radius: 12px;
  background: var(--comfy-menu-bg, var(--fg-color, #1f1f1f));
  color: var(--text-color, #ececec);
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.45);
}
.inferway-apikey-panel h2 {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-color, #ececec);
}
.inferway-apikey-status {
  margin: 0 0 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--comfy-input-bg, rgba(255, 255, 255, 0.06));
  font-size: 13px;
}
.inferway-apikey-status-title {
  margin-bottom: 6px;
  font-weight: 600;
  color: var(--descrip-text, #b9b9b9);
}
.inferway-apikey-status ul {
  margin: 0;
  padding-left: 18px;
}
.inferway-apikey-status li { margin: 2px 0; }
.inferway-apikey-form { display: flex; flex-direction: column; gap: 8px; }
.inferway-apikey-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: var(--descrip-text, #b9b9b9);
}
.inferway-apikey-input {
  box-sizing: border-box;
  width: 100%;
  padding: 7px 10px;
  border: 1px solid var(--border-color, rgba(255, 255, 255, 0.18));
  border-radius: 6px;
  background: var(--comfy-input-bg, #2a2a2a);
  color: var(--text-color, #ececec);
  font-size: 14px;
  font-family: inherit;
}
.inferway-apikey-input:focus {
  outline: 2px solid var(--primary-color, #5b8def);
  outline-offset: 1px;
}
.inferway-apikey-hint {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--descrip-text, #b9b9b9);
}
.inferway-apikey-message {
  min-height: 18px;
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--danger-color, #e5484d);
}
.inferway-apikey-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
}
.inferway-apikey-button,
.inferway-apikey-manage {
  padding: 7px 14px;
  border: 1px solid var(--border-color, rgba(255, 255, 255, 0.18));
  border-radius: 6px;
  background: var(--comfy-input-bg, #2f2f2f);
  color: var(--text-color, #ececec);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.inferway-apikey-button:hover:not(:disabled),
.inferway-apikey-manage:hover { filter: brightness(1.15); }
.inferway-apikey-button:disabled { opacity: 0.5; cursor: not-allowed; }
.inferway-apikey-primary {
  background: var(--primary-color, #5b8def);
  border-color: var(--primary-color, #5b8def);
  color: #fff;
}
.inferway-apikey-danger {
  background: transparent;
  border-color: var(--danger-color, #e5484d);
  color: var(--danger-color, #e5484d);
}
.inferway-apikey-link {
  margin-left: auto;
  font-size: 13px;
  color: var(--primary-color, #5b8def);
}
`;

function ensureStyles() {
  if (typeof document === "undefined") return;
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = STYLESHEET;
  document.head.appendChild(style);
}

function isChineseLocale() {
  let locale = "";
  try {
    const fromSettings = app?.extensionManager?.setting?.get("Comfy.Locale");
    if (typeof fromSettings === "string" && fromSettings) {
      locale = fromSettings;
    }
  } catch (err) {
    locale = "";
  }
  if (!locale && typeof navigator !== "undefined") {
    locale = navigator.language || "";
  }
  return String(locale).toLowerCase().startsWith("zh");
}

function strings() {
  if (isChineseLocale()) {
    return {
      manage: "管理 Inferway API key",
      settingsName: "Inferway API key",
      title: "Inferway API key",
      profileLabel: "Profile 名称",
      profilePlaceholder: "default",
      profileHint: "可以保存多个 profile，节点的 profile 下拉里选哪个就用哪个的 key。",
      keyLabel: "API key",
      keyPlaceholder: "粘贴你的 Inferway API key",
      privacy: "这个 key 只保存在本机，不会写进工作流",
      save: "保存",
      del: "删除此 profile",
      cancel: "取消",
      getkey: "获取 key",
      configured: "已配置",
      notConfigured: "未配置",
      sourceEnv: "环境变量",
      sourceFile: "本机文件",
      last4Label: "末 4 位",
      storedTitle: "本机已有的 profile",
      noneYet: "还没有保存过任何 profile",
      envHint: "环境变量 INFERWAY_API_KEY 里的 key 优先用于 default。",
      corsHint:
        "ComfyUI 是用 --enable-cors-header 启动的，浏览器里不能填 key。请改用环境变量 INFERWAY_API_KEY。",
      hostHint:
        "请用 http://127.0.0.1:<端口> 或 http://localhost:<端口> 打开 ComfyUI 再设置（<端口> 换成你的 ComfyUI 端口）。",
      remoteHint:
        "请在运行 ComfyUI 的这台电脑上打开页面再设置 key；局域网里的其他电脑默认不能改 key。",
      unavailableHint: "暂时读不到本机的 key 状态，请稍后再试。",
      emptyTitle: "还没有可用的 API key",
      emptyDetail: "在设置 → Inferway 里填一个就能用",
      saved: "已保存 profile",
      deleted: "已删除 profile",
      deleteFailed: "删除失败",
      invalidProfile: "Profile 只能用小写字母、数字、- 和 _，最多 32 个字符。",
      invalidKey: "Key 需要是 8–512 个可打印的 ASCII 字符。",
      aclNote: "没能把这个文件的权限收紧到当前用户，key 本身已经保存成功。",
      errorPrefix: "操作失败",
    };
  }
  return {
    manage: "Manage Inferway API key",
    settingsName: "Inferway API key",
    title: "Inferway API key",
    profileLabel: "Profile name",
    profilePlaceholder: "default",
    profileHint:
      "Save as many profiles as you like; the node dropdown picks which key to use.",
    keyLabel: "API key",
    keyPlaceholder: "Paste your Inferway API key",
    privacy: "This key is stored only on this machine and never enters a workflow",
    save: "Save",
    del: "Delete this profile",
    cancel: "Cancel",
    getkey: "Get a key",
    configured: "Configured",
    notConfigured: "Not configured",
    sourceEnv: "Environment variable",
    sourceFile: "Local file",
    last4Label: "Last 4",
    storedTitle: "Profiles stored on this machine",
    noneYet: "No profile has been saved yet",
    envHint: "The INFERWAY_API_KEY environment variable takes priority for default.",
    corsHint:
      "ComfyUI was started with --enable-cors-header, so keys cannot be entered from the browser. Use the INFERWAY_API_KEY environment variable instead.",
    hostHint:
      "Open ComfyUI at http://127.0.0.1:<port> or http://localhost:<port> (replace <port> with your ComfyUI port) and set the key there.",
    remoteHint:
      "Open this page on the computer running ComfyUI to manage keys; other machines on the LAN cannot change keys by default.",
    unavailableHint: "Could not read the local key status just now. Try again later.",
    emptyTitle: "No usable API key yet",
    emptyDetail: "Add one in Settings → Inferway to get started",
    saved: "Saved profile",
    deleted: "Deleted profile",
    deleteFailed: "Delete failed",
    invalidProfile:
      "A profile name may use lowercase letters, digits, - and _, up to 32 characters.",
    invalidKey: "A key must be 8–512 printable ASCII characters.",
    aclNote:
      "The file permissions could not be tightened to your account; the key itself was saved.",
    errorPrefix: "Request failed",
  };
}

function toast(severity, summary, detail) {
  const manager = app?.extensionManager?.toast;
  if (!manager || typeof manager.add !== "function") return;
  manager.add({ severity, summary, detail, life: 6000 });
}

async function readStatus() {
  let res;
  try {
    res = await api.fetchApi("/inferway/credentials");
  } catch (err) {
    // Network failure: same surface as an unreachable local store.
    return { blocked: "unavailable" };
  }
  if (res.status === 403) {
    let body = null;
    try {
      body = await res.json();
    } catch (err) {
      body = null;
    }
    const code = typeof body?.error === "string" ? body.error : "unavailable";
    // The guard's reason code travels all the way to the dialog.
    return { blocked: code };
  }
  if (!res.ok) return { blocked: "unavailable" };
  try {
    const data = await res.json();
    return { data };
  } catch (err) {
    return { blocked: "unavailable" };
  }
}

async function postJson(path, payload) {
  try {
    const res = await api.fetchApi(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    let data = null;
    try {
      data = await res.json();
    } catch (err) {
      data = null;
    }
    return { status: res.status, data };
  } catch (err) {
    return { status: 0, data: null };
  }
}

async function refreshNodeComboOptions() {
  // New profiles must show up in every node's dropdown without a page reload.
  try {
    if (app && typeof app.refreshComboInNodes === "function") {
      await app.refreshComboInNodes();
    }
  } catch (err) {
    console.warn("Inferway: could not refresh profile dropdowns", err);
  }
}

function hasUsableKey(status) {
  if (!status?.data) return false;
  if (status.data.env_key_present) return true;
  return Array.isArray(status.data.profiles)
    ? status.data.profiles.some((p) => p?.configured)
    : false;
}

function describeFailure(status, data, t) {
  const code = data?.error;
  if (status === 403 && code === "cors_open") return t.corsHint;
  if (status === 403 && code === "host_not_allowed") return t.hostHint;
  if (status === 403 && code === "cross_site") return t.hostHint;
  if (status === 403 && code === "remote_not_allowed") return t.remoteHint;
  if (code === "store_unavailable" || code === "store_version") {
    return `${t.errorPrefix}: ${code}`;
  }
  return `${t.errorPrefix}: ${code || status}`;
}

function blockedHint(blocked, t) {
  if (blocked === "cors_open") return t.corsHint;
  if (blocked === "host_not_allowed" || blocked === "cross_site") {
    return t.hostHint;
  }
  if (blocked === "remote_not_allowed") return t.remoteHint;
  return t.unavailableHint;
}

let dialogRoot = null;
let onEscape = null;

function closeDialog() {
  if (!dialogRoot) return;
  dialogRoot.remove();
  dialogRoot = null;
  if (onEscape) {
    document.removeEventListener("keydown", onEscape);
    onEscape = null;
  }
}

function buildStatusList(status, t) {
  const wrap = document.createElement("div");
  wrap.className = "inferway-apikey-status";
  const title = document.createElement("div");
  title.className = "inferway-apikey-status-title";
  title.textContent = t.storedTitle;
  wrap.appendChild(title);

  if (status.blocked === "cors_open") {
    const hint = document.createElement("p");
    hint.className = "inferway-apikey-hint";
    hint.textContent = t.corsHint;
    wrap.appendChild(hint);
    return wrap;
  }
  if (status.blocked) {
    const hint = document.createElement("p");
    hint.className = "inferway-apikey-hint";
    hint.textContent = blockedHint(status.blocked, t);
    wrap.appendChild(hint);
    return wrap;
  }

  const profiles = status.data?.profiles || [];
  const table = document.createElement("ul");
  for (const entry of profiles) {
    const item = document.createElement("li");
    const source =
      entry.source === "env"
        ? t.sourceEnv
        : entry.source === "file"
          ? t.sourceFile
          : "";
    item.textContent = `${entry.name} · ${
      entry.configured ? t.configured : t.notConfigured
    }${entry.last4 ? ` · ${t.last4Label} ${entry.last4}` : ""}${
      source ? ` · ${source}` : ""
    }`;
    table.appendChild(item);
  }
  wrap.appendChild(table);

  if (!profiles.some((entry) => entry?.configured)) {
    const hint = document.createElement("p");
    hint.className = "inferway-apikey-hint";
    hint.textContent = t.noneYet;
    wrap.appendChild(hint);
  }

  if (status.data?.env_key_present) {
    const hint = document.createElement("p");
    hint.className = "inferway-apikey-hint";
    hint.textContent = t.envHint;
    wrap.appendChild(hint);
  }
  if (status.data?.cors_open) {
    const hint = document.createElement("p");
    hint.className = "inferway-apikey-hint";
    hint.textContent = t.corsHint;
    wrap.appendChild(hint);
  }
  return wrap;
}

function formRow(labelText, input) {
  const row = document.createElement("label");
  row.className = "inferway-apikey-row";
  const label = document.createElement("span");
  label.textContent = labelText;
  row.appendChild(label);
  row.appendChild(input);
  return row;
}

async function openDialog() {
  const t = strings();
  closeDialog();
  ensureStyles();

  let status = { blocked: "pending" };
  try {
    status = await readStatus();
  } catch (err) {
    status = { blocked: "unavailable" };
  }

  const overlay = document.createElement("div");
  overlay.className = "inferway-apikey-overlay";

  const panel = document.createElement("div");
  panel.className = "inferway-apikey-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", t.title);

  const heading = document.createElement("h2");
  heading.textContent = t.title;
  panel.appendChild(heading);

  const statusList = buildStatusList(status, t);
  panel.appendChild(statusList);

  const profileInput = document.createElement("input");
  profileInput.type = "text";
  profileInput.className = "inferway-apikey-input";
  profileInput.name = PROFILE_INPUT_NAME;
  profileInput.placeholder = t.profilePlaceholder;
  profileInput.value = "default";
  profileInput.autocomplete = "off";
  profileInput.spellcheck = false;
  profileInput.setAttribute("data-lpignore", "true");
  profileInput.setAttribute("data-1p-ignore", "true");

  const keyInput = document.createElement("input");
  keyInput.type = "password";
  keyInput.className = "inferway-apikey-input";
  keyInput.name = KEY_INPUT_NAME;
  keyInput.placeholder = t.keyPlaceholder;
  // Never offer to save this into a browser password vault.
  keyInput.autocomplete = "new-password";
  keyInput.spellcheck = false;
  keyInput.setAttribute("data-lpignore", "true");
  keyInput.setAttribute("data-1p-ignore", "true");

  const form = document.createElement("div");
  form.className = "inferway-apikey-form";
  form.appendChild(formRow(t.profileLabel, profileInput));
  const profileHint = document.createElement("p");
  profileHint.className = "inferway-apikey-hint";
  profileHint.textContent = t.profileHint;
  form.appendChild(profileHint);
  form.appendChild(formRow(t.keyLabel, keyInput));

  const privacy = document.createElement("p");
  privacy.className = "inferway-apikey-hint inferway-apikey-privacy";
  privacy.textContent = t.privacy;
  form.appendChild(privacy);

  const message = document.createElement("p");
  message.className = "inferway-apikey-message";
  form.appendChild(message);

  const blocked = Boolean(status.blocked);
  if (blocked) {
    profileInput.disabled = true;
    keyInput.disabled = true;
    message.textContent = blockedHint(status.blocked, t);
  }
  panel.appendChild(form);

  const refreshStatus = async () => {
    try {
      const next = await readStatus();
      const replacement = buildStatusList(next, t);
      statusList.replaceWith(replacement);
    } catch (err) {
      /* keep the previous list rather than breaking the dialog */
    }
  };

  const actions = document.createElement("div");
  actions.className = "inferway-apikey-actions";

  const saveButton = document.createElement("button");
  saveButton.type = "button";
  saveButton.className = "inferway-apikey-button inferway-apikey-primary";
  saveButton.textContent = t.save;
  saveButton.disabled = blocked;
  saveButton.addEventListener("click", async () => {
    const profile = profileInput.value.trim();
    const key = keyInput.value.trim();
    if (!PROFILE_RE.test(profile)) {
      message.textContent = t.invalidProfile;
      return;
    }
    if (!KEY_RE.test(key)) {
      message.textContent = t.invalidKey;
      return;
    }
    message.textContent = "";
    let result = { status: 0, data: null };
    try {
      result = await postJson("/inferway/credentials", {
        profile,
        api_key: key,
      });
    } finally {
      // The key leaves this closure either way.
      keyInput.value = "";
    }
    if (result.status !== 200 || !result.data?.ok) {
      message.textContent = describeFailure(result.status, result.data, t);
      return;
    }
    toast(
      "success",
      `${t.saved}「${result.data.profile}」`,
      result.data.acl_restricted === false ? t.aclNote : t.privacy,
    );
    profileInput.value = "default";
    await refreshStatus();
    await refreshNodeComboOptions();
  });

  const deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "inferway-apikey-button inferway-apikey-danger";
  deleteButton.textContent = t.del;
  deleteButton.disabled = blocked;
  deleteButton.addEventListener("click", async () => {
    const profile = profileInput.value.trim();
    if (!PROFILE_RE.test(profile)) {
      message.textContent = t.invalidProfile;
      return;
    }
    message.textContent = "";
    let result = { status: 0, data: null };
    try {
      result = await postJson("/inferway/credentials/delete", { profile });
    } finally {
      keyInput.value = "";
    }
    if (result.status !== 200) {
      message.textContent = describeFailure(result.status, result.data, t);
      return;
    }
    if (!result.data?.ok) {
      message.textContent = t.deleteFailed;
      return;
    }
    toast("info", `${t.deleted}「${profile}」`, "");
    await refreshStatus();
    await refreshNodeComboOptions();
  });

  const cancelButton = document.createElement("button");
  cancelButton.type = "button";
  cancelButton.className = "inferway-apikey-button";
  cancelButton.textContent = t.cancel;
  cancelButton.addEventListener("click", closeDialog);

  const link = document.createElement("a");
  link.className = "inferway-apikey-link";
  link.href = GET_KEY_URL;
  link.target = "_blank";
  link.rel = "noreferrer noopener";
  link.textContent = t.getkey;

  actions.appendChild(saveButton);
  actions.appendChild(deleteButton);
  actions.appendChild(cancelButton);
  actions.appendChild(link);
  panel.appendChild(actions);

  overlay.appendChild(panel);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeDialog();
  });

  onEscape = (event) => {
    if (event.key === "Escape") closeDialog();
  };
  document.addEventListener("keydown", onEscape);

  dialogRoot = overlay;
  document.body.appendChild(overlay);
  if (!blocked) profileInput.focus();
}

app.registerExtension({
  name: "Inferway.ApiKey",
  commands: [
    {
      id: COMMAND_ID,
      label: () => strings().manage,
      function: () => {
        void openDialog();
      },
    },
  ],
  menuCommands: [
    {
      path: ["Inferway"],
      commands: [COMMAND_ID],
    },
  ],
  settings: [
    {
      id: SETTINGS_ID,
      category: ["Inferway", "API key", "Manage"],
      // Read at render time so the label follows Comfy.Locale. It reads as a
      // button, not as a text field, so nobody pastes a key into settings
      // (comfy.settings.json is reachable through /userdata).
      get name() {
        return strings().manage;
      },
      // Custom render: a button that opens our own dialog. setValue is never
      // called, so nothing about the key is ever written into settings.
      type: (name, setValue, value, attrs) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "inferway-apikey-manage";
        button.textContent = strings().manage;
        button.addEventListener("click", () => {
          void openDialog();
        });
        return button;
      },
      defaultValue: "",
    },
  ],
  async setup() {
    const t = strings();
    ensureStyles();
    let status = null;
    try {
      status = await readStatus();
    } catch (err) {
      return;
    }
    let alreadyToasted = false;
    try {
      alreadyToasted = sessionStorage.getItem(TOAST_SESSION_KEY) === "1";
      sessionStorage.setItem(TOAST_SESSION_KEY, "1");
    } catch (err) {
      alreadyToasted = false;
    }
    if (alreadyToasted) return;

    if (status?.blocked === "cors_open") {
      toast("warn", t.title, t.corsHint);
      return;
    }
    if (status?.blocked === "host_not_allowed" || status?.blocked === "cross_site") {
      toast("warn", t.title, t.hostHint);
      return;
    }
    if (status?.blocked === "remote_not_allowed") {
      toast("warn", t.title, t.remoteHint);
      return;
    }
    if (status?.blocked) return;
    if (!hasUsableKey(status)) {
      toast("info", t.emptyTitle, t.emptyDetail);
    }
  },
});
