---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 00744ee2872ebb6688a71f082191730f_804f9d19a62b11f199d2525400287e28
    ReservedCode1: yge0r7sQw4RCE5uTImSoH3M/vGBuNouAnOlVnsN/kpMo49+SAeK1AkfEucoym4WDWwpb20g/z/6HRIpGUTdbp3vc1ejJio/z01FqKUVCEgx8abAbqPOTP9vZ+YF9y4nAzX271rHrUXf/Ja3xeG6ClgL46z5OBqy5I7E6vSDv5TIy7AttIKKWG3db8Dk=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 00744ee2872ebb6688a71f082191730f_804f9d19a62b11f199d2525400287e28
    ReservedCode2: yge0r7sQw4RCE5uTImSoH3M/vGBuNouAnOlVnsN/kpMo49+SAeK1AkfEucoym4WDWwpb20g/z/6HRIpGUTdbp3vc1ejJio/z01FqKUVCEgx8abAbqPOTP9vZ+YF9y4nAzX271rHrUXf/Ja3xeG6ClgL46z5OBqy5I7E6vSDv5TIy7AttIKKWG3db8Dk=
---

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
   git clone https://github.com/shangcanyang/astrbot_plugin_dcs_monitor.git
   ```

2. 重启 AstrBot，或在 WebUI 中启用插件。

3. 安装依赖（插件目录下的 `requirements.txt`）：
   ```bash
   pip install -r requirements.txt
   ```

## ⚙️ 配置（WebUI 网页版表单）

在 AstrBot WebUI 的插件管理页面，找到 `astrbot_plugin_dcs_monitor`，插件配置页会展示**区块化分组表单**，所有需修改的参数均可直接填写后保存，无需编辑任何文件。

配置界面分为以下区块（参考 E:\monitor 配置弹窗的区块化布局）：

### 1. 连接设置（connection）

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `api_base` | DCS API 基础地址 | `http://119.36.147.45:8041` |
| `client_id` | 客户端 ID（可选，默认 `ms-content-sample`） | `ms-content-sample` |

### 2. 登录凭据（credentials）

| 配置项 | 说明 |
|--------|------|
| `username` | 登录用户名 |
| `password` | 登录密码，**掩码输入（secret）**，不提供默认值 |

### 3. 监控设置（monitoring）

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `point_prefix` | 自动拼接到每个点位名称前面的固定前缀 | `system:LinkObject:serverdata1:system:` |
| `default_check_interval` | 全局默认检查间隔（秒） | `10` |

### 4. 监控点位列表（points，template_list 模板化编辑）

可视化模板列表编辑（官方 `template_list` 类型），可添加/删除多个"监控点位"模板条目，折叠列表中直接显示位号便于区分。每个点位包含：

| 字段 | 说明 | 是否必填 |
|------|------|---------|
| `name` | 位号，拼接 `point_prefix` 后为完整点 ID | 必填 |
| `description` | 点位中文描述，用于预警消息展示 | 选填（缺省用位号） |
| `low_threshold` | 低阈值，低于此值触发预警；**填 0 或留空不预警** | 选填 |
| `high_threshold` | 高阈值，高于此值触发预警；**填 0 或留空不预警** | 选填 |
| `check_interval` | 该点位独立检查间隔（秒），留空使用全局默认值 | 选填 |
| `enabled` | 是否启用该点位 | 选填（默认启用） |

保存后的配置结构中每个条目会带 `__template_key` 字段标识模板，插件加载时自动忽略该字段。

> 注意：`point_prefix` 与点位 `name` 拼接后必须为 DCS 系统中真实存在的点 ID。

### 🔐 凭据安全说明（v2.3.0+）

`username` / `password` 不提供明文默认值。凭据通过两种方式配置，**统一持久化到 AstrBot `data/config/<plugin>_config.json`**（官方规范：持久化数据存 data 目录，更新/重装插件不丢失）：

1. **网页版配置（推荐主渠道）**：在「登录凭据」区块直接填写 `username` / `password`（密码为掩码输入），保存后写入 AstrBot 配置。
2. **聊天指令（兜底渠道）**：使用 `/dcs_set` 指令，同样写入同一份配置并即时保存：

```
/dcs_set username <用户名>          # 设置登录用户名
/dcs_set password <密码>            # 设置登录密码
/dcs_set client_id <客户端ID>       # 设置客户端 ID（可选）
/dcs_set                            # 查看凭据状态（不显示密码明文）
/dcs_set remove <username|password|client_id>   # 清除某项
```

## 🤖 使用指令

所有指令通过发送聊天消息触发（需在已绑定预警的会话中发送 `/dcs_bind` 后）。

| 指令 | 说明 |
|------|------|
| `/dcs_set` | 设置/查看本地凭据（username / password / client_id），仅管理员可操作 |
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
*（内容由AI生成，仅供参考）*
