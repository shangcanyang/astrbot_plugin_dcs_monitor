#!/usr/bin/env python3

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.web import error_response, json_response, request

PLUGIN_NAME = "astrbot_plugin_dcs_monitor"


@register(
    PLUGIN_NAME,
    "canyang",
    "DCS 多点位监控（简单模式：位号+描述+上下限）",
    "2.6.0",
    "https://github.com/shangcanyang/astrbot_plugin_dcs_monitor",
)
class DCSMonitor(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        if config is None:
            config = self.context.get_config()
        if not isinstance(config, dict):
            logger.error(f"配置格式错误，预期 dict，实际为 {type(config)}")
            config = {}
        self.config = config

        # ---- 分组配置（官方 _conf_schema.json 区块化结构） ----
        conn = config.get("connection", {})
        cred = config.get("credentials", {})
        mon = config.get("monitoring", {})
        self.api_base = conn.get("api_base", "http://119.36.147.45:8041")
        self.client_id = conn.get("client_id", "ms-content-sample")
        self.username = cred.get("username", "")
        self.password = cred.get("password", "")
        self.global_prefix = mon.get(
            "point_prefix", "system:LinkObject:serverdata1:system:"
        )
        self.default_check_interval = mon.get("default_check_interval", 10)

        # ---- 点位列表（template_list） ----
        self.points = self._parse_points(
            config.get("points", []), self.global_prefix, self.default_check_interval
        )
        if not self.points:
            logger.warning("未配置任何启用的监控点位，插件将不会进行监控。")

        # 运行时状态
        self.ticket: str | None = None
        self.monitor_task: asyncio.Task | None = None
        self.running = False
        # 登录互斥锁：避免多个点位任务并发触发重复登录
        self._login_lock = asyncio.Lock()
        self.point_tasks: dict[str, asyncio.Task] = {}
        # 预警目标从持久化配置恢复，避免插件重载/机器人重启后丢失
        self.alert_targets: set = set(config.get("alert_targets", []) or [])

        # 插件 Pages：WebUI 位号管理页后端 API
        self._register_web_apis()

    # ------------------- 点位解析 -------------------
    @staticmethod
    def _to_threshold(value) -> float | None:
        """template_list 的 float 字段留空时默认保存为 0.0，统一转为 None（不预警）。"""
        if not value:
            return None
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if v != 0 else None

    @classmethod
    def _parse_points(
        cls, points_config: list[Any], prefix: str, default_interval: int
    ) -> list[dict[str, Any]]:
        """解析 template_list 点位（每条带 __template_key 字段，解析时忽略之）。"""
        points = []
        for pt in points_config:
            if (
                not isinstance(pt, dict)
                or not pt.get("name")
                or not pt.get("enabled", True)
            ):
                logger.warning(f"忽略无效点位配置: {pt}")
                continue
            points.append(
                {
                    "name": pt["name"],
                    "description": pt.get("description", pt["name"]),
                    "point_id": prefix + pt["name"],
                    "low_threshold": cls._to_threshold(pt.get("low_threshold")),
                    "high_threshold": cls._to_threshold(pt.get("high_threshold")),
                    "check_interval": pt.get("check_interval") or default_interval,
                    "enabled": True,
                    "last_alert_state": "normal",
                }
            )
        return points

    # ------------------- 凭据存储（data/config 目录） -------------------
    def _save_credentials(self, updates: dict[str, str]) -> bool:
        """合并更新凭据并通过 AstrBotConfig.save_config() 持久化到 data/config。"""
        try:
            creds = dict(self.config.get("credentials") or {})
            for k, v in updates.items():
                if v is None:
                    creds.pop(k, None)
                else:
                    creds[k] = str(v)
            self.config["credentials"] = creds
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
            else:
                logger.warning("当前配置对象不支持 save_config()，凭据仅保存在内存中")
            return True
        except Exception as e:
            logger.error(f"保存凭据失败: {e}")
            return False

    async def terminate(self):
        self.running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            self.monitor_task = None
        for task in self.point_tasks.values():
            if not task.done():
                task.cancel()
        self.point_tasks.clear()
        logger.info("DCS监控插件已停止")

    # ------------------- API 交互 -------------------
    async def login(self) -> str | None:
        login_url = f"{self.api_base}/inter-api/auth/login"
        payload = {
            "userName": self.username,
            "password": self.password,
            "clientId": self.client_id,
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    login_url, json=payload, headers=headers, timeout=10
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    if data.get("needForceLogin") is True:
                        logger.info("检测到 needForceLogin，强制踢出...")
                        payload["forceLogin"] = True
                        async with session.post(
                            login_url, json=payload, headers=headers, timeout=10
                        ) as resp2:
                            resp2.raise_for_status()
                            data = await resp2.json()
                    ticket = data.get("ticket")
                    if ticket:
                        logger.info(f"登录成功，ticket: {ticket[:10]}...")
                        return ticket
                    logger.error(f"登录响应无 ticket: {data}")
                    return None
        except Exception as e:
            logger.error(f"登录失败: {e}")
            return None

    @staticmethod
    def _utc_to_beijing(utc_str: str) -> str:
        utc_dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return utc_dt.astimezone(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    async def fetch_latest(
        self, ticket: str, point_id: str
    ) -> tuple[str, float] | str | None:
        now = datetime.now(timezone.utc)
        max_date = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        min_date = (
            (now - timedelta(seconds=30))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        payload = {
            "list": [
                {
                    "dataSource": point_id,
                    "filters": {
                        "minDate": min_date,
                        "maxDate": max_date,
                        "aggrType": "first",
                        "group": "time(1s,0s) fill(previous)",
                        "isHistory": True,
                        "limit": 601,
                    },
                    "type": "instance.property",
                }
            ]
        }
        headers = {
            "Authorization": f"Bearer {ticket}",
            "Cookie": f"suposTicket={ticket}; suposTicketForFrontend={ticket}",
            "Content-Type": "application/json; charset=UTF-8",
            "VALUE-TO-STRING": "true",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base}/api/compose/manage/v3/objectselector/objectdata/batchQuery",
                    headers=headers,
                    json=payload,
                    timeout=10,
                ) as resp:
                    if resp.status == 401:
                        return "TOKEN_EXPIRED"
                    if resp.status == 400:
                        text = await resp.text()
                        logger.error(f"请求格式错误(400) 点位 {point_id}: {text[:500]}")
                        return "BAD_REQUEST"
                    resp.raise_for_status()
                    data = await resp.json()
                    point_data = data.get(point_id)
                    if point_data and point_data.get("list"):
                        last = point_data["list"][-1]
                        return (
                            self._utc_to_beijing(last["time"]),
                            float(last["first"]),
                        )
                    logger.warning(f"点位 {point_id} 响应中无数据: {data}")
                    return None
        except Exception as e:
            logger.error(f"请求异常 点位 {point_id}: {e}")
            return None

    async def send_alert(self, message: str):
        if not self.alert_targets:
            logger.warning("没有绑定预警目标，消息未发送: " + message)
            return
        for session_id in self.alert_targets:
            try:
                await self.context.send_message(session_id, message)
            except Exception as e:
                logger.error(f"向 {session_id} 发送预警失败: {e}")

    async def check_and_alert_for_point(self, point: dict, value: float):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        display_name = point.get("description", point["name"])
        low = point.get("low_threshold")
        high = point.get("high_threshold")
        state = point["last_alert_state"]
        if low is not None and value < low:
            if state != "low":
                await self.send_alert(
                    f"⚠️ DCS 低阈值预警 [{now_str}]\n点位: {display_name}\n当前值: {value}\n低于阈值: {low}"
                )
                point["last_alert_state"] = "low"
        elif high is not None and value > high:
            if state != "high":
                await self.send_alert(
                    f"⚠️ DCS 高阈值预警 [{now_str}]\n点位: {display_name}\n当前值: {value}\n高于阈值: {high}"
                )
                point["last_alert_state"] = "high"
        else:
            if state != "normal" and (low is not None or high is not None):
                await self.send_alert(
                    f"✅ DCS 状态恢复正常 [{now_str}]\n点位: {display_name}\n当前值: {value}"
                )
                point["last_alert_state"] = "normal"

    # ------------------- 预警目标持久化（data/config 目录） -------------------
    def _save_alert_targets(self) -> bool:
        """将预警目标会话列表持久化到插件配置（AstrBot data/config 目录）。"""
        try:
            self.config["alert_targets"] = sorted(self.alert_targets)
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
            else:
                logger.warning(
                    "当前配置对象不支持 save_config()，预警目标仅保存在内存中"
                )
            return True
        except Exception as e:
            logger.error(f"保存预警目标失败: {e}")
            return False

    # ------------------- 独立点位轮询（每点位按自身 check_interval 调度） -------------------
    async def _ensure_ticket(self) -> str | None:
        """获取有效 ticket；无 ticket 时加锁登录，避免多个点位任务并发触发重复登录。"""
        if self.ticket:
            return self.ticket
        async with self._login_lock:
            if self.ticket:
                return self.ticket
            self.ticket = await self.login()
            return self.ticket

    async def _point_loop(self, point: dict):
        """单个点位的独立轮询循环，按点位自己的 check_interval 调度，互不影响。"""
        while self.running:
            ticket = await self._ensure_ticket()
            if not ticket:
                logger.error("登录失败，10秒后重试")
                await asyncio.sleep(10)
                continue

            result = await self.fetch_latest(ticket, point["point_id"])
            if result == "TOKEN_EXPIRED":
                logger.warning(f"Ticket 已失效，重新登录 (点位 {point['name']})")
                self.ticket = None
            elif result == "BAD_REQUEST":
                logger.error(f"点位 {point['name']} 请求格式错误，跳过本次")
            elif result is None:
                logger.warning(f"点位 {point['name']} 获取数据失败")
            else:
                bj_time, value = result
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.info(f"[{current_time}] {point['name']}: {value} @ {bj_time}")
                await self.check_and_alert_for_point(point, value)

            # 轮询间隔不低于 10 秒，避免对 DCS 服务造成过大压力
            await asyncio.sleep(max(point["check_interval"], 10))

    async def _monitor_supervisor(self):
        """监控总控：为每个点位创建独立轮询任务并保持存活；任一异常退出时记录并继续。"""
        logger.info("DCS 多点位监控启动（各点位独立调度）")
        # 用局部变量持有本轮任务集合，避免点位热更新重启时与旧任务的取消逻辑相互干扰
        tasks = {
            p["name"]: asyncio.create_task(self._point_loop(p)) for p in self.points
        }
        self.point_tasks = tasks
        try:
            done, pending = await asyncio.wait(
                tasks.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"点位轮询任务异常退出: {e}")
            # 有任务异常退出时，取消其余任务，避免残留后台任务
            for task in pending:
                task.cancel()
        except asyncio.CancelledError:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            raise

    # ------------------- 插件 Pages（WebUI 位号管理页） -------------------
    def _register_web_apis(self):
        """注册插件 Pages 后端 API：WebUI 插件详情页可打开「位号管理」页面。"""
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/points",
            self.api_list_points,
            ["GET"],
            "获取监控点位列表",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/points",
            self.api_add_point,
            ["POST"],
            "新增监控点位",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/points/update",
            self.api_update_point,
            ["POST"],
            "更新监控点位",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/points/delete",
            self.api_delete_point,
            ["POST"],
            "删除监控点位",
        )

    def _save_points_config(self) -> bool:
        """将运行时点位列表写回插件配置并持久化（AstrBot data/config 目录）。"""
        try:
            cfg_points = []
            for pt in self.points:
                cfg_points.append(
                    {
                        "name": pt["name"],
                        "description": pt.get("description", pt["name"]),
                        "low_threshold": pt.get("low_threshold") or 0.0,
                        "high_threshold": pt.get("high_threshold") or 0.0,
                        "check_interval": pt.get("check_interval")
                        or self.default_check_interval,
                        "enabled": pt.get("enabled", True),
                    }
                )
            self.config["points"] = cfg_points
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
            else:
                logger.warning("当前配置对象不支持 save_config()，点位仅保存在内存中")
            return True
        except Exception as e:
            logger.error(f"保存点位配置失败: {e}")
            return False

    def _restart_monitor_if_running(self):
        """监控运行中时，点位配置变更后重启监控任务使新配置立即生效。"""
        if not self.running:
            return
        old_task = self.monitor_task
        self.monitor_task = None
        if old_task and not old_task.done():
            old_task.cancel()
        self.monitor_task = asyncio.create_task(self._monitor_supervisor())
        logger.info("点位配置已变更，监控任务已重启以应用新配置")

    def _validate_point_payload(self, payload: dict) -> dict | str:
        """校验页面提交的点位数据，返回解析后的点位 dict 或错误信息字符串。"""
        name = str(payload.get("name", "")).strip()
        if not name:
            return "位号(name)不能为空"
        description = str(payload.get("description", "")).strip() or name
        low = self._to_threshold(payload.get("low_threshold"))
        high = self._to_threshold(payload.get("high_threshold"))
        interval = payload.get("check_interval") or self.default_check_interval
        try:
            interval = max(int(interval), 10)
        except (TypeError, ValueError):
            return "检查间隔必须是数字（秒）"
        return {
            "name": name,
            "description": description,
            "point_id": self.global_prefix + name,
            "low_threshold": low,
            "high_threshold": high,
            "check_interval": interval,
            "enabled": True,
            "last_alert_state": "normal",
        }

    async def api_list_points(self):
        """返回点位列表及前缀信息，供页面初始化渲染。"""
        return json_response(
            {
                "prefix": self.global_prefix,
                "default_interval": self.default_check_interval,
                "running": self.running,
                "points": [
                    {
                        "name": p["name"],
                        "description": p["description"],
                        "point_id": p["point_id"],
                        "low_threshold": p.get("low_threshold"),
                        "high_threshold": p.get("high_threshold"),
                        "check_interval": p.get("check_interval"),
                        "enabled": p.get("enabled", True),
                        "last_alert_state": p.get("last_alert_state", "normal"),
                    }
                    for p in self.points
                ],
            }
        )

    async def api_add_point(self):
        """新增一个监控点位。"""
        payload = await request.json(default={})
        point = self._validate_point_payload(payload)
        if isinstance(point, str):
            return error_response(point, status_code=400)
        if any(p["name"] == point["name"] for p in self.points):
            return error_response(f"点位 {point['name']} 已存在", status_code=400)
        self.points.append(point)
        self._save_points_config()
        self._restart_monitor_if_running()
        logger.info(f"WebUI 新增点位: {point['name']} (完整点 ID: {point['point_id']})")
        return json_response({"ok": True, "name": point["name"]})

    async def api_update_point(self):
        """更新一个监控点位（old_name 定位原点位，data 为新数据）。"""
        payload = await request.json(default={})
        old_name = str(payload.get("old_name", "")).strip()
        point = self._validate_point_payload(payload.get("data") or {})
        if isinstance(point, str):
            return error_response(point, status_code=400)
        for i, p in enumerate(self.points):
            if p["name"] == old_name:
                if point["name"] != old_name and any(
                    q["name"] == point["name"] for q in self.points
                ):
                    return error_response(
                        f"点位 {point['name']} 已存在", status_code=400
                    )
                self.points[i] = point
                self._save_points_config()
                self._restart_monitor_if_running()
                logger.info(f"WebUI 更新点位: {old_name} -> {point['name']}")
                return json_response({"ok": True, "name": point["name"]})
        return error_response(f"点位 {old_name} 不存在", status_code=404)

    async def api_delete_point(self):
        """删除一个监控点位。"""
        payload = await request.json(default={})
        name = str(payload.get("name", "")).strip()
        for i, p in enumerate(self.points):
            if p["name"] == name:
                del self.points[i]
                self._save_points_config()
                self._restart_monitor_if_running()
                logger.info(f"WebUI 删除点位: {name}")
                return json_response({"ok": True})
        return error_response(f"点位 {name} 不存在", status_code=404)

    # ------------------- 指令 -------------------
    @filter.command("dcs_start")
    async def start_monitor(self, event: AstrMessageEvent):
        if self.running:
            yield event.plain_result("DCS 监控已经在运行中")
            return
        if not self.points:
            yield event.plain_result(
                "⚠️ 未配置任何监控点位，请先在插件配置中添加 points 列表。"
            )
            return
        self.running = True
        self.monitor_task = asyncio.create_task(self._monitor_supervisor())
        yield event.plain_result(
            f"✅ DCS 多点位监控已启动，共监控 {len(self.points)} 个点位（各点位独立调度）"
        )

    @filter.command("dcs_stop")
    async def stop_monitor(self, event: AstrMessageEvent):
        if not self.running:
            yield event.plain_result("DCS 监控未运行")
            return
        self.running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            self.monitor_task = None
        yield event.plain_result("🛑 DCS 监控已停止")

    @filter.command("dcs_status")
    async def status(self, event: AstrMessageEvent):
        status_text = "运行中" if self.running else "已停止"
        msg = f"DCS 监控状态: {status_text}\n"
        if not self.points:
            msg += "未配置任何监控点位。"
        else:
            msg += f"监控点位数量: {len(self.points)}\n"
            for pt in self.points:
                display = pt.get("description", pt["name"])
                msg += f"\n📊 {display} ({pt['name']})\n  - 点ID: {pt['point_id']}\n  - 间隔: {pt['check_interval']}秒"
                if pt.get("low_threshold") is not None:
                    msg += f"\n  - 低阈值: {pt['low_threshold']}"
                if pt.get("high_threshold") is not None:
                    msg += f"\n  - 高阈值: {pt['high_threshold']}"
                msg += f"\n  - 状态: {pt.get('last_alert_state', 'normal')}"
        msg += f"\n\n当前预警会话数: {len(self.alert_targets)}"
        yield event.plain_result(msg)

    @filter.command("dcs_bind")
    async def bind_target(self, event: AstrMessageEvent):
        session_id = event.get_session_id()
        self.alert_targets.add(session_id)
        self._save_alert_targets()
        yield event.plain_result(f"✅ 当前会话已绑定预警目标 (session: {session_id})")

    @filter.command("dcs_unbind")
    async def unbind_target(self, event: AstrMessageEvent):
        session_id = event.get_session_id()
        if session_id in self.alert_targets:
            self.alert_targets.remove(session_id)
            self._save_alert_targets()
            yield event.plain_result("❌ 已解除当前会话的预警绑定")
        else:
            yield event.plain_result("当前会话并未绑定预警")

    @filter.command("dcs_set")
    async def set_credential(self, event: AstrMessageEvent):
        """设置/查看凭据（保存到 AstrBot data/config 目录）"""
        try:
            if not event.is_admin():
                yield event.plain_result("❌ 仅管理员可设置 DCS 凭据")
                return
        except AttributeError:
            # 平台适配器未实现管理员判定时，放行并记录提示
            logger.debug("当前平台不支持 is_admin 判定，跳过权限检查")
        except Exception as e:
            logger.warning(f"权限检查异常，按非管理员处理: {e}")
            yield event.plain_result("❌ 权限检查失败，无法设置 DCS 凭据")
            return

        raw = event.message_str.strip()
        rest = raw.split(None, 1)[1].strip() if " " in raw else ""
        if not rest:
            msg = "DCS 凭据状态（保存于 AstrBot data/config 目录）:\n"
            msg += f"username: {'已设置' if self.username else '未设置'}\n"
            msg += f"client_id: {self.client_id or '未设置(默认 ms-content-sample)'}\n"
            msg += f"password: {'已设置' if self.password else '未设置'}\n"
            msg += "\n用法:\n"
            msg += "/dcs_set username <用户名>\n"
            msg += "/dcs_set password <密码>\n"
            msg += "/dcs_set client_id <客户端ID>\n"
            msg += "/dcs_set remove <username|password|client_id>   # 清除某项\n"
            msg += "\n提示：凭据保存于 AstrBot data/config 目录，更新/重装插件不丢失；不提交到 Git。"
            yield event.plain_result(msg)
            return

        sub, _, value = rest.partition(" ")
        sub = sub.strip().lower()
        value = value.strip()

        if sub == "remove":
            key = value.lower().strip()
            if key not in ("username", "password", "client_id"):
                yield event.plain_result(
                    "❌ 仅支持清除 username / password / client_id"
                )
                return
            if not self._save_credentials({key: None}):
                yield event.plain_result("❌ 清除凭据失败，请查看日志")
                return
            setattr(self, key, "" if key != "client_id" else "ms-content-sample")
            yield event.plain_result(f"✅ 已清除凭据: {key}")
            return

        if sub not in ("username", "password", "client_id") or not value:
            yield event.plain_result(f"❌ 用法: /dcs_set {sub} <值>")
            return
        if not self._save_credentials({sub: value}):
            yield event.plain_result("❌ 保存凭据失败，请查看日志")
            return
        setattr(self, sub, value)
        if sub == "password":
            yield event.plain_result(
                "✅ 已设置 DCS 密码并保存到 AstrBot data/config 目录"
            )
        else:
            yield event.plain_result(
                f"✅ 已设置 {sub} 并保存到 AstrBot data/config 目录"
            )

    @filter.command("dcs_now")
    async def query_now(self, event: AstrMessageEvent):
        if not self.ticket:
            yield event.plain_result("尚未登录，请先使用 /dcs_start 启动监控")
            return
        if not self.points:
            yield event.plain_result("未配置任何监控点位")
            return
        results = []
        for point in self.points:
            display = point.get("description", point["name"])
            result = await self.fetch_latest(self.ticket, point["point_id"])
            if result == "TOKEN_EXPIRED":
                yield event.plain_result("登录已过期，请重新启动监控")
                return
            elif result == "BAD_REQUEST":
                results.append(f"❌ {display}: 请求格式错误")
            elif result is None:
                results.append(f"❌ {display}: 获取数据失败")
            else:
                bj_time, value = result
                results.append(f"✅ {display}: {value} @ {bj_time}")
        yield event.plain_result("📊 当前点位值:\n" + "\n".join(results))
