# Inferway H3 ComfyUI Node Extension

Native ComfyUI custom node extension for [Inferway](https://inferway.ai) H3 video generation (`inferway/minimax-h3-768p`), featuring text-to-video, first/last frame video generation, reference image video generation, task resumption, and remote task cancellation.

> [!NOTE]
> **Package**: Published to the ComfyUI Registry as package ID `inferway-comfy` (PublisherId `inferway`, DisplayName `Inferway H3`). Source repository: https://github.com/inferway/comfyui-inferway
>
> **Production Acceptance & Verification Boundaries**: Authorized real production acceptance was completed on 2026-09-21 for `inferway/minimax-h3-768p` on Linux (Python 3.12.13, ComfyUI 0.37.0, frontend 1.53.6). macOS has not yet been independently verified. On Windows, 0.1.2 and earlier could not download a finished video (see Section 7.2); 0.1.3 fixes this, and its client package suites run on Windows in CI. 0.1.4 ran end to end on a real Windows 11 host (ComfyUI 0.37.0): a key saved from the in-app dialog, Prompt Expand into Generate, then History into Resume. Real production validation demonstrated exactly one paid create across Generate, Resume, and Cancel workflows. See [Section 7](#7-production-acceptance-evidence--verification-boundaries) for complete verified evidence and boundaries.

Documentation: [ComfyUI installation and setup](https://inferway.ai/docs/comfyui) · [H3 model, capabilities and pricing](https://inferway.ai/model/minimax-h3-768p) · [Video API guide](https://inferway.ai/docs/video).

中文文档：[ComfyUI 安装与接入](https://inferway.ai/zh/docs/comfyui) · [H3 模型、能力与价格](https://inferway.ai/zh/model/minimax-h3-768p) · [视频 API 指南](https://inferway.ai/zh/docs/video)。

---

## 1. Installation

Requirements: ComfyUI 0.35.0 or later, running on Python 3.10 or later.

### 1.1 ComfyUI-Manager (Recommended)

Install directly from the ComfyUI Registry:

1. Open **ComfyUI-Manager** in your ComfyUI interface.
2. Search for **Inferway H3** (or package ID `inferway-comfy`).
3. Click **Install**.
4. Restart your ComfyUI server.


### 1.2 Manual Installation (Same-Python pip fallback)

If installing manually from archive or source directory, clone or copy the package directory into your ComfyUI `custom_nodes/` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
# Place package directory as custom_nodes/inferway-comfy
```

Then install the package dependencies using the **same Python environment** that runs ComfyUI:

```bash
# Using the ComfyUI virtual environment Python
/path/to/ComfyUI/.venv/bin/python -m pip install -r /path/to/ComfyUI/custom_nodes/inferway-comfy/requirements.txt
```

Alternatively, install the package in editable mode:

```bash
/path/to/ComfyUI/.venv/bin/python -m pip install -e /path/to/ComfyUI/custom_nodes/inferway-comfy
```

### 1.3 Updates

From 0.1.6 the node pack tells you when a newer version is published. At most once a day the browser reads the version line of [`pyproject.toml`](https://raw.githubusercontent.com/inferway/comfyui-inferway/main/pyproject.toml) on this repository's `main` branch, which is what ComfyUI-Manager installs, and shows a notice once per new version. The request sends no cookie, referrer or API key, and a failure is silent. Turn it off under **Settings → Inferway → Updates**.

To update, use **Update** on Inferway H3 in ComfyUI-Manager, or run `git pull` in the node folder if you cloned it, then restart ComfyUI.

Requests to the Inferway API carry `User-Agent: inferway-comfy/<version>`, so Inferway can tell which plugin versions are still in use.

---

## 2. Server-Side Key Configuration & Security

**Get an API key first**: sign in at [inferway.ai/console/keys](https://inferway.ai/console/keys) and create one. The [API documentation](https://inferway.ai/docs) describes the video generation API these nodes call.

Inferway nodes use a strict server-side credentials boundary to protect API keys:

1. **Configure Environment Variable**: Set `INFERWAY_API_KEY` in the operating system environment where your ComfyUI server process executes:
   ```bash
   export INFERWAY_API_KEY="your_inferway_api_key_here"
   ```
2. **Restart ComfyUI**: Restart your ComfyUI server so the process picks up the environment variable.
3. **No UI or Workflow Leaks**: ComfyUI frontend nodes **never** expose API key inputs, tokens, custom backend URLs, or secret paths — only a `profile` **name** dropdown whose options are read server-side from the local store. Saved workflow JSON files will not contain credentials, preventing accidental leaks when sharing workflows.
4. **No Cloud Credentials Required**: No AWS credentials, SQS endpoints, or third-party storage secrets are required for clients.
5. **Machine-Local Profile Store (Security Note)**: a key saved from the in-app dialog (**Inferway → Manage Inferway API key**) is written to `user/__inferway/credentials.json` inside ComfyUI's system-user directory, which no HTTP route serves. The file is created owner-only (`0600`/`0700` on POSIX; `icacls`-restricted to the current user on Windows; 0.1.4 left the Windows file with its inherited permissions, so save the key once more after upgrading), never stored in `comfy.settings.json`, workflow files or PNG metadata, and never logged. Profile edits are refused for any non-loopback client unless `INFERWAY_ALLOW_REMOTE_KEY_EDIT=1` is set deliberately.

   > **Caveat**: the key store belongs to the whole ComfyUI installation, not to a ComfyUI login. Under ComfyUI's `--multi-user` mode the profiles are **not** isolated between users — everyone who can use that ComfyUI instance can select (and, on that machine, read) the same stored keys. Do not share a `--multi-user` instance between mutually untrusting parties.

---

## 3. Node Specifications

The extension registers six nodes under the **`Inferway`** category:

### 3.1 `InferwayH3GenerateV2` / `InferwayH3Generate` (Video Generation)

`InferwayH3GenerateV2` (shown as **Inferway H3 Generate**) is the current node. It is
exactly the legacy node in behaviour, with one change: `seed` is now a normal ComfyUI
integer widget with the usual *control after generate* selector, defaulted to
`randomize`.

| | `InferwayH3GenerateV2` | `InferwayH3Generate` (legacy) |
|---|---|---|
| `seed` widget | integer, `0` to `18446744073709551615`, with *control after generate* | text field, decimal digits or blank |
| Blank `seed` | not possible — a value is always sent | blank means "no seed pinned" |
| Default control mode | `randomize`: every run is a new paid order, so each one gets a different video | n/a |
| Node search | listed normally | marked deprecated, shown as **Inferway H3 Generate (legacy)** |

**Every run is a new order.** Each time you press Run, the Generate node places a
new order and that order is billed when its video finishes. The seed does not
change this: a `fixed` seed does not reuse an earlier order, it only asks for a
similar video again. After a wait timeout, do not run Generate again; put the
task id into an `InferwayH3Resume` node instead. Every other input, output,
socket position and saved workflow from the legacy node keeps working
unchanged; new examples ship with V2.

Submits an asynchronous video generation task and polls locally for delivery.

- **Inputs**:
  - `model`: Model identifier, default `inferway/minimax-h3-768p`.
  - `prompt`: Text prompt for video generation.
  - `duration_seconds`: Video duration in integer seconds (5 to 10, default 5).
  - `resolution`: Resolution preset (`"default"`, `"1344x768"`, `"768x1344"`).
  - `seed`: V2 takes an integer from `0` to `18446744073709551615` (default `0`,
    control after generate `randomize`). The legacy node takes an optional decimal
    uint64 string; leaving it blank means no seed is pinned.
  - `first_frame` (optional): Initial frame image from a `LoadImage` node.
  - `last_frame` (optional): Final frame image from a `LoadImage` node.
  - `ref_image_1`, `ref_image_2`, `ref_image_3` (optional): Up to 3 reference images.
  - `profile`: Profile name, default `"default"`.
  - `wait_timeout_seconds`: How long this node keeps waiting locally. `0` (the
    default) waits on the queue automatically, capped at 7200 seconds; `60` to
    `7200` sets an explicit cap. Reaching the cap never cancels the task.
- **Outputs**:
  - `video`: Native ComfyUI `VIDEO` object, directly connectable to `SaveVideo`.
  - `interaction_id`: Unique task identifier (`int_<32 hex chars>`).
  - `status`: Completion status string (`"succeeded"`).
- **Mode Support & Capability Boundaries**:
  - Image input modes (first/last frame and reference images) depend strictly on the remote API's advertised modes from model discovery. Image sockets exist on the node, but image modes are allowed only when the live model catalog advertises them.
  - Unsupported or unadvertised modes fail validation locally before task creation (zero creates on unadvertised mode combinations).
  - Current production observation was text-to-video only (`t2v-only`), observed as of 2026-09-21. This is a dated point-in-time observation, not a timeless guarantee; the remote service may advertise and enable additional image modes in the future.

### 3.2 `InferwayH3Resume` (Task Resumption)

Resumes polling and downloading for an already existing task after network interruption, restart, or local timeout.

- **Inputs**:
  - `interaction_id`: Target task ID (`int_<32 hex chars>`).
  - `profile`: Default `"default"`.
  - `wait_timeout_seconds`: How long to keep waiting locally: `0` (default) waits
    automatically up to 7200 seconds, `60` to `7200` sets an explicit cap. The
    task itself is never cancelled when the cap is reached.
- **Outputs**:
  - `video`: Native `VIDEO` object.
  - `interaction_id`: Verified task ID.
  - `status`: Final status string.
- **Contract**: **This node never creates a new task** (zero creates).

### 3.3 `InferwayH3Cancel` (Remote Cancellation)

Explicitly requests remote cancellation of a queued or processing generation task on Inferway servers by issuing `POST /v1/interactions` with an `op=cancel` body (`{"op": "cancel", "interaction_id": ...}`).

- **Inputs**:
  - `interaction_id`: Target task ID to cancel.
  - `profile`: Default `"default"`.
- **Outputs**:
  - `interaction_id`: Target task ID.
  - `status`: Detailed status string containing remote outcome and billing status.
- **Outcomes & Billing**:
  - Possible `outcome` values: `pending`, `cancelled`, `delivering`, `delivered`, `refused`.
  - The status includes the server's boolean `charged` field (e.g. `delivering` with `charged=False`, `delivered` with `charged=True`). Do not assume HTTP 200 indicates free cancellation.

### 3.4 `InferwayH3History` (Recent Interaction History)

Lists this account's most recent video tasks inside ComfyUI, so a workflow that hit its local timeout (or a ComfyUI restart) never forces a trip to the console to copy an interaction ID.

- **Inputs**:
  - `profile`: Profile name, default `"default"`.
  - `limit`: How many recent interactions to list (1 to 50, default 10).
  - `state`: Filter (`all`, `succeeded`, `failed`, `queued`, `running`, `cancelled`), default `all`. `all` sends no state filter.
- **Outputs**:
  - `history`: One line per interaction — `created_at` (UTC, truncated to the minute), `state`, `request.mode`, `request.duration_seconds`, `billing.charged_amount` plus `currency`, availability (`downloadable` or `expired`), `id`, and `request.prompt_excerpt`. With no records the single line reads `No interactions found.`
  - `latest_succeeded_id`: The newest `succeeded` interaction whose result is still downloadable, empty when there is none. It is a `STRING` compatible with the Resume `interaction_id` input, so the two nodes can be wired directly. In the shipped `history.json`, Resume fails the queue with `invalid_interaction_id` when no downloadable success is listed, and otherwise downloads that newest video again on every queue (no charge, only transfer time); unwire Resume when you only want the list.
- **Contract**: **read-only**. The node sends `POST /v1/interactions` with `op=list` only — never `get`, `create` or `cancel` — and `op=list` never issues a signed download link. It refetches on every queue, because history is live data.

### 3.5 `InferwayPromptExpand` (MiMo Prompt Expansion)

Expands a short idea into a complete video prompt with Inferway's own `inferway/mimo-v2.6-flash` chat model (`POST /v1/chat/completions`, non-streaming), ready to feed into the Generate `prompt` input.

- **Inputs**:
  - `profile`: Profile name, default `"default"`. **This profile's balance pays for the MiMo tokens.**
  - `idea`: The short idea to expand (multi-line, required). A blank idea fails locally before any request is sent.
  - `language`: Prompt language — `auto`, `zh` or `en`, default `auto` (`auto` answers in the language of the idea).
- **Outputs**:
  - `prompt`: One paragraph of prompt text without preamble, explanation or Markdown.
- **Billing**: the expansion call is **billed per token** on `inferway/mimo-v2.6-flash`, separately from any video generation charge.
  - Re-queuing with identical inputs usually reuses ComfyUI's cached text, so the same expansion is not paid for twice. This is best effort: a ComfyUI restart, `--cache-none`, or the cache being evicted under memory pressure runs the expansion again and bills it again (and, at temperature 0.7, may return different text).
  - A failed expansion is not cached. In particular, a 30-second timeout can arrive after the service has already finished and billed the tokens, so re-queuing after a timeout may bill a second expansion.
  - If the reply hits the token limit, the node keeps the text but shows `提示词被截断…` / `Prompt truncated…` on the node. Read the prompt before it reaches a paid Generate node, or shorten the idea and expand again.

---

## 4. Background Jobs, Interruption & Cancellation

1. **Progress While You Wait**:
   Generate and Resume report every status poll back to ComfyUI. While an order is
   queued the node shows `排队中：前面还有 N 单，预计 M 分钟后开始` / `Queued: N ahead,
   starts in about M min`; while it renders it shows `生成中：已 X 秒（通常 Y 秒）` /
   `Generating: Xs (usually Ys)` together with a progress bar scaled to the
   service's typical duration for that tier. Nothing is drawn while queued, so the
   bar never pretends to know a percentage the service does not publish.
2. **Automatic Waiting**:
   `wait_timeout_seconds = 0` (the default) means "wait on the queue": the node
   keeps polling for as long as the order is `queued` or `running`, up to 7200
   seconds. A value saved by an older workflow (600 and friends) is still honoured
   as an explicit cap. If the queue estimate will outlast the cap, the node says so
   in its progress text.
3. **Neither The Cap Nor Stop Cancels Anything**:
   Hitting the local wait cap, or pressing **Stop** in ComfyUI, only ends the local
   wait. The remote order keeps running and is billed only when the video exists —
   so a red "timed out" message does **not** mean the money is gone. Do not re-submit:
   put the task id from the message into an `InferwayH3Resume` node and collect the
   clip. The message says exactly this, in Chinese and English.
4. **Closing Browser Windows**:
   Closing browser tabs only disconnects the frontend WebSocket client. It **does not stop** execution on the ComfyUI server. The server background coroutine continues waiting, polling, and downloading generation results.
2. **Local Stop vs. Explicit Remote Cancellation**:
   - Clicking **Stop** in the ComfyUI interface or encountering a local `wait_timeout_seconds` timeout only interrupts the local client polling coroutine. It **does not send a cancel request** to the remote Inferway service. Remote generation continues and may incur charges if completed.
   - To stop a remote task, execute `InferwayH3Cancel` with the target `interaction_id` (issuing `POST /v1/interactions` with an `op=cancel` body) and inspect the returned `outcome` and `charged` status.
3. **Error Messages Are Bilingual**:
   Every customer-facing failure is rendered as a Chinese sentence, an English
   sentence, and the raw code in parentheses — for example
   `余额不足，请充值后重试：https://inferway.ai/console/billing` / `Insufficient
   balance, top up and try again: ...` `(code: payment_required)`. A missing or
   rejected key points at ComfyUI's **Settings → Inferway** row and
   https://inferway.ai/console/keys; a failed order that the service reports as
   uncharged says so. The trailing code is what support needs, the sentences are
   what you need, and neither ever contains an API key.

---

### Finding an Interaction ID After a Lost Response

Use the Inferway console task history, the `InferwayH3History` node (it prints the same IDs on the canvas and can feed Resume directly), or run the history CLI with the same server-side key and ComfyUI Python environment:

```bash
cd /path/to/ComfyUI/custom_nodes/inferway-comfy
/path/to/ComfyUI/.venv/bin/python -m inferway_comfy history --limit 20
```

The CLI lists IDs, model, status, creation time and source key name. It does not print prompts or signed download links, and never automatically chooses a job. Confirm the ID, then use Resume.

---

## 5. Media, Audio & Cache Semantics

- **Video & Audio Preservation**: Video and audio streams are preserved when present in the delivered media result, and connect directly to the native `SaveVideo` node to produce MP4 files.
- **Audio Verification & Fixture Scope**: The observed AAC result in production acceptance and test fixtures proves the tested audio handling and preservation in the client and `SaveVideo` pipeline, not that every future response is guaranteed to contain AAC audio.
- **Secure Download & Integrity**: Result downloads verify byte count and SHA-256 against the result metadata returned by the authenticated API before caching.
- **Log Hygiene**: httpx logs every request URL at INFO. A signed download link's query is replaced by `?<redacted>`, so `comfyui.log` never holds a working link to the video.
- **Private Cache Leases**: Media cache files are stored in private temporary directories and held by `VIDEO` object references. Prompt cleanup clears execution registry records without prematurely removing video cache files referenced by downstream nodes.
- **Windows**: Windows has no POSIX owner or permission bits, so the owner-only checks apply on Linux and macOS; on Windows the cache directory inherits the ACL of ComfyUI's temp directory. Downloads are written in binary mode. A cache file that a preview or save node still holds open is deleted later, at the latest when ComfyUI empties its temp directory on the next start.

---

## 6. Workflow Examples

### 6.1 Template Browser and Canvas Workflows

After installing the node pack, click **Templates** in ComfyUI's left sidebar and pick **Inferway examples** under Extensions: every canvas workflow below is listed there with a preview, and one click loads it. You can also open one with **Ctrl+O** from [`example_workflows/`](example_workflows/README.md):

- [Text to video](<example_workflows/Inferway Text to Video.json>): Generate, save an MP4, and display the interaction ID.
- [First and last frame](<example_workflows/Inferway First and Last Frame.json>): Load an opening and a closing image, generate the shot between them, and save an MP4. Available only while the live model catalog advertises the mode.
- [Reference images](<example_workflows/Inferway Reference Images.json>): Load up to three reference images to keep the generated shot visually consistent with them. Available only while the live model catalog advertises the mode.
- [Resume](<example_workflows/Inferway Resume.json>): Enter an existing interaction ID to retrieve its video without creating another task.
- [Cancel](<example_workflows/Inferway Cancel.json>): Enter the target interaction ID and inspect the visible outcome and `charged` value.
- [History](<example_workflows/Inferway History.json>): List recent tasks on the canvas and hand the newest resumable interaction ID straight to Resume.
- [Prompt expand](<example_workflows/Inferway Prompt Expand.json>): Expand a short idea with MiMo and feed the result into Generate.

The image workflows open with empty `LoadImage` nodes outlined in red until you choose your own images; that is ComfyUI's normal missing-input marker.

Generate/Resume/Cancel node paths were exercised against production, while the canvas JSON files were separately validated in native ComfyUI (0.37.0, frontend 1.53.6) against the loopback API.

### 6.2 API-format Workflows

Sample API-format workflow definitions are included in the [`api_workflows/`](api_workflows/) directory. They are for the ComfyUI HTTP API, not for the canvas, so they are kept out of the template browser:

1. [`api_workflows/t2v.json`](api_workflows/t2v.json): Text-to-video workflow (`InferwayH3GenerateV2` -> `SaveVideo`).
2. [`api_workflows/first-last-frame.json`](api_workflows/first-last-frame.json): First and last frame video workflow.
3. [`api_workflows/reference-images.json`](api_workflows/reference-images.json): Multi-image reference video workflow.
4. [`api_workflows/resume.json`](api_workflows/resume.json): Resumption workflow (`InferwayH3Resume` -> `SaveVideo`).
5. [`api_workflows/cancel.json`](api_workflows/cancel.json): Explicit remote cancellation workflow (`InferwayH3Cancel`).
6. [`api_workflows/history.json`](api_workflows/history.json): Recent-history workflow (`InferwayH3History` -> `InferwayH3Resume` -> `SaveVideo`).
7. [`api_workflows/prompt-expand-t2v.json`](api_workflows/prompt-expand-t2v.json): Prompt expansion workflow (`InferwayPromptExpand` -> `InferwayH3GenerateV2` -> `SaveVideo`).

### Submitting API Format Workflows

These workflow files are raw **ComfyUI API format graphs**. To submit via the ComfyUI HTTP API, wrap the JSON in a `{"prompt": ...}` payload:

```bash
curl -X POST http://127.0.0.1:8188/prompt \
  -H "Content-Type: application/json" \
  -d "{\"prompt\": $(cat api_workflows/t2v.json)}"
```

> [!NOTE]
> - For image-input workflows (`first-last-frame.json`, `reference-images.json`), place your input images in ComfyUI's `input/` folder and match the filenames.
> - For resume and cancel workflows (`resume.json`, `cancel.json`), replace `YOUR_INTERACTION_ID` with your actual task ID.

---

## 7. Production Acceptance Evidence & Verification Boundaries

Authorized real production acceptance was completed on **2026-09-21** for the H3 integration. The verified results, parameters, and boundaries are documented below:

### 7.1 Verified Production Execution Evidence
- **Model**: `inferway/minimax-h3-768p` text-to-video.
- **Request Parameters**: 5 seconds duration at 1344x768 resolution. (Task interaction ID and prompt text are confidential and omitted.)
- **Media Delivery**: The generated MP4 decoded as H.264 video plus AAC audio and lasted about 5.167 seconds.
- **Same-ID Resume**: A subsequent same-ID `InferwayH3Resume` invocation retrieved the task result and saved another native `SaveVideo` MP4 with H.264 plus AAC, creating **zero new interactions**.
- **Same-ID Cancel**: A subsequent same-ID `InferwayH3Cancel` invocation on the already-delivered interaction surfaced `outcome=delivered, charged=True` via `POST /v1/interactions` with an `op=cancel` body, creating **zero new interactions**.
- **Accounting & Creates**: Total paid creates across Generate, Resume, and Cancel remained **exactly one**.
- **Production Billing**: The authoritative billing block recorded USD held/charged/refunded as `0.20 / 0.20 / 0.00` under the point-in-time 50% promotion. Note that pricing and promotions can change over time; the live catalog and console remain the definitive price authority.
- **Runtime-Code Acceptance Candidate**: Merge SHA `da486d216495dad3fc798a350e6e5a868aaa0f52` and 24-file package archive SHA-256 `06996aed1aa623e96f1c93321857a225e85719bcb68b81628f90a9560aadd99c` represent the exact runtime-code acceptance candidate tested before this docs-only follow-up (not the final publication archive identity, which updates when package documentation changes).
- **Verified Platform**: The verified host platform for the final continuation was Linux, Python 3.12.13, ComfyUI 0.37.0, frontend 1.53.6.

### 7.2 Verification Boundaries & Policies
- **Capability Boundary**: At the 2026-09-21 acceptance, production offered text-to-video only. Since then the live model catalog advertises `t2v`, `fl2va` and `ref2va`; on 2026-10-01 one first/last-frame and one reference-image order each ran end to end against production through the HTTP API (not through ComfyUI). Image modes are allowed only when the live model catalog advertises them.
- **Audio Scope**: The observed AAC result proves the tested path and media pipeline preservation, not that every future response from the service is guaranteed to contain AAC audio.
- **Platform Scope**: Linux is verified end to end. Up to 0.1.2, every Windows download failed after the order had been charged: the media cache called `os.getuid()`, which Windows does not have, and opened the file without `O_BINARY`, which corrupts the video. 0.1.3 fixes both, and CI now runs the client package suites, media download included, on `windows-2025`. A full ComfyUI session on Windows and anything on macOS have not been independently verified.
- **Publication Status**: Published to the ComfyUI Registry as `inferway-comfy` and to the public repository https://github.com/inferway/comfyui-inferway; the node is searchable and installable from ComfyUI-Manager.
- **Confidentiality**: In accordance with security policy, no API keys, secret paths, full interaction IDs, prompt text, internal host paths, prompt IDs, or signed download URLs are published.

---

## 8. License

This client-only extension package is released under the [MIT License](LICENSE).
