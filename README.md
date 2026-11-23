# AI Passport MCP (`ai-passport-mcp`)

macOS 侧的 **FoloToy AI Passport（ESP32-C3）完整 MCP Server & 本地语音中枢**。

安装后，你可以直接通过 **Antigravity / Claude Code / Cursor / Trae** 等 AI 客户端：
1. **一键拉取并烧录固件**：自动从 GitHub Releases 下载并校验 SHA256，使用内置 `esptool` 直接刷入插在 Mac 上的 AI Passport 硬件（**无需安装 ESP-IDF**）。
2. **多 AI 语音伴侣**：自动在后台保活本地 Hub（HTTP `8765` + UDP `8766` 局域网自动发现），向硬件播报任务进度、通过实体按键或离线语音回复 AI。

---

## 快速安装（推荐 `uvx`，零手动配置）

先确保本机已安装 [`uv`](https://docs.astral.sh/uv/)（macOS：`brew install uv`）。

在你的 AI 客户端 MCP 配置文件（如 `mcp_config.json` / `.mcp.json`）中加入：

```json
{
  "mcpServers": {
    "ai-passport": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/TironFox/ai-passport-mcp.git",
        "passport-mcp"
      ]
    }
  }
}
```

> `passport-mcp` 启动时会自动检测并在后台拉起本地 `passport-hub` 单例服务（默认端口 `8765` / UDP `8766`），无需单独开终端运行。如需禁用自动拉起，可设置环境变量 `PASSPORT_AUTO_START_HUB=0`。

---

## 一键刷入硬件固件（对话式操作）

1. 用 USB-C 数据线将 **FoloToy AI Passport** 连接到 Mac。
2. 在 AI 对话框里直接说：
   > “帮我查看已连接的 AI Passport 设备，拉取最新版本固件并刷入。”
3. AI 会依次调用：
   - `list_passport_devices`：扫描 macOS 串口（自动优先识别 ESP32-C3 原生 `/dev/cu.usbmodem*`）。
   - `list_firmware_releases`：查询 GitHub Releases 上的最新固件版本。
   - `flash_passport_firmware`：下载 `.bin` 并校验 SHA256，向你确认串口号后执行 `esptool` 烧录到 `0x0`。

---

## 提供的 MCP Tools

| Tool | 说明 |
| :--- | :--- |
| `list_passport_devices` | 扫描本机已连接的 USB 串口设备，识别 ESP32-C3 硬件 |
| `list_firmware_releases` | 查询 GitHub Releases 可用固件版本与更新说明 |
| `flash_passport_firmware` | 拉取并校验指定版本固件（或本地 `.bin`），安全确认后刷入硬件 `0x0` |
| `report_task_status` | 将任务进度/完成状态推送到 AI Passport 屏幕并语音播报 |
| `ask_passport_user` | 向硬件推送问题/决策选项，阻塞等待用户按键或语音回复 |
| `check_passport_reply` | 非阻塞查询指定 `task_id` 的用户硬件回复 |

---

## 手动运行本地 Hub（可选）

如果你想前台查看日志或手动启动 Hub：

```bash
uvx --from git+https://github.com/TironFox/ai-passport-mcp.git passport-hub
```

启用离线语音识别（可选安装 `faster-whisper`）：

```bash
pip install "git+https://github.com/TironFox/ai-passport-mcp.git#egg=ai-passport-mcp[asr]"
```
