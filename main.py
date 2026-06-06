#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Union

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# ---------- 插件注册 ----------
@register(
    "astrbot_plugin_dcs_monitor",
    "YourName",
    "DCS 数据监控与预警插件",
    "1.0.0",
    "https://github.com/yourname/astrbot_plugin_dcs_monitor",
)
class DCSMonitor(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 读取配置（来自 _conf_schema.json）
        self.config = context.get_config()
        self.api_base = self.config.get("api_base", "http://119.36.147.45:8041")
        self.username = self.config["username"]
        self.password = self.config["password"]
        self.client_id = self.config.get("client_id", "ms-content-sample")
        self.point_prefix = self.config.get("point_prefix", "system:LinkObject:serverdata1:system:")
        self.point_name = self.config["point_name"]
        self.point_id = self.point_prefix + self.point_name
        self.check_interval = self.config.get("check_interval", 10)     # 秒
        self.low_threshold = self.config.get("low_threshold", None)     # 低阈值
        self.high_threshold = self.config.get("high_threshold", None)   # 高阈值

        # 运行时状态
        self.ticket: Optional[str] = None
        self.monitor_task: Optional[asyncio.Task] = None
        self.running = False
        self.alert_targets: set = set()          # 存储需要接收预警的会话ID

        # 辅助变量（避免重复报警）
        self.last_alert_state = "normal"   # normal, low, high

    # ---------- 生命周期 ----------
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

    # ---------- API 交互（异步）----------
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

    async def fetch_latest(self, ticket: str) -> Union[Tuple[str, float], str, None]:
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
                "dataSource": self.point_id,
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
                        logger.error(f"请求格式错误(400): {text[:500]}")
                        return "BAD_REQUEST"
                    resp.raise_for_status()
                    data = await resp.json()
                    point_data = data.get(self.point_id)
                    if point_data and point_data.get("list"):
                        last = point_data["list"][-1]
                        return (self._utc_to_beijing(last["time"]), last["first"])
                    else:
                        logger.warning(f"响应中无数据: {data}")
                        return None
        except Exception as e:
            logger.error(f"请求异常: {e}")
            return None

    async def send_alert(self, message: str):
        """向所有绑定的会话发送预警消息"""
        if not self.alert_targets:
            logger.warning("没有绑定预警目标，消息未发送: " + message)
            return
        for session_id in self.alert_targets:
            try:
                # 使用 AstrBot 的通用消息发送接口
                await self.context.send_message(session_id, message)
            except Exception as e:
                logger.error(f"向 {session_id} 发送预警失败: {e}")

    async def check_and_alert(self, value: float):
        """根据阈值判断并发送预警"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self.low_threshold is not None and value < self.low_threshold:
            if self.last_alert_state != "low":
                msg = f"⚠️ DCS 低阈值预警 [{now_str}]\n点位: {self.point_name}\n当前值: {value}\n低于阈值: {self.low_threshold}"
                await self.send_alert(msg)
                self.last_alert_state = "low"
        elif self.high_threshold is not None and value > self.high_threshold:
            if self.last_alert_state != "high":
                msg = f"⚠️ DCS 高阈值预警 [{now_str}]\n点位: {self.point_name}\n当前值: {value}\n高于阈值: {self.high_threshold}"
                await self.send_alert(msg)
                self.last_alert_state = "high"
        else:
            if self.last_alert_state != "normal":
                msg = f"✅ DCS 状态恢复正常 [{now_str}]\n点位: {self.point_name}\n当前值: {value}"
                await self.send_alert(msg)
                self.last_alert_state = "normal"

    async def monitoring_loop(self):
        """监控主循环"""
        logger.info("DCS 监控循环启动")
        while self.running:
            if not self.ticket:
                logger.info("尝试登录获取 ticket...")
                self.ticket = await self.login()
                if not self.ticket:
                    logger.error("登录失败，10秒后重试")
                    await asyncio.sleep(10)
                    continue

            result = await self.fetch_latest(self.ticket)
            if result == "TOKEN_EXPIRED":
                logger.warning("Ticket 已失效，重新登录")
                self.ticket = None
                continue
            elif result == "BAD_REQUEST":
                logger.error("请求格式错误（可能点ID无效），停止监控")
                self.running = False
                break
            elif result is None:
                logger.warning("获取数据失败，稍后重试")
                await asyncio.sleep(self.check_interval)
                continue

            bj_time, value = result
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[{current_time}] {self.point_name}: {value} @ {bj_time}")

            # 预警判断
            await self.check_and_alert(value)

            await asyncio.sleep(self.check_interval)

    # ---------- 指令定义 ----------
    @filter.command("dcs_start")
    async def start_monitor(self, event: AstrMessageEvent):
        """启动 DCS 监控"""
        if self.running:
            yield event.plain_result("DCS 监控已经在运行中")
            return
        self.running = True
        self.monitor_task = asyncio.create_task(self.monitoring_loop())
        yield event.plain_result("✅ DCS 监控已启动")

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
        msg = f"DCS 监控状态: {status_text}\n监控点位: {self.point_name}\n检查间隔: {self.check_interval}秒"
        if self.low_threshold is not None:
            msg += f"\n低阈值: {self.low_threshold}"
        if self.high_threshold is not None:
            msg += f"\n高阈值: {self.high_threshold}"
        msg += f"\n当前预警会话数: {len(self.alert_targets)}"
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
            yield event.plain_result(f"❌ 已解除当前会话的预警绑定")
        else:
            yield event.plain_result("当前会话并未绑定预警")

    @filter.command("dcs_now")
    async def query_now(self, event: AstrMessageEvent):
        """立即查询一次当前值（需要 ticket 有效）"""
        if not self.ticket:
            yield event.plain_result("尚未登录，请先使用 /dcs_start 启动监控")
            return
        result = await self.fetch_latest(self.ticket)
        if result == "TOKEN_EXPIRED":
            yield event.plain_result("登录已过期，请重新启动监控")
        elif result == "BAD_REQUEST":
            yield event.plain_result("请求格式错误，请检查点ID配置")
        elif result is None:
            yield event.plain_result("获取数据失败")
        else:
            bj_time, value = result
            yield event.plain_result(f"点位 {self.point_name} 最新值: {value}\n时间: {bj_time}")