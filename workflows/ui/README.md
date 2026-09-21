# Inferway ComfyUI 画布工作流 (UI Canvas Workflows)

本目录提供可直接导入 ComfyUI 前端画布的原生工作流文件（LiteGraph UI 格式），支持在 ComfyUI Web 界面中可视化查看、调整节点参数并执行任务。

---

## 1. 包含的工作流文件

| 文件名 | 核心节点链路 | 功能说明 |
| --- | --- | --- |
| `t2v.json` | `InferwayH3Generate` &rarr; `SaveVideo`<br>`InferwayH3Generate` &rarr; `PreviewAny` | 文生视频画布工作流。包含提示词、时长、分辨率及十进制 Seed 参数控件，直连原生 `SaveVideo` 保存视频，并通过原生 `PreviewAny` 节点在画布上直接展示并方便复制任务 `interaction_id`。 |
| `resume.json` | `InferwayH3Resume` &rarr; `SaveVideo` | 任务恢复与下载画布工作流。用于通过任务 ID 恢复等待或重新拉取已完成视频，直连 `SaveVideo`，不发起新任务。 |
| `cancel.json` | `InferwayH3Cancel` &rarr; `PreviewAny` | 远程任务取消工作流。向服务端发起明确取消请求，并通过原生 `PreviewAny` 节点在画布上实时呈现权威取消处置结果与计费结算状态（`status` / charged）。 |

> **UI 画布格式说明**：本目录下的工作流采用 ComfyUI 前端画布导出格式（包含 `nodes` 列表、`links` 连线、画布坐标 `pos` 与尺寸 `size`、`widgets_values` 控件值），支持直接拖拽导入；不同于根目录 `workflows/*.json` 中的底层 API Prompt 图字典结构。

---

## 2. 导入与运行

### 2.1 导入工作流
1. 打开 ComfyUI 浏览器界面（如 `http://127.0.0.1:8188`）。
2. 将本目录下的任意 `.json` 文件（例如 `t2v.json`）直接拖拽释放到 ComfyUI 前端画布空白区域，或点击右侧控制面板中的 **Load** 按钮选择文件导入。
3. 画布将自动排布节点及其输入输出连线。

### 2.2 配置与重启
1. **安装节点扩展**：确保本扩展目录（`integrations/comfyui`）已正确放置在 ComfyUI 的 `custom_nodes/` 目录下，并且在 ComfyUI 运行环境中完成了依赖安装。
2. **凭据安全配置**：在运行 ComfyUI 服务端的主机环境变量中配置 API Key：
   ```bash
   export INFERWAY_API_KEY="your_inferway_api_key_here"
   ```
3. **重启服务端**：启动或重启 ComfyUI 服务，使系统发现并加载 `Inferway` 类别下的全部节点。
4. **安全红线**：画布界面与工作流文件中**绝不包含**且**严禁填入** API 密钥、私有 URL、本地绝对路径或证书文件。凭据统一在服务端操作系统环境变量中维护。

---

## 3. 节点控件与参数说明

### 3.1 `InferwayH3Generate` (生成节点)
- **`model`**：模型标识，默认使用 `inferway/minimax-h3-768p`。
- **`prompt`**：文本提示词，支持多行输入。可直接在画布文本框中编辑。
- **`duration_seconds`**：生成视频时长（整秒），当前有效范围为 `5` 至 `10`，`t2v.json` 预设为 `5`。
- **`resolution`**：分辨率规格，支持 `default`、`1344x768`（横屏）及 `768x1344`（竖屏）。
- **`seed`**：随机种子。为避免浏览器 JavaScript 将 64 位无符号整数解析为双精度浮点数导致精度丢失，采用十进制字符串输入（例如 `"42"`）。留空表示由服务端生成随机种子。
- **可选图片插槽**：节点包含 `first_frame`、`last_frame`、`ref_image_1`、`ref_image_2`、`ref_image_3` 五个 `IMAGE` 输入插槽。在纯文生视频（T2V）场景下，这些插槽保持未连接（`link: null`）即可。
- **`profile`**：配置配置文件名称，默认固定为 `default`。
- **`wait_timeout_seconds`**：本地等待与轮询超时时限（秒），默认 `600`，取值范围 `1` 至 `3600`。

### 3.2 `InferwayH3Resume` (恢复节点)
- **`interaction_id`**：目标任务的交互 ID，工作流中预留占位符 `YOUR_INTERACTION_ID`。导入后需替换为实际任务 ID。
- **`profile`**：固定为 `default`。
- **`wait_timeout_seconds`**：等待超时时限，默认 `600`。
- **严格约束**：恢复节点仅执行状态查询与媒体下载，**绝不包含** Generate 逻辑，绝不会产生新的计费任务。

### 3.3 `InferwayH3Cancel` (取消节点)
- **`interaction_id`**：需要取消的目标任务 ID，工作流预留占位符 `YOUR_INTERACTION_ID`。
- **`profile`**：固定为 `default`。
- **输出端口**：输出 `interaction_id`（任务 ID）与 `status`（取消处置结果与计费结算说明）。
- **严格约束**：取消节点仅发送明确的取消指令，**绝不包含** Generate 逻辑。

### 3.4 原生 `PreviewAny` 节点说明
- 由于 ComfyUI 画布的字符串输出端口本身无法直接渲染文本内容，为了让用户能在前端画布上直观查看与交互，本工作流采用了 ComfyUI 官方自带的原生 `PreviewAny` 核心节点（无需安装任何第三方插件或修改运行时代码）：
  - 在 `t2v.json` 中，`InferwayH3Generate` 的 `interaction_id` 端口连接至 `PreviewAny`，生成完成后任务 ID 直接呈现在文本框内，用户可一键双击复制。
  - 在 `cancel.json` 中，`InferwayH3Cancel` 的 `status` 端口连接至 `PreviewAny`，取消执行后服务端的权威结果（如 `outcome=cancelled, charged=False`）直接显示在画布上。

---

## 4. 输出保存与媒体规格

在 `t2v.json` 与 `resume.json` 中，视频输出端口（`VIDEO`）直接连接到 ComfyUI 原生 `SaveVideo` 节点：
- **`filename_prefix`**：输出文件前缀，例如 `video/t2v` 或 `video/resume`，文件将保存于 ComfyUI 根目录的 `output/` 下。
- **`format`**：保存格式，设置为 `mp4`。
- **音视频保真**：本扩展利用 PyAV 原生流管道下载并封装 MP4 文件，完整保留视频画面与 AAC 音频轨道，不在内存中解包为逐帧张量，确保音频不丢失且显存占用极低。

---

## 5. 任务 ID (`interaction_id`) 的获取与复用

1. **获取 ID**：当 `InferwayH3Generate` 提交任务成功后，服务端分配的唯一 `interaction_id` 会直接输出到画布上的 `PreviewAny` 文本框中，用户可直接查看并复制。此外，该 ID 也会输出至 ComfyUI 服务端运行日志以及执行历史记录（Prompt History）中。
2. **复用至恢复流程**：若生成过程中本地网络断开、本地轮询超时或人为终止了本地任务，远端生成仍在后台进行。用户只需复制该 `interaction_id`，载入 `resume.json` 工作流，将 `YOUR_INTERACTION_ID` 替换为该 ID，即可无损拉取渲染完成的视频，无需重新计费。
3. **复用至取消流程**：若需明确停止正在排队或执行的远端任务并结算费用，将该 ID 填入 `cancel.json` 的 `interaction_id` 控件并执行，执行后可通过其连接的 `PreviewAny` 节点在画布上核对取消状态与扣费凭据。

---

## 6. 本地中断 (Stop/Interrupt) 与远程取消 (Remote Cancel) 的关键区别

| 操作类型 | 触发方式 | 行为特征 | 计费与远端状态影响 |
| --- | --- | --- | --- |
| **本地中断 (Local Stop)** | ComfyUI 界面点击 **Interrupt** 按钮，或终止本地进程 | 仅中断 ComfyUI 服务端当前的本地协程等待与网络轮询，关闭本地临时媒体租约。 | **不通知远端，不取消生成**。远端 GPU 仍会按计划完成任务，不会释放已锁定的余额。后续可通过 `resume.json` 恢复取回。 |
| **远程取消 (Remote Cancel)** | 运行 `cancel.json` 工作流 (`InferwayH3Cancel`) | 明确向 Inferway 服务端发送 `POST /v1/interactions` 请求（JSON 请求体包含 `{"op": "cancel", "interaction_id": ...}`）。 | **远端终止任务并结算**。若任务尚在等待认领则释放冻结扣费；若已开始交付或已完成则返回权威处置状态与扣费凭据。 |

---

## 7. 验证依据与边界说明

- **核验与运行环境**：Linux、Python 3.12.13、ComfyUI 0.37.0 与 ComfyUI 前端 1.53.6。其中 Generate/Resume/Cancel 节点调用链路针对授权生产环境核验，可直接导入的画布 JSON 文件则在原生 ComfyUI 环境下基于回环 API（Loopback / Fake API）独立验证。
- **生产环境核验结果（2026-09-21）**：
  - 完成一次授权的 `inferway/minimax-h3-768p` 文生视频真实生产任务调用（1344x768，5 秒规格），成功生成并下载解码为 H.264 画面与 AAC 音频的 MP4 视频（时长约 5.167 秒）。为保护隐私，任务交互 ID 与提示词全文均不予公开。
  - 同 ID 恢复（Resume）：使用相同 ID 执行 `resume.json` 成功通过原生 `SaveVideo` 重新保存含 AAC 音频的 MP4 视频，产生 **0 次新任务创建**。
  - 同 ID 取消（Cancel）：对已交付任务执行 `cancel.json`（发送 `POST /v1/interactions`，body 为 `op=cancel`），权威返回 `outcome=delivered, charged=True`，产生 **0 次新任务创建**。
  - 费用与扣费：整个 Generate、Resume、Cancel 流程中实际付费任务创建次数严格保持为 **1 次**；权威账单记录为 USD 冻结/扣除/退还 `0.20 / 0.20 / 0.00`（按当时的 50% 优惠计费；价格与促销活动可能调整，以实时控制台和模型目录为准）。
  - 运行代码准入候选版本：本次生产准入测试的底层运行代码对象为合并 SHA `da486d216495dad3fc798a350e6e5a868aaa0f52`，对应 24 文件发布包归档 SHA-256 `06996aed1aa623e96f1c93321857a225e85719bcb68b81628f90a9560aadd99c`（系本纯文档补充跟进前测试的代码候选版本，不代表最终打包归档哈希）。
- **受控合成与自动化测试**：自动化测试套件基于本地回环伪服务端（Fake API）与自签名 TLS 凭证执行，涵盖契约断言、字段变异与安全沙箱规则。
- **能力与平台边界**：
  - 当前生产环境观察为纯文生视频（`t2v-only`）；画布节点包含图片插槽，但仅在远端模型目录明确声明支持时才允许开启对应图片模式。
  - 测试中观察到的 AAC 音频结果证明了当前客户端与 `SaveVideo` 管线能够无损保留音频流，不代表远端服务的所有未来响应均保证包含 AAC 音频。
  - Windows 与 macOS 环境尚未经过独立核验。
  - 本工作流及扩展包发布状态为候选版本（Candidate），**尚未发布**至官方 ComfyUI Registry 索引或第三方公开仓库，用户暂无法通过 Registry 检索安装。
  - 文档严格遵守保密边界，绝不包含任何 API 密钥、私有路径、完整交互 ID、提示词原文或签名下载链接。
