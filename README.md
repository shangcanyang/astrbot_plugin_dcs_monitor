---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 00744ee2872ebb6688a71f082191730f_3fa38e28a62e11f1891f525400f8a581
    ReservedCode1: 3A2hxYEkiDDD6QSB+ldX8mTJbBFl5psmQN4K8UPTKiC+aCQ6InNTxtu8kSa90bFjOyD7uGq163wfQOR+vvRO4VLu3/eGJFfU3XlpkPWHXT0MnGdWk6kmKf8VYWG89IWcsD1t+ye/3qSasBqNhrdEQdYhSaZnQG8RbRjAL6SrI8MMVrkTuuYbszF1L/A=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 00744ee2872ebb6688a71f082191730f_3fa38e28a62e11f1891f525400f8a581
    ReservedCode2: 3A2hxYEkiDDD6QSB+ldX8mTJbBFl5psmQN4K8UPTKiC+aCQ6InNTxtu8kSa90bFjOyD7uGq163wfQOR+vvRO4VLu3/eGJFfU3XlpkPWHXT0MnGdWk6kmKf8VYWG89IWcsD1t+ye/3qSasBqNhrdEQdYhSaZnQG8RbRjAL6SrI8MMVrkTuuYbszF1L/A=
---



# AstrBot DCS 监控与预警插件

在 AstrBot 中监控工业 DCS（分布式控制系统）实时数据点位的插件。支持异步登录、定时轮询、高/低阈值预警与恢复通知，可将预警消息推送到 QQ、微信等多个聊天会话。

## 功能特性

- 异步登录，自动处理 token 过期与重连（含强制踢出）
- 定时查询指定 DCS 点位的最新值，每个点位可独立配置检查间隔
- 高/低阈值预警，越限触发、恢复时通知
- 多会话绑定：预警消息可同时发送到多个群聊/私聊
- 完整生命周期管理：启动 / 停止 / 状态查询 / 即时查询
- 基于 `_conf_schema.json` 官方规范的 WebUI 区块化配置界面

## 安装与依赖

依赖：`Python 3.9+`、`aiohttp>=3.8.0`（见 `requirements.txt`）

1. 将插件克隆到 AstrBot 插件目录：

   ```bash
   cd AstrBot/data/plugins
   git clone https://github.com/shangcanyang/astrbot_plugin_dcs_monitor.git
   ```

2. 安装依赖：

   ```bash
   cd astrbot_plugin_dcs_monitor
   pip install -r requirements.txt
   ```

3. 重启 AstrBot，或在 WebUI 插件管理中启用插件。

## 配置（WebUI 网页版表单）

插件配置由 `_conf_schema.json` 自动生成 WebUI 表单，所有参数均在插件管理页填写保存，无需编辑任何文件。

**WebUI 配置步骤：**

1. 打开 AstrBot WebUI →「插件管理」→ 找到 `astrbot_plugin_dcs_monitor` →「配置」。
2. 按下方区块填写连接、凭据、监控设置与点位列表，密码为掩码输入。
3. 点击保存；凭据等数据持久化到 AstrBot `data/config` 目录。

配置分为 4 个区块：`connection` / `credentials` / `monitoring` / `points`。

### 1. 连接设置（connection）

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `api_base` | DCS API 基础地址（登录与点位数据查询） | `http://119.36.147.45:8041` |
| `client_id` | 登录接口的客户端 ID | `ms-content-sample` |

### 2. 登录凭据（credentials）

| 配置项 | 说明 |
|--------|------|
| `username` | 登录用户名 |
| `password` | 登录密码，**掩码输入（secret）**，不提供默认值 |

### 3. 监控设置（monitoring）

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `point_prefix` | 自动拼接到每个点位名称前面的固定前缀 | `system:LinkObject:serverdata1:system:` |
| `default_check_interval` | 全局默认检查间隔（秒），单个点位可覆盖 | `10` |

> 注意：`point_prefix` 与点位 `name` 拼接后必须为 DCS 系统中真实存在的点 ID。

### 4. 监控点位列表（points，template_list）

使用官方 `template_list` 模板化编辑，可添加/删除多个点位条目，列表中直接显示位号便于区分。每个点位字段：

| 字段 | 说明 | 是否必填 |
|------|------|---------|
| `name` | 位号，拼接 `point_prefix` 后为完整点 ID | 必填 |
| `description` | 点位描述，用于预警消息展示 | 选填（缺省用位号） |
| `low_threshold` | 低阈值，低于此值触发预警；**填 0 或留空不预警** | 选填 |
| `high_threshold` | 高阈值，高于此值触发预警；**填 0 或留空不预警** | 选填 |
| `check_interval` | 该点位独立检查间隔（秒），留空使用全局默认值 | 选填 |
| `enabled` | 是否启用该点位 | 选填（默认启用） |

保存后每个条目自动携带 `__template_key` 标识字段，插件加载时忽略。

## 凭据存储与安全

- `username` / `password` 不提供明文默认值。
- 凭据统一持久化到 AstrBot `data/config/<plugin>_config.json`（官方规范：持久化数据存 data 目录），更新/重装插件不丢失，且不会提交到 Git。
- 配置方式：
  1. **WebUI（推荐）**：在「登录凭据」区块填写保存，密码掩码输入。
  2. **聊天指令（兜底）**：通过 `/dcs_set` 写入同一份配置并即时保存。

## 使用指令

以下指令在已绑定预警的会话中发送（先执行 `/dcs_bind`）。共 7 个指令：

| 指令 | 说明 |
|------|------|
| `/dcs_set` | 设置/查看凭据（username / password / client_id），仅管理员可操作 |
| `/dcs_start` | 启动后台监控任务 |
| `/dcs_stop` | 停止监控任务 |
| `/dcs_status` | 查看监控状态、当前点位及阈值配置 |
| `/dcs_now` | 立即查询一次当前点位值 |
| `/dcs_bind` | 将当前聊天会话绑定为预警接收目标 |
| `/dcs_unbind` | 解除当前会话的预警绑定 |

`/dcs_set` 详细用法：

```
/dcs_set username <用户名>          # 设置登录用户名
/dcs_set password <密码>            # 设置登录密码
/dcs_set client_id <客户端ID>       # 设置客户端 ID（可选）
/dcs_set                            # 查看凭据状态（不显示密码明文）
/dcs_set remove <username|password|client_id>   # 清除某项
```

## 预警流程

1. 插件启动后自动登录 DCS 系统。
2. 按各点位 `check_interval` 定期获取最新值。
3. 超过配置的阈值时：
   - 首次越限 → 推送预警消息到所有绑定会话；
   - 状态恢复正常 → 推送恢复通知。
4. token 过期自动重新登录，无需人工干预。

## 开发与调试

- 基于 AstrBot 3.x/4.x 的 `Star` 模型开发，完全异步。
- 日志输出使用 `astrbot.api.logger`。
- 修改代码后可在 WebUI 点击「重载插件」生效，无需重启机器人。

## 版本记录

| 版本 | 说明 |
|------|------|
| v2.4.0 | 舍弃全部旧版兼容逻辑，仅保留官方规范最新结构（分组配置 + template_list 点位） |
| v2.3.0 | 按官方文档升级：points 改为 template_list、凭据迁移至 data/config、版本对齐 |
| v2.2.0 | 凭据安全修复：移除明文默认值，凭据本地化存储 |

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request。
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
