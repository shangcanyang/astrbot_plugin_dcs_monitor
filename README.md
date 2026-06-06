# AstrBot DCS 监控与预警插件

本插件用于在 AstrBot 中监控工业 DCS（分布式控制系统）的实时数据点位，支持阈值预警、多会话绑定、自动登录与 token 刷新，可方便地将预警消息推送到 QQ、微信等聊天平台。

## 📦 功能特性

- ✅ 异步登录，自动处理 token 过期与重连
- ✅ 定时查询指定 DCS 点位的最新值（可配置间隔）
- ✅ 高/低阈值预警，支持恢复通知
- ✅ 多会话绑定：可将预警消息发送到多个群聊/私聊
- ✅ 插件生命周期管理：启动/停止监控任务
- ✅ 基于 `_conf_schema.json` 的 WebUI 配置界面
- ✅ 提供常用控制指令

## 📥 安装

1. 将本插件克隆到 AstrBot 的插件目录：
   ```bash
   cd AstrBot/data/plugins
   git clone https://github.com/你的用户名/astrbot_plugin_dcs_monitor.git
   ```

2. 重启 AstrBot，或在 WebUI 中启用插件。

3. 安装依赖（插件目录下的 `requirements.txt`）：
   ```bash
   pip install -r requirements.txt
   ```

## ⚙️ 配置

在 AstrBot WebUI 的插件管理页面，找到 `astrbot_plugin_dcs_monitor`，填写以下配置项：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `api_base` | DCS API 基础地址 | `http://119.36.147.45:8041` |
| `username` | 登录用户名 | `J21137` |
| `password` | 登录密码 | `Cxfpass@123` |
| `client_id` | 客户端 ID | `ms-content-sample` |
| `point_prefix` | 点 ID 前缀 | `system:LinkObject:serverdata1:system:` |
| `point_name` | 监控点名称 | `HCY_FICOMP_710B101_PV` |
| `check_interval` | 检查间隔（秒） | `10` |
| `low_threshold` | 低阈值（可选，低于此值预警） | `0.0` |
| `high_threshold` | 高阈值（可选，高于此值预警） | `100.0` |

> 注意：`point_name` 和 `point_prefix` 拼接后必须为 DCS 系统中真实存在的点 ID。

## 🤖 使用指令

所有指令通过发送聊天消息触发（需在已绑定预警的会话中发送 `/dcs_bind` 后）。

| 指令 | 说明 |
|------|------|
| `/dcs_start` | 启动后台监控任务 |
| `/dcs_stop`  | 停止监控任务 |
| `/dcs_status` | 查看监控状态、当前点位及阈值配置 |
| `/dcs_now`   | 立即查询一次当前点位值 |
| `/dcs_bind`  | 将当前聊天会话绑定为预警接收目标 |
| `/dcs_unbind`| 解除当前会话的预警绑定 |

## 📡 预警流程

1. 插件启动后自动登录 DCS 系统。
2. 按照 `check_interval` 定期获取点位值。
3. 若超过配置的阈值：
   - 首次触发 → 推送预警消息到所有绑定会话。
   - 状态恢复正常 → 推送恢复通知。
4. 若 token 过期，自动重新登录。

## 🛠️ 开发与调试

- 插件基于 AstrBot 3.x 的 `Star` 模型开发，完全异步。
- 日志输出使用 `astrbot.api.logger`。
- 修改代码后可在 WebUI 中点击「重载插件」生效，无需重启机器人。

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。