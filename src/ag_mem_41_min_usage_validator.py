#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-41
模块名称: 最低复用次数校验单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 接收 ag-mem-40（遗忘阈值判定单元）对接近遗忘阈值边界的条目发起的复用次数保护
          校验请求。校验该条目在同类任务场景下的历史复用次数是否满足最低保护条件：
          若复用次数高于最低保护阈值，即使I值低于遗忘阈值也暂时保留；若复用次数不足且
          I值确实偏低，则确认遗忘判定有效。通过引入复用次数保护机制，避免那些I值因自然
          衰减而暂时低于阈值但仍具有较高实用价值的经验被过早误删。不参与遗忘判定决策，
          仅提供复用次数的客观校验与保护建议。

依赖模块:
    ag-mem-33(C值统计单元), ag-mem-40(遗忘阈值判定单元),
    ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-40

安全约束:
  U-01: 复用次数校验仅读取条目的复用频次统计数据，不得修改任何经验数据
  U-02: 保护有效期到期后，该条目在下次遗忘判定时需重新校验，不得自动续期
  U-03: 工具调用槽（ag-mem-16）的最低保护次数上调20%
  U-04: 校验结果缓存仅用于避免短时间内重复查询，有效期60秒，超时自动失效

版本: V1.0 (生产优化版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class ValidatorState(Enum):
    IDLE = "idle"
    QUERYING_USAGE = "querying_usage"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


class MinUsageValidator:
    module_id = "ag-mem-41"
    module_name = "最低复用次数校验单元"
    version = "V1.0"

    # 基础配置常量
    BASE_PROTECTION = {"L1": 1, "L2": 3, "L3": 5, "L4": 8}
    SLOT_ADJUSTMENTS = {
        "ag-mem-15": 1.0, "ag-mem-16": 1.2, "ag-mem-17": 1.0,
        "ag-mem-18": 1.0, "ag-mem-19": 0.8,
    }
    I_VALUE_RATIO_THRESHOLD = 0.7
    RECENT_CALL_DAYS = 7
    CACHE_VALIDITY_SEC = 60
    STATUS_REPORT_INTERVAL_SEC = 180
    # 兜底默认值
    DEFAULT_LAYER = "L1"
    DEFAULT_SLOT_ID = "ag-mem-19"
    C_VALUE_SCALE = 10

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = ValidatorState.IDLE
        # 缓存：entry_id → (result, timestamp)
        self._cache: Dict[str, tuple] = {}
        # 待处理的校验请求：correlation_id → 请求上下文
        self._pending_requests: Dict[str, Dict[str, Any]] = {}
        self._total_checks: int = 0
        self._protected_count: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 全系统统一调度入口 ======================
    def run_cycle(self):
        self.min_usage_validator_main_loop()

    # ====================== 主循环 ======================
    def min_usage_validator_main_loop(self):
        if self.state == ValidatorState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        # 清理过期缓存
        self._cleanup_cache(now)
        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口（异常防护） ======================
    def handle_message(self, msg: Message):
        # 系统暂停状态直接拦截
        if self.state == ValidatorState.SYSTEM_PAUSED:
            return
        # 非法消息格式校验
        if not isinstance(msg.data, dict):
            self._log_event("INVALID_MSG_FORMAT", {"topic": msg.topic, "reason": "数据非字典类型"})
            return

        try:
            if msg.topic == "ag-mem-41.reuse_check_request":
                self._handle_check_request(msg)
            elif msg.topic == "ag-mem-41.c_value_response":
                self._handle_c_value_response(msg)
        except Exception as e:
            # 全局异常捕获，防止模块宕机
            self._log_event("MSG_PROCESS_ERROR", {
                "topic": msg.topic,
                "error": str(e)
            })

    def _handle_check_request(self, msg: Message):
        """处理校验请求：查缓存或向 ag-mem-33 查询复用数据"""
        entry_id = msg.data.get("entry_id", "")
        # 安全数值转换
        current_i = self._safe_float(msg.data.get("current_i"))
        current_layer = msg.data.get("current_layer", self.DEFAULT_LAYER)
        source_slot_id = msg.data.get("source_slot_id", self.DEFAULT_SLOT_ID)
        forget_threshold = self._safe_float(msg.data.get("forget_threshold"))

        # 缓存命中校验
        if entry_id in self._cache:
            cached_result, cached_time = self._cache[entry_id]
            if time.time() - cached_time < self.CACHE_VALIDITY_SEC:
                self._reply_check_result(msg, cached_result)
                return

        # 异步查询C值
        corr_id = msg.correlation_id or str(uuid.uuid4())
        self._pending_requests[corr_id] = {
            "original_msg": msg,
            "current_i": current_i,
            "current_layer": current_layer,
            "source_slot_id": source_slot_id,
            "forget_threshold": forget_threshold,
            "start_time": time.time()
        }

        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-33",
                event_type="c_value_query",
                source_module=self.module_id,
                data={"entry_id": entry_id, "_correlation_id": corr_id}
            )

    def _handle_c_value_response(self, msg: Message):
        """接收 C 值查询回执，执行校验判定"""
        corr_id = msg.data.get("_correlation_id") or msg.correlation_id
        pending = self._pending_requests.pop(corr_id, None)
        if not pending:
            self._log_event("PENDING_REQUEST_NOT_FOUND", {"correlation_id": corr_id})
            return

        original_msg = pending["original_msg"]
        entry_id = original_msg.data.get("entry_id", "")
        current_layer = pending["current_layer"]
        source_slot_id = pending["source_slot_id"]
        forget_threshold = pending["forget_threshold"]
        current_i = pending["current_i"]

        # 安全获取C值并计算调用次数
        c_value = self._safe_float(msg.data.get("c_value"))
        total_calls = int(c_value * self.C_VALUE_SCALE)

        # 计算最低保护次数
        base = self.BASE_PROTECTION.get(current_layer, 3)
        adj = self.SLOT_ADJUSTMENTS.get(source_slot_id, 1.0)
        min_threshold = max(1, round(base * adj))

        now = time.time()
        is_protected = False
        reason = ""
        protection_days = 0

        # 核心保护判定逻辑（原逻辑100%保留）
        if total_calls >= min_threshold:
            is_protected = True
            reason = f"复用次数满足最低保护条件({total_calls}≥{min_threshold})"
            protection_days = 30
        elif current_i >= forget_threshold * self.I_VALUE_RATIO_THRESHOLD:
            is_protected = True
            reason = f"I值({current_i:.2f})≥遗忘阈值×0.7({forget_threshold*0.7:.2f})"
            protection_days = 30
        else:
            if c_value > 0.1:
                is_protected = True
                reason = f"近期有复用记录"
                protection_days = 7
            else:
                reason = f"复用次数不足({total_calls}<{min_threshold})，建议确认遗忘判定"

        result = {
            "entry_id": entry_id,
            "is_protected": is_protected,
            "current_usage": total_calls,
            "min_protection_threshold": min_threshold,
            "protection_reason": reason,
            "protection_duration_days": protection_days
        }

        # 更新缓存
        self._cache[entry_id] = (result, now)
        self._reply_check_result(original_msg, result)

        # 更新统计
        self._total_checks += 1
        if is_protected:
            self._protected_count += 1

        self._log_event("CHECK_COMPLETE", {
            "entry_id": entry_id,
            "is_protected": is_protected,
            "layer": current_layer
        })

    def _reply_check_result(self, original_msg: Message, result: Dict[str, Any]):
        if self.bus:
            self.bus.publish(
                topic=f"{original_msg.source_module}.reuse_check_result",
                source_module=self.module_id,
                data=result,
                target_module=original_msg.source_module,
                correlation_id=original_msg.correlation_id
            )

    # ====================== 工具方法 ======================
    def _safe_float(self, value: Any) -> float:
        """安全浮点数转换，杜绝类型异常"""
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def _cleanup_cache(self, now: float):
        """清理过期缓存（U-04 约束落地）"""
        expired = [eid for eid, (_, ts) in self._cache.items() if now - ts >= self.CACHE_VALIDITY_SEC]
        for eid in expired:
            del self._cache[eid]
        if expired:
            self._log_event("CACHE_CLEANUP", {"expired_count": len(expired)})

    # ====================== 状态上报 ======================
    def _report_status(self):
        if self.bus:
            ratio = self._protected_count / max(self._total_checks, 1)
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_checks": self._total_checks,
                    "protected_ratio": round(ratio, 3)
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        """标准化紧急停机，清空所有临时数据"""
        self.state = ValidatorState.SYSTEM_PAUSED
        self._cache.clear()
        self._pending_requests.clear()
        self._pending_logs.clear()
        self._log_event("EMERGENCY_SHUTDOWN", {"desc": "模块紧急停机，缓存/请求已清空"})

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        """标准化系统日志"""
        log_entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(log_entry)
        if self.bus:
            self.bus.publish_to_module("ag-mem-51", "log_event", self.module_id, log_entry)

    def collect_pending_logs(self) -> List[Dict]:
        tmp = self._pending_logs.copy()
        self._pending_logs.clear()
        return tmp