# Inferway ComfyUI 画布工作流 (UI Canvas Workflows)

本目录提供可直接导入 ComfyUI 前端画布的原生工作流文件（LiteGraph UI 格式），支持在 ComfyUI Web 界面中可视化查看、调整节点参数并执行任务。

---

## 1. 包含的工作流文件

| 文件名 | 核心节点链路 | 功能说明 |
| --- | --- | --- |
| `Inferway Text to Video.json` | `InferwayH3GenerateV2` &rarr; `SaveVideo`<br>`InferwayH3GenerateV2` &rarr; `PreviewAny` | 文生视频。填提示词、时长、分辨率和 seed，成片直接交给原生 `SaveVideo` 保存，任务 `interaction_id` 显示在 `PreviewAny` 文本框里，方便复制。 |
| `Inferway First and Last Frame.json` | `LoadImage` &times;2 &rarr; `InferwayH3GenerateV2` &rarr; `SaveVideo`<br>`InferwayH3GenerateV2` &rarr; `PreviewAny` | 首尾帧生视频。两个 `LoadImage` 分别接 `first_frame` 和 `last_frame`，成片从首帧出发、落到尾帧。只有模型列表当前开放这个模式时才能用，否则服务端以 `mode_unavailable` 拒绝；首尾帧和参考图不能在同一个节点里混用。 |
| `Inferway Reference Images.json` | `LoadImage` &times;3 &rarr; `InferwayH3GenerateV2` &rarr; `SaveVideo`<br>`InferwayH3GenerateV2` &rarr; `PreviewAny` | 参考图生视频。三个 `LoadImage` 分别接 `ref_image_1` 到 `ref_image_3`，成片参考图里的人物或场景。同样要模型列表开放这个模式才能用，且不能和首尾帧混用。 |
| `Inferway Resume.json` | `InferwayH3Resume` &rarr; `SaveVideo` | 按任务 ID 取回视频：接着等没等完的任务，或重新下载已完成的视频。不会新下单。 |
| `Inferway Cancel.json` | `InferwayH3Cancel` &rarr; `PreviewAny` | 取消远端任务，`PreviewAny` 显示服务端给出的处置结果和是否扣费（`status` / charged）。 |
| `Inferway History.json` | `InferwayH3History` &rarr; `InferwayH3Resume` &rarr; `SaveVideo` | 在画布上列出最近的任务，并把最新一个可取回的任务 ID 直接交给 Resume。 |
| `Inferway Prompt Expand.json` | `InferwayPromptExpand` &rarr; `InferwayH3GenerateV2` &rarr; `SaveVideo` | 用 MiMo 把一句简短的想法扩写成视频提示词，再交给生成节点。扩写按 token 计费。 |

每个 `.json` 旁边同名的 `.jpg` 是模板面板里显示的缩略图。

> **画布格式说明**：本目录的工作流是 ComfyUI 前端画布格式（有 `nodes`、`links`、坐标 `pos`、尺寸 `size` 和 `widgets_values`），可以直接打开；插件根目录 `api_workflows/*.json` 里的是给 HTTP API 用的 Prompt 图，格式不同，也不会出现在模板面板里。

---

## 2. 导入与运行

### 2.1 打开工作流
1. 打开 ComfyUI 网页界面（例如 `http://127.0.0.1:8188`）。
2. 点左侧边栏的「模板」，在「扩展」下选「Inferway 示例」，点卡片即可打开（英文界面显示为 Inferway examples）。
3. 也可以把本目录下任意 `.json` 拖到画布空白处，或用 **Ctrl+O** 打开。

### 2.2 配置 API Key
1. **安装插件**：在 ComfyUI-Manager 里搜索 Inferway H3 安装，或把仓库克隆到 `custom_nodes/` 并安装依赖，然后重启 ComfyUI。
2. **保存 API Key**：在 ComfyUI 菜单里点 **Inferway → Manage Inferway API key**（或在设置里的 Inferway 分组打开），把 key 存到 `default` 配置里。key 只保存在本机，不会写进工作流、PNG 或任何控件。也可以继续用服务端环境变量 `INFERWAY_API_KEY`。
3. **安全红线**：不要把 API Key、私有 URL、本地绝对路径或证书写进画布或工作流文件。

---

## 3. 节点控件与参数说明

### 3.1 `InferwayH3GenerateV2` (生成节点)

示例工作流使用 V2 节点；旧的 `InferwayH3Generate` 标记为 deprecated（显示名 `Inferway H3 Generate (legacy)`），node_id 与输入顺序完全不变，历史工作流照常加载运行。
- **`model`**：模型标识，默认使用 `inferway/minimax-h3-768p`。
- **`prompt`**：文本提示词，支持多行输入。可直接在画布文本框中编辑。
- **`duration_seconds`**：生成视频时长（整秒），当前有效范围为 `5` 至 `10`，`Inferway Text to Video.json` 预设为 `5`。
- **`resolution`**：分辨率规格，支持 `default`、`1344x768`（横屏）及 `768x1344`（竖屏）。
- **`seed`**：随机种子。V2 节点用 ComfyUI 原生的整数控件，范围 `0` 到 `18446744073709551615`，默认 `0`；后面的 *control after generate* 默认 `randomize`。注意：每点一次运行就会新下一单并扣费，跟 seed 是不是固定无关；固定 seed 只是让画面尽量接近，不会复用上一单。等待超时后不要再点运行，用 Resume 节点填入任务 id 取回。旧的 `InferwayH3Generate`（legacy）仍是十进制字符串写法，留空表示不传 seed。
- **可选图片插槽**：节点包含 `first_frame`、`last_frame`、`ref_image_1`、`ref_image_2`、`ref_image_3` 五个 `IMAGE` 输入插槽。在纯文生视频（T2V）场景下，这些插槽保持未连接（`link: null`）即可。
- **`profile`**：配置文件名称，默认固定为 `default`。
- **`wait_timeout_seconds`**：本地等待时限（秒），默认 `0` 表示按排队状态自动等待（总上限 `7200` 秒），也可显式填 `60` 至 `7200`。到限只是停止本地等待，**不会**取消任务。

### 3.2 `InferwayH3Resume` (恢复节点)
- **`interaction_id`**：目标任务的交互 ID，工作流中预留占位符 `YOUR_INTERACTION_ID`。导入后需替换为实际任务 ID。
- **`profile`**：固定为 `default`。
- **`wait_timeout_seconds`**：本地等待时限，默认 `0`（自动等待，上限 `7200` 秒），到限不取消任务。
- **严格约束**：恢复节点仅执行状态查询与媒体下载，**绝不包含** Generate 逻辑，绝不会产生新的计费任务。

### 3.3 `InferwayH3Cancel` (取消节点)
- **`interaction_id`**：需要取消的目标任务 ID，工作流预留占位符 `YOUR_INTERACTION_ID`。
- **`profile`**：固定为 `default`。
- **输出端口**：输出 `interaction_id`（任务 ID）与 `status`（取消处置结果与计费结算说明）。
- **严格约束**：取消节点仅发送明确的取消指令，**绝不包含** Generate 逻辑。

### 3.4 原生 `PreviewAny` 节点说明
- 由于 ComfyUI 画布的字符串输出端口本身无法直接渲染文本内容，为了让用户能在前端画布上直观查看与交互，本工作流采用了 ComfyUI 官方自带的原生 `PreviewAny` 核心节点（无需安装任何第三方插件或修改运行时代码）：
  - 在 `Inferway Text to Video.json` 中，`InferwayH3GenerateV2` 的 `interaction_id` 端口连接至 `PreviewAny`，生成完成后任务 ID 直接呈现在文本框内，用户可一键双击复制。
  - 在 `Inferway Cancel.json` 中，`InferwayH3Cancel` 的 `status` 端口连接至 `PreviewAny`，取消执行后服务端的权威结果（如 `outcome=cancelled, charged=False`）直接显示在画布上。

---

## 4. 输出保存与媒体规格

在 `Inferway Text to Video.json` 与 `Inferway Resume.json` 中，视频输出端口（`VIDEO`）直接连接到 ComfyUI 原生 `SaveVideo` 节点：
- **`filename_prefix`**：输出文件前缀，例如 `video/t2v` 或 `video/resume`，文件将保存于 ComfyUI 根目录的 `output/` 下。
- **`format`**：保存格式，设置为 `mp4`。
- **音视频保真**：本扩展利用 PyAV 原生流管道下载并封装 MP4 文件，完整保留视频画面与 AAC 音频轨道，不在内存中解包为逐帧张量，确保音频不丢失且显存占用极低。

---

## 5. 任务 ID (`interaction_id`) 的获取与复用

1. **获取 ID**：当 `InferwayH3GenerateV2` 提交任务成功后，服务端分配的唯一 `interaction_id` 会直接输出到画布上的 `PreviewAny` 文本框中，用户可直接查看并复制。此外，该 ID 也会输出至 ComfyUI 服务端运行日志以及执行历史记录（Prompt History）中。
2. **复用至恢复流程**：若生成过程中本地网络断开、本地轮询超时或人为终止了本地任务，远端生成仍在后台进行。用户只需复制该 `interaction_id`，载入 `Inferway Resume.json` 工作流，将 `YOUR_INTERACTION_ID` 替换为该 ID，即可无损拉取渲染完成的视频，无需重新计费。
3. **复用至取消流程**：若需明确停止正在排队或执行的远端任务并结算费用，将该 ID 填入 `Inferway Cancel.json` 的 `interaction_id` 控件并执行，执行后可通过其连接的 `PreviewAny` 节点在画布上核对取消状态与扣费凭据。

---

## 6. 本地中断 (Stop/Interrupt) 与远程取消 (Remote Cancel) 的关键区别

| 操作类型 | 触发方式 | 行为特征 | 计费与远端状态影响 |
| --- | --- | --- | --- |
| **本地中断 (Local Stop)** | ComfyUI 界面点击 **Interrupt** 按钮，或终止本地进程 | 仅中断 ComfyUI 服务端当前的本地协程等待与网络轮询，关闭本地临时媒体租约。 | **不通知远端，不取消生成**。远端 GPU 仍会按计划完成任务，不会释放已锁定的余额。后续可通过 `Inferway Resume.json` 恢复取回。 |
| **远程取消 (Remote Cancel)** | 运行 `Inferway Cancel.json` 工作流 (`InferwayH3Cancel`) | 明确向 Inferway 服务端发送 `POST /v1/interactions` 请求（JSON 请求体包含 `{"op": "cancel", "interaction_id": ...}`）。 | **远端终止任务并结算**。若任务尚在等待认领则释放冻结扣费；若已开始交付或已完成则返回权威处置状态与扣费凭据。 |

---

## 7. 验证依据与边界说明

- **核验与运行环境**：Linux、Python 3.12.13、ComfyUI 0.37.0 与 ComfyUI 前端 1.53.6。其中 Generate/Resume/Cancel 节点调用链路针对授权生产环境核验，可直接导入的画布 JSON 文件则在原生 ComfyUI 环境下基于回环 API（Loopback / Fake API）独立验证。
- **生产环境核验结果（2026-09-21）**：
  - 完成一次授权的 `inferway/minimax-h3-768p` 文生视频真实生产任务调用（1344x768，5 秒规格），成功生成并下载解码为 H.264 画面与 AAC 音频的 MP4 视频（时长约 5.167 秒）。为保护隐私，任务交互 ID 与提示词全文均不予公开。
  - 同 ID 恢复（Resume）：使用相同 ID 执行 `Inferway Resume.json` 成功通过原生 `SaveVideo` 重新保存含 AAC 音频的 MP4 视频，产生 **0 次新任务创建**。
  - 同 ID 取消（Cancel）：对已交付任务执行 `Inferway Cancel.json`（发送 `POST /v1/interactions`，body 为 `op=cancel`），权威返回 `outcome=delivered, charged=True`，产生 **0 次新任务创建**。
  - 费用与扣费：整个 Generate、Resume、Cancel 流程中实际付费任务创建次数严格保持为 **1 次**；权威账单记录为 USD 冻结/扣除/退还 `0.20 / 0.20 / 0.00`（按当时的 50% 优惠计费；价格与促销活动可能调整，以实时控制台和模型目录为准）。
  - 运行代码准入候选版本：本次生产准入测试的底层运行代码对象为合并 SHA `da486d216495dad3fc798a350e6e5a868aaa0f52`，对应 24 文件发布包归档 SHA-256 `06996aed1aa623e96f1c93321857a225e85719bcb68b81628f90a9560aadd99c`（系本纯文档补充跟进前测试的代码候选版本，不代表最终打包归档哈希）。
- **受控合成与自动化测试**：自动化测试套件基于本地回环伪服务端（Fake API）与自签名 TLS 凭证执行，涵盖契约断言、字段变异与安全沙箱规则。
- **能力与平台边界**：
  - 当前生产环境观察为纯文生视频（`t2v-only`）；画布节点包含图片插槽，但仅在远端模型目录明确声明支持时才允许开启对应图片模式。
  - 测试中观察到的 AAC 音频结果证明了当前客户端与 `SaveVideo` 管线能够无损保留音频流，不代表远端服务的所有未来响应均保证包含 AAC 音频。
  - Windows 与 macOS 环境尚未经过独立核验。
  - 本工作流所属扩展包已发布至官方 ComfyUI Registry（包 ID `inferway-comfy`，发布者 `inferway`）及公开仓库 https://github.com/inferway/comfyui-inferway ，用户可通过 ComfyUI-Manager 检索并安装。
  - 文档严格遵守保密边界，绝不包含任何 API 密钥、私有路径、完整交互 ID、提示词原文或签名下载链接。
