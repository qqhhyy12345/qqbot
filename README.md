# QQ 机器人

基于 SnowLuma + AstrBot 的 QQ 机器人全栈项目：本地视觉、语音听写、B 站听看、分层记忆、情绪状态机。

```
QQ 客户端(hook 注入) → SnowLuma(OneBot v11) → 反向 WS → AstrBot → 大模型 API
```

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
