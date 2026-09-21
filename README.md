# Inferway H3 ComfyUI Node Extension

Native ComfyUI custom node extension for [Inferway](https://inferway.ai) H3 video generation (`inferway/minimax-h3-768p`), featuring text-to-video, first/last frame video generation, reference image video generation, task resumption, and remote task cancellation.

> [!NOTE]
> **Candidate Notice**: This distribution package is a publication candidate (version 0.1.0) pending final authorization. Proposed Registry package ID `inferway-comfy`, PublisherId `inferway`, DisplayName `Inferway H3`, and proposed repository `https://github.com/inferway/comfyui-inferway`. Registry and public repository publication remain pending; the node cannot yet be searched or installed from the Registry.
>
> **Production Acceptance & Verification Boundaries**: Authorized real production acceptance was completed on 2026-09-21 for `inferway/minimax-h3-768p` on Linux (Python 3.12.13, ComfyUI 0.37.0, frontend 1.53.6). Windows and macOS environments have not yet been independently verified. Real production validation demonstrated exactly one paid create across Generate, Resume, and Cancel workflows. See [Section 7](#7-production-acceptance-evidence--verification-boundaries) for complete verified evidence and boundaries.

---

## 1. Installation

### 1.1 ComfyUI-Manager (Pending Publication)

Publication to ComfyUI-Manager / ComfyUI Registry is pending final authorization. Once published, installation will be:

1. Open **ComfyUI-Manager** in your ComfyUI interface.
2. Search for **Inferway H3** (or package ID `inferway-comfy`).
3. Click **Install**.
4. Restart your ComfyUI server.

Currently, install manually from archive or source directory as described below.

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

---

## 2. Server-Side Key Configuration & Security

Inferway nodes use a strict server-side credentials boundary to protect API keys:

1. **Configure Environment Variable**: Set `INFERWAY_API_KEY` in the operating system environment where your ComfyUI server process executes:
   ```bash
   export INFERWAY_API_KEY="your_inferway_api_key_here"
   ```
2. **Restart ComfyUI**: Restart your ComfyUI server so the process picks up the environment variable.
3. **No UI or Workflow Leaks**: ComfyUI frontend nodes **never** expose API key inputs, tokens, custom backend URLs, or secret paths. Saved workflow JSON files will not contain credentials, preventing accidental leaks when sharing workflows.
4. **No Cloud Credentials Required**: No AWS credentials, SQS endpoints, or third-party storage secrets are required for clients.

---

## 3. Node Specifications

The extension registers three nodes under the **`Inferway`** category:

### 3.1 `InferwayH3Generate` (Video Generation)

Submits an asynchronous video generation task and polls locally for delivery.

- **Inputs**:
  - `model`: Model identifier, default `inferway/minimax-h3-768p`.
  - `prompt`: Text prompt for video generation.
  - `duration_seconds`: Video duration in integer seconds (5 to 10, default 5).
  - `resolution`: Resolution preset (`"default"`, `"1344x768"`, `"768x1344"`).
  - `seed`: Optional uint64 string seed (`0` to `18446744073709551615`). Leave blank for random.
  - `first_frame` (optional): Initial frame image from a `LoadImage` node.
  - `last_frame` (optional): Final frame image from a `LoadImage` node.
  - `ref_image_1`, `ref_image_2`, `ref_image_3` (optional): Up to 3 reference images.
  - `profile`: Profile name, default `"default"`.
  - `wait_timeout_seconds`: Polling wait timeout in seconds (1 to 3600, default 600).
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
  - `wait_timeout_seconds`: Polling timeout in seconds (default 600).
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

---

## 4. Background Jobs, Interruption & Cancellation

1. **Closing Browser Windows**:
   Closing browser tabs only disconnects the frontend WebSocket client. It **does not stop** execution on the ComfyUI server. The server background coroutine continues waiting, polling, and downloading generation results.
2. **Local Stop vs. Explicit Remote Cancellation**:
   - Clicking **Stop** in the ComfyUI interface or encountering a local `wait_timeout_seconds` timeout only interrupts the local client polling coroutine. It **does not send a cancel request** to the remote Inferway service. Remote generation continues and may incur charges if completed.
   - To stop a remote task, execute `InferwayH3Cancel` with the target `interaction_id` (issuing `POST /v1/interactions` with an `op=cancel` body) and inspect the returned `outcome` and `charged` status.

---

### Finding an Interaction ID After a Lost Response

Use the Inferway console task history, or run the history CLI with the same server-side key and ComfyUI Python environment:

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
- **Private Cache Leases**: Media cache files are stored in private temporary directories and held by `VIDEO` object references. Prompt cleanup clears execution registry records without prematurely removing video cache files referenced by downstream nodes.

---

## 6. Workflow Examples

### 6.1 Importable Canvas Workflows

Use **Ctrl+O** in ComfyUI to import one of the [canvas workflows](workflows/ui/README.md):

- [Text to video](workflows/ui/t2v.json): Generate, save an MP4, and display the interaction ID.
- [Resume](workflows/ui/resume.json): Enter an existing interaction ID to retrieve its video without creating another task.
- [Cancel](workflows/ui/cancel.json): Enter the target interaction ID and inspect the visible outcome and `charged` value.

Generate/Resume/Cancel node paths were exercised against production, while the importable canvas JSON files were separately validated in native ComfyUI (0.37.0, frontend 1.53.6) against the loopback API.

### 6.2 API-format Workflows

Sample API-format workflow definitions are included in the [`workflows/`](workflows/) directory:

1. [`workflows/t2v.json`](workflows/t2v.json): Text-to-video workflow (`InferwayH3Generate` -> `SaveVideo`).
2. [`workflows/first-last-frame.json`](workflows/first-last-frame.json): First and last frame video workflow.
3. [`workflows/reference-images.json`](workflows/reference-images.json): Multi-image reference video workflow.
4. [`workflows/resume.json`](workflows/resume.json): Resumption workflow (`InferwayH3Resume` -> `SaveVideo`).
5. [`workflows/cancel.json`](workflows/cancel.json): Explicit remote cancellation workflow (`InferwayH3Cancel`).

### Submitting API Format Workflows

These workflow files are raw **ComfyUI API format graphs**. To submit via the ComfyUI HTTP API, wrap the JSON in a `{"prompt": ...}` payload:

```bash
curl -X POST http://127.0.0.1:8188/prompt \
  -H "Content-Type: application/json" \
  -d "{\"prompt\": $(cat workflows/t2v.json)}"
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
- **Capability Boundary**: Production capability was observed as text-to-video only (`t2v-only`). Image sockets exist on the node, but image modes are allowed only when the live model catalog advertises them.
- **Audio Scope**: The observed AAC result proves the tested path and media pipeline preservation, not that every future response from the service is guaranteed to contain AAC audio.
- **Platform Scope**: Linux is verified; Windows and macOS environments have not been independently verified.
- **Publication Status**: Registry and public repository publication remain pending; users cannot yet search or install the node from the ComfyUI Registry.
- **Confidentiality**: In accordance with security policy, no API keys, secret paths, full interaction IDs, prompt text, internal host paths, prompt IDs, or signed download URLs are published.

---

## 8. License

This client-only extension package is proposed under the [MIT License](LICENSE) for publication candidate review.
