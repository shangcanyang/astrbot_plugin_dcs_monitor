#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Union, List, Dict, Any

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register(
    "astrbot_plugin_dcs_monitor",
    "YourName",
    "DCS 多点位数据监控与预警插件",
    "2.0.0",
    "https://github.com/yourname/astrbot_plugin_dcs_monitor",
)
class DCSMonitor(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 安全获取配置
        raw_config = self.context.get_config()
        if not isinstance(raw_config, dict):
            logger.error(f"配置格式错误，预期 dict，实际为 {type(raw_config)}，内容: {raw_config}")
            raw_config = {}

        self.api_base = raw_config.get("api_base", "http://119.36.147.45:8041")
        self.username = raw_config.get("username", "")
        self.password = raw_config.get("password", "")
        self.client_id = raw_config.get("client_id", "ms-content-sample")
        self.global_prefix = raw_config.get("point_prefix", "system:LinkObject:serverdata1:system:")
        self.default_check_interval = raw_config.get("default_check_interval", 10)

        # 解析点位列表
        self.points: List[Dict[str, Any]] = []
        points_config = raw_config.get("points", [])
        for pt in points_config:
            if not pt.get("enabled", True):
                continue
            point_id = pt.get("point_id")
            if not point_id:
                point_id = self.global_prefix + pt.get("name", "")
            point_info = {
                "name": pt["name"],
                "point_id": point_id,
                "low_threshold": pt.get("low_threshold"),
                "high_threshold": pt.get("high_threshold"),
                "check_interval": pt.get("check_interval", self.default_check_interval),
                "last_alert_state": "normal"  # normal, low, high
            }
            self.points.append(point_info)

        if not self.points:
            logger.warning("未配置任何启用的监控点位，插件将不会进行监控。")

        # 运行时状态
        self.ticket: Optional[str] = None
        self.monitor_task: Optional[asyncio.Task] = None
        self.running = False
        self.alert_targets: set = set()

    async def terminate(self):
        """插件卸载时停止监控任务"""
        if self.monitor_task:
            self.running = False
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("DCS监控插件已停止")

    # ------------------- API 交互 -------------------
    async def login(self) -> Optional[str]:
        """登录并获取 ticket"""
        login_url = f"{self.api_base}/inter-api/auth/login"
        payload = {
            "userName": self.username,
            "password": self.password,
            "clientId": self.client_id,
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(login_url, json=payload, headers=headers, timeout=10) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    if data.get("needForceLogin") is True:
                        logger.info("检测到 needForceLogin，强制踢出...")
                        payload["forceLogin"] = True
                        async with session.post(login_url, json=payload, headers=headers, timeout=10) as resp2:
                            resp2.raise_for_status()
                            data = await resp2.json()
                    ticket = data.get("ticket")
                    if ticket:
                        logger.info(f"登录成功，ticket: {ticket[:10]}...")
                        return ticket
                    else:
                        logger.error(f"登录响应无 ticket: {data}")
                        return None
        except Exception as e:
            logger.error(f"登录失败: {e}")
            return None

    @staticmethod
    def _utc_to_beijing(utc_str: str) -> str:
        utc_dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return utc_dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

    async def fetch_latest(self, ticket: str, point_id: str) -> Union[Tuple[str, float], str, None]:
        """
        返回：
            (北京时间, 数值)  正常数据
            "TOKEN_EXPIRED"  401
            "BAD_REQUEST"    400
            None              其他错误
        """
        now = datetime.now(timezone.utc)
        max_date = now.isoformat(timespec='seconds').replace('+00:00', 'Z')
        min_date = (now - timedelta(seconds=30)).isoformat(timespec='seconds').replace('+00:00', 'Z')

        payload = {
            "list": [{
                "dataSource": point_id,
                "filters": {
                    "minDate": min_date,
                    "maxDate": max_date,
                    "aggrType": "first",
                    "group": "time(1s,0s) fill(previous)",
                    "isHistory": True,
                    "limit": 601
                },
                "type": "instance.property"
            }]
        }
        headers = {
            "Authorization": f"Bearer {ticket}",
            "Cookie": f"suposTicket={ticket}; suposTicketForFrontend={ticket}",
            "Content-Type": "application/json; charset=UTF-8",
            "VALUE-TO-STRING": "true",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base}/api/compose/manage/v3/objectselector/objectdata/batchQuery",
                    headers=headers,
                    json=payload,
                    timeout=10
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
                        return (self._utc_to_beijing(last["time"]), last["first"])
                    else:
                        logger.warning(f"点位 {point_id} 响应中无数据: {data}")
                        return None
        except Exception as e:
            logger.error(f"请求异常 点位 {point_id}: {e}")
            return None

    async def send_alert(self, message: str):
        """向所有绑定的会话发送预警消息"""
        if not self.alert_targets:
            logger.warning("没有绑定预警目标，消息未发送: " + message)
            return
        for session_id in self.alert_targets:
            try:
                await self.context.send_message(session_id, message)
            except Exception as e:
                logger.error(f"向 {session_id} 发送预警失败: {e}")

    async def check_and_alert_for_point(self, point: Dict, value: float):
        """根据点位阈值判断并发送预警"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        low = point.get("low_threshold")
        high = point.get("high_threshold")
        name = point["name"]
        state = point["last_alert_state"]

        if low is not None and value < low:
            if state != "low":
                msg = f"⚠️ DCS 低阈值预警 [{now_str}]\n点位: {name}\n当前值: {value}\n低于阈值: {low}"
                await self.send_alert(msg)
                point["last_alert_state"] = "low"
        elif high is not None and value > high:
            if state != "high":
                msg = f"⚠️ DCS 高阈值预警 [{now_str}]\n点位: {name}\n当前值: {value}\n高于阈值: {high}"
                await self.send_alert(msg)
                point["last_alert_state"] = "high"
        else:
            if state != "normal" and (low is not None or high is not None):
                msg = f"✅ DCS 状态恢复正常 [{now_str}]\n点位: {name}\n当前值: {value}"
                await self.send_alert(msg)
                point["last_alert_state"] = "normal"

    async def monitoring_loop(self):
        """多点位监控主循环"""
        logger.info("DCS 多点位监控循环启动")
        while self.running:
            if not self.ticket:
                logger.info("尝试登录获取 ticket...")
                self.ticket = await self.login()
                if not self.ticket:
                    logger.error("登录失败，10秒后重试")
                    await asyncio.sleep(10)
                    continue

            # 计算本次循环需要等待的最短间隔
            min_interval = 10
            token_expired = False

            for point in self.points:
                result = await self.fetch_latest(self.ticket, point["point_id"])
                if result == "TOKEN_EXPIRED":
                    logger.warning("Ticket 已失效，重新登录")
                    self.ticket = None
                    token_expired = True
                    break
                elif result == "BAD_REQUEST":
                    logger.error(f"点位 {point['name']} 请求格式错误，跳过本次")
                    continue
                elif result is None:
                    logger.warning(f"点位 {point['name']} 获取数据失败")
                    continue
                else:
                    bj_time, value = result
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"[{current_time}] {point['name']}: {value} @ {bj_time}")
                    await self.check_and_alert_for_point(point, value)

                # 更新最小间隔
                interval = point.get("check_interval", self.default_check_interval)
                if interval < min_interval:
                    min_interval = interval

            if token_expired:
                continue  # 重新登录后继续循环

            await asyncio.sleep(min_interval)

    # ------------------- 指令 -------------------
    @filter.command("dcs_start")
    async def start_monitor(self, event: AstrMessageEvent):
        """启动 DCS 监控"""
        if self.running:
            yield event.plain_result("DCS 监控已经在运行中")
            return
        if not self.points:
            yield event.plain_result("⚠️ 未配置任何监控点位，请先在插件配置中添加 points 列表。")
            return
        self.running = True
        self.monitor_task = asyncio.create_task(self.monitoring_loop())
        yield event.plain_result(f"✅ DCS 多点位监控已启动，共监控 {len(self.points)} 个点位")

    @filter.command("dcs_stop")
    async def stop_monitor(self, event: AstrMessageEvent):
        """停止 DCS 监控"""
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
        """查看监控状态"""
        status_text = "运行中" if self.running else "已停止"
        msg = f"DCS 监控状态: {status_text}\n"
        if not self.points:
            msg += "未配置任何监控点位。"
        else:
            msg += f"监控点位数量: {len(self.points)}\n"
            for pt in self.points:
                msg += f"\n📊 {pt['name']}\n  - 点ID: {pt['point_id']}\n  - 间隔: {pt['check_interval']}秒"
                if pt.get('low_threshold') is not None:
                    msg += f"\n  - 低阈值: {pt['low_threshold']}"
                if pt.get('high_threshold') is not None:
                    msg += f"\n  - 高阈值: {pt['high_threshold']}"
                msg += f"\n  - 状态: {pt.get('last_alert_state', 'normal')}"
        msg += f"\n\n当前预警会话数: {len(self.alert_targets)}"
        yield event.plain_result(msg)

    @filter.command("dcs_bind")
    async def bind_target(self, event: AstrMessageEvent):
        """将当前会话绑定为预警目标"""
        session_id = event.get_session_id()
        self.alert_targets.add(session_id)
        yield event.plain_result(f"✅ 当前会话已绑定预警目标 (session: {session_id})")

    @filter.command("dcs_unbind")
    async def unbind_target(self, event: AstrMessageEvent):
        """解除当前会话的预警绑定"""
        session_id = event.get_session_id()
        if session_id in self.alert_targets:
            self.alert_targets.remove(session_id)
            yield event.plain_result("❌ 已解除当前会话的预警绑定")
        else:
            yield event.plain_result("当前会话并未绑定预警")

    @filter.command("dcs_now")
    async def query_now(self, event: AstrMessageEvent):
        """立即查询所有点位当前值"""
        if not self.ticket:
            yield event.plain_result("尚未登录，请先使用 /dcs_start 启动监控")
            return
        if not self.points:
            yield event.plain_result("未配置任何监控点位")
            return

        results = []
        for point in self.points:
            result = await self.fetch_latest(self.ticket, point["point_id"])
            if result == "TOKEN_EXPIRED":
                yield event.plain_result("登录已过期，请重新启动监控")
                return
            elif result == "BAD_REQUEST":
                results.append(f"❌ {point['name']}: 请求格式错误")
            elif result is None:
                results.append(f"❌ {point['name']}: 获取数据失败")
            else:
                bj_time, value = result
                results.append(f"✅ {point['name']}: {value} @ {bj_time}")

        msg = "📊 当前点位值:\n" + "\n".join(results)
        yield event.plain_result(msg)