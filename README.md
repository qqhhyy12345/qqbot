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
| 适用硬件 | NVIDIA 显卡（游戏卡即可） | AMD Radeon（官方仅支持部分型号，如 RX 7900/9000 系；A 卡核显基本不在列） | Intel CPU / iGPU / Arc / NPU |
| 典型用途 | 训练 + 推理全能，深度学习首选 | Linux 下 AMD 卡跑训练/推理 | 边缘/端侧推理、核显白嫖算力 |
| Windows 体验 | 开箱即用 | 常需 WSL2 或等待 Windows 后端完善 | 开箱即用 |

**选型速查**：

- NVIDIA 卡 → CUDA，别犹豫
- AMD 卡 + Linux → ROCm 可行，先查官方支持卡列表；AMD 卡 + Windows → 大概率退回 CPU 或 DirectML/ONNX Runtime 路线
- Intel 核显/CPU/NPU、无独显的轻薄本 → OpenVINO（本项目路线）
- 都没有 → 纯 CPU + 小参数量化模型（faster-whisper int8、Q4 gguf），本项目语音/旋律分析就是纯 CPU 跑的

## 目录结构

```
├─ plugins/   AstrBot 插件（17 个，每个插件独立目录）
├─ tools/     运维/测试/诊断脚本
├─ scripts/   启动脚本（SnowLuma + AstrBot）
└─ docs/      部署说明与技术记录（已脱敏）
```

## 插件一览

| 插件 | 职责 |
| --- | --- |
| `astrbot_plugin_memory_tier` | 分层记忆：老对话归档成持久摘要，只保留近期原文（省 token + 保缓存前缀） |
| `astrbot_plugin_memory_beyond` | 长期记忆工具（read/write/edit/search）+ 记忆规则注入 |
| `astrbot_plugin_emotion` | 五轴情绪状态机（PAD 扩展）：快慢双分量衰减、事件感知、按人记账、自调额度 |
| `astrbot_plugin_local_vision` | 本地视觉：llama.cpp / OpenVINO 转述图片，图片不进云端上下文 |
| `astrbot_plugin_asr` | 本地语音转文字（faster-whisper small int8 CPU + opencc 简体化） |
| `astrbot_plugin_listen_bili` | 听 B 站视频：卡片直读、音频下载、ASR 转写、librosa 旋律特征，听完即删 |
| `astrbot_plugin_bilibili` | 只读逛 B 站（搜索/详情/热评/动态） |
| `astrbot_plugin_voice_tool` | 语音合成（模型自选、返回状态码、发完即删） |
| `astrbot_plugin_fetch_url` | 联网查资料（必应 RSS 搜索 + 百度百科三层通道） |
| `astrbot_plugin_qzone` | QQ 空间读写（仅主人，工具层硬闸门） |
| `astrbot_plugin_persona_editor` | 人格卡读写（仅主人） |
| `astrbot_plugin_session_profile` | 会话画像：按会话类型裁剪工具表 + 能力地图注入 |
| `astrbot_plugin_wake_gate` | 群聊唤醒闸门（没人 @ 不进 LLM，省 token） |
| `astrbot_plugin_no_leak` | 出站净化：剥内部标记（system_reminder 等），防提示词外泄 |
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
