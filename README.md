# QQ 机器人

基于 SnowLuma + AstrBot 的 QQ 机器人全栈项目：本地视觉、语音听写、B 站听看、分层记忆、情绪状态机。

```
QQ 客户端(hook 注入) → SnowLuma(OneBot v11) → 反向 WS → AstrBot → 大模型 API
```

## 部署前置：SnowLuma + AstrBot

本项目代码不包含这两个框架本体，需要先独立部署（均为第三方/开源项目）：

### 1. SnowLuma（QQ 协议端）

- **仓库**：https://github.com/SnowLuma/SnowLuma
- **文档**：https://snowluma.github.io/
- **部署方式**：到 [Releases](https://github.com/SnowLuma/SnowLuma/releases/latest) 下载对应平台的完整版（内置 Node.js，推荐），Windows x64 为 `SnowLuma-vX.Y.Z-win-x64.zip`，解压即用：
  - Windows：运行 `launcher.bat`，浏览器开 `http://localhost:5099`（初始账号 `admin`，密码在启动日志 `initial credentials:` 行）
  - 无人值守部署需设置环境变量 `SNOWLUMA_ACCEPT_EULA=1` 和 `SNOWLUMA_ACCEPT_PRIVACY=1` 完成协议确认
  - 登录 QQ 后自动注入 hook，转成 OneBot v11（HTTP 3000 / WS 3001）
  - ⚠️ 非官方协议端接 QQ 有封号风险，**务必用小号**

### 2. AstrBot（机器人框架）

- **仓库**：https://github.com/AstrBotDevs/AstrBot
- **文档**：https://astrbot.app/
- **部署方式**（uv 安装，Python 3.12）：

  ```bash
  # Windows
  set UV_TOOL_DIR=D:\qqbot\_uv_tools
  set UV_TOOL_BIN_DIR=D:\qqbot\_uv_bin
  uv tool install astrbot --python 3.12
  astrbot init   # 初始化数据目录
  astrbot run    # WebUI 默认 http://127.0.0.1:6185
  ```

  Docker 方式见官方文档：`docker run -p 6185:6185 soulter/astrbot`（挂载 `/AstrBot/data`）

### 3. 对接（本项目的前提配置）

1. SnowLuma WebUI → OneBot 配置：只监听 `127.0.0.1`，添加反向 WS 客户端指向 `ws://127.0.0.1:6199/ws`
2. AstrBot WebUI → 平台适配器：启用 `aiocqhttp`（OneBot v11），端口 6199
3. AstrBot → 服务提供商：配置 OpenAI 兼容接口（本项目用火山方舟 `deepseek-v4.1-flash`）
4. 把本仓库 `plugins/` 下的插件复制到 `astrbot/data/plugins/`，重启 AstrBot 生效

## 支持的环境与硬件加速

### 运行环境

| 项 | 要求 |
| --- | --- |
| OS | Windows x64（本项目实测）/ Linux x64、arm64（SnowLuma 原生支持，AstrBot 官方支持） |
| Python | 3.12（AstrBot 主环境 + 各 worker 子进程） |
| Node.js | SnowLuma 完整版自带，Lite 版需 24+ |
| 本地推理依赖 | llama.cpp（gguf）/ OpenVINO ≥ 2026.4（IR 推理）/ faster-whisper（ctranslate2） |

### OpenVINO（Intel 路线，本项目实测在用）

Intel 出品的跨平台推理引擎：模型经 `optimum-cli` 导出成 IR 格式后，同一份模型可跑在 **Intel CPU / 核显（iGPU）/ 独显 / NPU** 上，无需改代码。

- **适用范围**：Intel 核显笔记本（如 Iris Xe / Arc 核显）的性价比方案——不花一分钱把闲置的 iGPU 用起来；也覆盖 Arc 独显和带 NPU 的 Core Ultra
- **本项目实测**：Qwen2-VL-2B 走 `openvino-genai` 的 VLMPipeline 在 Iris Xe 上跑视觉，比 llama.cpp 纯 CPU **快约 1 倍**（960px 图 ~10s），且能吃下 llama.cpp 必崩的大图
- **局限**：openvino-genai 的管线对模型导出格式敏感（本项目 Whisper 的 IR 就因 optimum-intel 版本组合导出为无状态格式而跑不了，退回 faster-whisper CPU）；AMD/NVIDIA 显卡不在加速范围内

### 替代品对比：ROCm 与 CUDA

| | **CUDA** | **ROCm** | **OpenVINO** |
| --- | --- | --- | --- |
| 厂商 | NVIDIA | AMD | Intel |
| 生态成熟度 | 最成熟，所有主流框架一等公民 | 追赶中，Linux 支持好，**Windows 支持有限**（HIP SDK 渐进补齐） | Intel 全家桶内成熟 |
| 适用硬件 | NVIDIA 显卡（游戏卡即可） | AMD Radeon **RDNA3（RX 7000 系）与 RDNA4（RX 9000 系）** 已官方支持，更老的 RDNA1/2 部分可用；A 卡核显基本不在列 | Intel CPU / iGPU / Arc / NPU |

**ROCm（HIP SDK）Windows 官方支持名单**（依据 rocm.docs.amd.com，2026 年）：

- **RDNA4（完整支持：Runtime + HIP SDK + Debugger）**：RX 9070 XT / 9070 / 9070 GRE / 9060 XT / 9060（gfx1200/1201），工作站卡 AI PRO R9700
- **RDNA3（Runtime + HIP SDK 支持，Windows 上 Debugger 不可用）**：RX 7900 XTX / 7900 XT / 7900 GRE、RX 7800 XT / 7700 XT、RX 7650 GRE / 7600 XT / 7600（gfx1100~1102），工作站卡 PRO W7900 / W7800 / W7700
- **RDNA3.5 APU（完整支持）**：Ryzen AI Max 385 / 390 / 395（含 Pro 版，gfx1151）、Ryzen AI 300 系列（gfx1150）——Strix Halo 一族的核显是官方点名支持的
- **RDNA2（RX 6000 系）**：在新版矩阵中已降级/标注不受支持，社区靠 `HSA_OVERRIDE_GFX_VERSION` 强绕（官方声明不支持 Windows 上这么做）
- **A 卡核显（780M/890M 等）**：不在官方名单，部分工具链可经 override 勉强工作
| 典型用途 | 训练 + 推理全能，深度学习首选 | Linux 下 AMD 卡跑训练/推理 | 边缘/端侧推理、核显白嫖算力 |
| Windows 体验 | 开箱即用 | 常需 WSL2 或等待 Windows 后端完善 | 开箱即用 |

**选型速查**：

- NVIDIA 卡 → CUDA，别犹豫
- AMD 卡（RDNA3/RDNA4）→ ROCm 官方支持，Linux 体验最好；Windows 后端在逐步完善中，不稳时退 WSL2 或 ONNX Runtime-DirectML；A 卡核显仍建议 CPU/OpenVINO 路线
- Intel 核显/CPU/NPU、无独显的轻薄本 → OpenVINO（本项目路线）
- 都没有 → 纯 CPU + 小参数量化模型（faster-whisper int8、Q4 gguf），本项目语音/旋律分析就是纯 CPU 跑的

## 目录结构

```
├─ plugins/   AstrBot 插件（17 个，每个插件独立目录）
├─ tools/     运维/测试/诊断脚本
├─ scripts/   启动脚本（SnowLuma + AstrBot）
└─ docs/      部署说明与技术记录（已脱敏）
```

## 插件详细介绍

### 记忆与状态

**`astrbot_plugin_memory_tier`** — 分层记忆（自研核心插件）

解决 AstrBot 内置上下文压缩"一次性"的缺陷：`ContextManager.process()` 的结果只用于当次请求、不持久化，导致每轮都重揉完整历史、摘要漂移。本插件把老对话**归档一次**落盘为持久摘要（`bot-memory/archive/<umo>.md` + 游标 meta.json），只保留最近 N 轮原文进上下文。实测 96K 上下文压到 12K（-87.5%），且因为只在阈值触发时改写 `req.contexts` 末段，prompt cache 前缀得以保全。安全边界：绝不写 `conversations` 表，删插件即恢复原状。参数：`KEEP_RECENT_ROUNDS=15`、`SOFT_LIMIT_TOKENS=22000`、限频 300s、单次最多归档 30 轮。

**`astrbot_plugin_memory_beyond`** — 长期记忆工具

给模型四个记忆工具（`memory_read` / `memory_write` / `memory_edit` / `memory_search`），文件按三层作用域组织（global 自身准则 / session 会话 / people 人物档案），frontmatter 自动盖 `source` 来源标注（含后台提取路径的兜底盖章），并维护 MEMORY.md 索引。工具描述里内置行为准则：写前查重、按人按事合并文件、禁止直写索引。

**`astrbot_plugin_emotion`** — 五轴情绪状态机

插件级情绪引擎：愉悦/亲近（分人）/唤醒/疲惫/好奇五轴，每轴带快/慢双半衰期分量（如愉悦 90min / 48h），强事件沉入慢分量形成"余温"。事件感知由规则表驱动（辱骂/冷落/夸奖/撒娇/道歉 + 敷衍检测），带完整防刷机制：单事件限幅 0.06、单轮 0.10、同源同向 10 分钟饱和递减、LLM 自调工具 `adjust_mood` 每日额度封顶。状态向量翻译成自然语言注入上下文末尾（不碰 system_prompt，保缓存）。状态存偏差而非绝对值，原子写盘 + 版本迁移。

### 多模态感知

**`astrbot_plugin_local_vision`** — 本地视觉转述

聊天图片不进云端：按需拉起本地推理服务，图片缩到长边 960 → 一句中文描述进上下文（原图 4500 token → 转述 ~54 token）。双引擎可切：llama.cpp（Vulkan/CPU，gguf）或 **OpenVINO VLMPipeline**（Intel iGPU，IR，本项目现用，快约 1 倍）。HTTP worker 模拟 llama-server 接口，插件侧生命周期管理：闲置自动卸载、单次推理超时降级（转述失败原图兜底直传）、两级结果缓存、来图时同步清扫旧临时文件。

**`astrbot_plugin_asr`** — 语音转文字服务

从 listen_bili 拆出的独立 ASR 能力：worker 子进程内 av 解码 → faster-whisper small（int8 CPU，14 线程）→ opencc 繁转简。三个入口：`asr_transcribe` 工具（转音频直链）、`on_llm_request` 钩子（QQ 语音消息自动转写，含引用消息里的语音，单条上限 2 条）、`transcribe_file` 公开接口（其他插件 import 即用）。转写后把 Record 组件就地换成 Plain 文字并摘掉 `audio_urls`——防止音频漏给纯文本模型。听完即删：只删自己 temp 目录内的文件，目录外一律拒绝。

**`astrbot_plugin_listen_bili`** — 听 B 站视频

`bili_listen(video, minutes)` 工具：wbi 签名 + 复用浏览器 cookie 取音频流（durl mp4 回退、finger/spi 领 buvid 防 412 风控）→ 下载 m4s → asr 转写 + librosa 旋律特征（BPM/调性/音域/走向，纯语音内容自动跳过旋律段）→ 文字汇报。音频在 finally 里同步删除。附带 **B 站卡片直读**：管线早期钩子把转发卡片的 Json 组件解码成「标题 + BV 号」回写 message_str——她能直接"看到"卡片并自己决定要不要听，b23.tv 短链走 302 解析。

**`astrbot_plugin_bilibili`** — 只读逛 B 站

`bili_search` / `bili_hot` / `bili_video` / `bili_up_dynamics` 四个只读工具，带 wbi 签名与风控限速（请求间隔 1s），让她能自己刷热门、查视频、翻 UP 主动态。

**`astrbot_plugin_fetch_url`** — 联网查资料

三件套：`search_web`（必应 RSS 通道——HTML 结果页是 JS 壳抓不到，RSS 是本机唯一稳定通道；内置词典页过滤、实体提取、够用判定与条件重试）、`baike_lookup`（百度百科三层通道：openapi → 必应 site 限定 → wap 正文，"宁可漏不可错"的相关性判定层）、`fetch_url`（网页正文抓取与净化）。

### 表达与人格

**`astrbot_plugin_voice_tool`** — 语音合成

`send_voice` 工具：模型自选文案与音色。修掉了 AstrBot 把"随机语音开关"和"取 TTS 服务"绑死的问题——三级 provider 回退（会话偏好 → 配置指定 → 任一可用）。返回状态码 `已发送=1 / 未发送=0(原因)`，模型拿到 1 无需确认重发。音频文件发完（失败也）即删。

**`astrbot_plugin_persona_editor`** — 人格卡读写

`persona_read` / `persona_edit` 工具，支持双人格（主人私聊 / 其他人）分卡编辑。**仅主人可用**：工具层校验会话 UMO，其他任何会话调用直接拒绝。

**`astrbot_plugin_qzone`** — QQ 空间

`llm_view_feed` / `llm_publish_feed` / `llm_delete_feed`（浏览/发说说/删说说/点赞/评论/投稿表白墙）。**仅主人可用**：工具层硬闸门校验发送者 QQ，配合 session_profile 的画像层裁剪形成双重封锁。

### 框架级管控（安全与成本）

**`astrbot_plugin_session_profile`** — 会话画像

按会话类型（主人私聊 / 普通私聊 / 群聊）裁剪工具表和提示小节：非主人会话里敏感工具**连定义都不出现**（省 token + 攻击面最小化），并注入当前会话的能力地图，让她知道"我在哪、能用什么"。

**`astrbot_plugin_wake_gate`** — 唤醒闸门

群聊里没人 @ 她就在进入 LLM 之前直接掐断（`stop_event()`），零 token 消耗。处理群友按昵称呼叫（纯文本 `@昵称` 无 At 组件）的场景，@ 本人时无条件放行并注入"必须实质回复"约束——防止模型自己判断"不重要"就沉默。

**`astrbot_plugin_no_leak`** — 出站净化

挂发送前钩子，四级净化出站文本：成对标签 → 落单开标签（删到结尾）→ 落单闭标签 → 裸露元信息行，覆盖 `system_reminder` 及连字符/空格变体，剥完为空则不发空消息。防内部标记（系统时间、工具定义等）泄漏给聊天对象。

**`astrbot_plugin_contact_list`** — 联系人目录

`llm_list_contacts` 工具（仅私聊可用）：好友/群列表查询，配合她主动找人、认人的场景。
| `astrbot_plugin_contact_list` | 好友/群列表查询 |

## 设计要点

- **缓存优先**：所有动态注入走 `req.contexts` 末尾，绝不写 system_prompt，避免打崩 prompt cache（实测缓存命中率 80%+）
- **本地优先**：图片/音频一律本地小模型处理，只给云端传文字（4500 token/图 → 54 token）
- **听完/看完即删**：临时媒体文件在 finally 里同步清理
- **权限分层**：主人/普通私聊/群聊三级工具表，敏感工具（人格卡、QQ 空间）双重封锁（画像隐藏 + 工具层校验）
- **沙箱进程隔离**：重活（ASR/视觉/旋律）全部子进程执行，主环境零依赖污染，闲置自动卸载

## 环境说明

- SnowLuma 2.1.1（QQ 协议端，第三方，务必用小号）
- AstrBot 4.29.1 + Python 3.12 venv
- 本地推理：llama.cpp b11853 / OpenVINO 2026.4.1（Iris Xe GPU）/ faster-whisper small

详见 [docs/部署说明.md](docs/部署说明.md) 与 [docs/技术记录.md](docs/技术记录.md)。

## 安全说明

- 本仓库所有代码已脱敏：QQ 号、账号、密钥均替换为 `<OWNER_QQ>` / `<BOT_QQ>` 等占位符
- API Key、cookie、面板密码等配置文件不在仓库内（`cmd_config.json`、`*_config.json` 等）
