#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-37
模块名称: 重要度增量定时刷新单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 作为漏斗二中重要度维护的定时驱动器，每24小时对全量经验条目执行一次I值增量刷新。
          协调 ag-mem-33（C值统计单元）重新计算所有条目的复用频次C值，再协调 ag-mem-36
          （I值聚合单元）基于最新的C值及其他维度分值重新聚合I值。通过定期刷新，确保经验的
          重要度始终反映最新的调用状态与时间衰减趋势。不参与各维度分值计算，仅执行刷新任务
          的编排、分批调度与进度监控。

依赖模块:
    ag-mem-03(漏斗二专属调度单元), ag-mem-33(C值统计单元),
    ag-mem-36(I值聚合单元), ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-03, ag-mem-33, ag-mem-36

安全约束:
  R-01: 刷新过程不得修改经验条目的任何内容数据，仅触发数值更新（C值与I值）
  R-02: 每批条目数硬限制为500条，批次间隔默认200ms，防止批量操作冲击下游存储与计算模块
  R-03: 刷新过程中不得跳过L4/L5层条目，确保长期与核心经验的重要度也得到及时更新
  R-04: 手动刷新必须有1小时冷却时间，防止管理员误操作频繁触发全量刷新导致系统负载过高
  R-05: 刷新超时后已完成的批次结果有效，未完成批次在下次刷新周期自动补全

版本: V1.0 (最终可提交版)
"""

import time
import uuid
import math
import copy
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class SchedulerState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    C_REFRESHING = "c_refreshing"
    I_RECALC = "i_recalc"
    SYSTEM_PAUSED = "system_paused"


class IValueRefreshScheduler:
    module_id = "ag-mem-37"
    module_name = "重要度增量定时刷新单元"
    version = "V1.0"

    # 刷新配置 | 严格遵守安全约束
    INTERVAL_HOURS = 24.0          # 24小时定时刷新
    MAX_BATCH_SIZE = 500           # R-02 批次硬上限
    BATCH_INTERVAL_MS = 200.0      # R-02 批次间隔
    C_REFRESH_TIMEOUT_SEC = 300.0
    I_RECALC_TIMEOUT_SEC = 600.0
    MANUAL_COOLDOWN_HOURS = 1.0    # R-04 手动冷却时间
    PROTECTED_LEVELS = {"L4", "L5"}# R-03 不可跳过的层级

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = SchedulerState.IDLE

        # 基础刷新状态
        self._last_refresh_complete_time: float = time.time()
        self._last_manual_refresh_time: float = 0.0
        self._last_batch_send_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 全量条目与批次管理
        self._all_entries: List[Dict[str, Any]] = []
        self._unfinished_entries: List[Dict[str, Any]] = []  # R-05 超时未完成补全
        self._current_batch_index: int = 0
        self._total_batches: int = 0

        # 执行统计
        self._c_start_time: float = 0.0
        self._i_start_time: float = 0.0
        self._c_success_count: int = 0
        self._i_success_count: int = 0
        self._error_entries: List[str] = []
        self._last_progress_time: float = 0.0

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.i_refresh_scheduler_main_loop()

    def i_refresh_scheduler_main_loop(self):
        if self.state == SchedulerState.SYSTEM_PAUSED:
            return

        # 处理总线消息
        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        self._check_scheduled_refresh(now)
        self._check_timeouts(now)
        self._check_nonblocking_batch(now)
        self._report_progress_periodically(now)

    # ====================== 核心调度逻辑 ======================
    def _check_scheduled_refresh(self, now: float):
        """定时刷新检查"""
        if self.state != SchedulerState.IDLE:
            return
        # 优先处理R-05未完成的条目
        if self._unfinished_entries:
            self._start_refresh("超时补全刷新")
            return
        # 24小时定时触发
        hours_since = (now - self._last_refresh_complete_time) / 3600
        if hours_since >= self.INTERVAL_HOURS:
            self._start_refresh("定时刷新")

    def _check_nonblocking_batch(self, now: float):
        """非阻塞批次发送（R-02 批次间隔）"""
        if self.state not in (SchedulerState.C_REFRESHING, SchedulerState.I_RECALC):
            return
        if now - self._last_batch_send_time < self.BATCH_INTERVAL_MS / 1000:
            return
        if self.state == SchedulerState.C_REFRESHING:
            self._send_next_c_batch()
        else:
            self._send_next_i_batch()
        self._last_batch_send_time = now

    def _check_timeouts(self, now: float):
        """超时检查 + R-05 未完成条目缓存"""
        if self.state == SchedulerState.C_REFRESHING:
            if now - self._c_start_time > self.C_REFRESH_TIMEOUT_SEC:
                self._cache_unfinished_entries()
                self._finalize_c_phase(timeout=True)
        if self.state == SchedulerState.I_RECALC:
            if now - self._i_start_time > self.I_RECALC_TIMEOUT_SEC:
                self._cache_unfinished_entries()
                self._finalize_refresh(timeout=True)

    def _report_progress_periodically(self, now: float):
        """30秒定期进度上报"""
        if self.state in (SchedulerState.C_REFRESHING, SchedulerState.I_RECALC):
            if now - self._last_progress_time >= 30:
                self._publish_progress()
                self._last_progress_time = now

    # ====================== 总线消息处理 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict) or self.state == SchedulerState.SYSTEM_PAUSED:
            return
        try:
            if msg.topic == "ag-mem-37.manual_refresh":
                self._handle_manual_refresh(msg)
            elif msg.topic == "ag-mem-37.entry_list":
                self._handle_entry_list(msg.data)
            elif msg.topic == "ag-mem-37.c_batch_result":
                self._handle_c_batch_result(msg.data)
            elif msg.topic == "ag-mem-37.i_batch_result":
                self._handle_i_batch_result(msg.data)
        except Exception as e:
            self._log_event("MESSAGE_ERROR", {"error": str(e), "topic": msg.topic})

    def _handle_manual_refresh(self, msg: Message):
        """R-04 手动刷新+冷却校验"""
        if self.state != SchedulerState.IDLE:
            self._log_event("MANUAL_REFRESH_REJECTED", {"reason": "系统繁忙"})
            return

        now = time.time()
        cooldown_sec = self.MANUAL_COOLDOWN_HOURS * 3600
        remaining = cooldown_sec - (now - self._last_manual_refresh_time)
        
        if remaining > 0:
            self._log_event("MANUAL_REFRESH_COOLDOWN", {"remaining_seconds": round(remaining)})
            return

        self._last_manual_refresh_time = now
        self._start_refresh("手动刷新")

    def _handle_entry_list(self, data: Dict[str, Any]):
        """接收全量条目 + R-03 保留L4/L5 + R-05 合并未完成条目"""
        entries = data.get("entries", [])
        # 合并未完成条目（R-05）
        self._all_entries = self._unfinished_entries + entries
        self._unfinished_entries.clear()

        if not self._all_entries:
            self.state = SchedulerState.IDLE
            self._log_event("REFRESH_SKIPPED", {"reason": "无经验条目"})
            return

        # R-03 强制保留L4/L5条目，不跳过
        self._total_batches = math.ceil(len(self._all_entries) / self.MAX_BATCH_SIZE)
        self._current_batch_index = 0
        self._reset_c_phase_stats()
        self.state = SchedulerState.C_REFRESHING
        self._log_event("C_REFRESH_START", {"total_entries": len(self._all_entries), "total_batches": self._total_batches})

    # ====================== 批次发送与结果处理 ======================
    def _send_next_c_batch(self):
        self._send_batch("ag-mem-33", "full_refresh")

    def _send_next_i_batch(self):
        self._send_batch("ag-mem-36", "full_recalc")

    def _send_batch(self, target_module: str, event_type: str):
        """统一批次发送（R-01 只读传递，不修改数据）"""
        if self._current_batch_index >= self._total_batches:
            return
        start = self._current_batch_index * self.MAX_BATCH_SIZE
        end = min(start + self.MAX_BATCH_SIZE, len(self._all_entries))
        # R-01 传递副本，绝不修改原始数据
        batch = copy.deepcopy(self._all_entries[start:end])

        if self.bus:
            self.bus.publish_to_module(
                target_module=target_module,
                event_type=event_type,
                source_module=self.module_id,
                data={
                    "entries": batch,
                    "batch_number": self._current_batch_index,
                    "total_batches": self._total_batches
                }
            )

    def _handle_c_batch_result(self, data: Dict[str, Any]):
        self._process_batch_result(data, "C")
        if self._current_batch_index >= self._total_batches:
            self._finalize_c_phase()

    def _handle_i_batch_result(self, data: Dict[str, Any]):
        self._process_batch_result(data, "I")
        if self._current_batch_index >= self._total_batches:
            self._finalize_refresh()

    def _process_batch_result(self, data: Dict[str, Any], phase: str):
        """统一处理批次回执"""
        batch_num = data.get("batch_number", -1)
        if batch_num != self._current_batch_index:
            self._log_event("STALE_BATCH", {"phase": phase, "expected": self._current_batch_index, "received": batch_num})
            return

        if data.get("success"):
            count = data.get("processed_count", 0)
            if phase == "C":
                self._c_success_count += count
            else:
                self._i_success_count += count
        else:
            self._error_entries.extend(data.get("error_entry_ids", []))

        self._current_batch_index += 1

    # ====================== 阶段收尾与R-05补全 ======================
    def _cache_unfinished_entries(self):
        """R-05 缓存未完成条目，下次周期补全"""
        start = self._current_batch_index * self.MAX_BATCH_SIZE
        self._unfinished_entries = self._all_entries[start:]
        self._log_event("UNFINISHED_ENTRIES_CACHED", {"count": len(self._unfinished_entries)})

    def _finalize_c_phase(self, timeout: bool = False):
        """C值刷新完成，切换到I值重算"""
        if timeout:
            self._log_event("C_REFRESH_TIMEOUT", {"completed_batches": self._current_batch_index})
        self.state = SchedulerState.I_RECALC
        self._current_batch_index = 0
        self._reset_i_phase_stats()

    def _finalize_refresh(self, timeout: bool = False):
        """刷新流程完成，重置状态"""
        if timeout:
            self._log_event("I_RECALC_TIMEOUT", {"completed_batches": self._current_batch_index})

        # 生成回执
        receipt = {
            "total_entries": len(self._all_entries),
            "c_updated": self._c_success_count,
            "i_recalculated": self._i_success_count,
            "errors": len(self._error_entries),
            "unfinished_entries": len(self._unfinished_entries),
            "duration_ms": round((time.time() - self._c_start_time) * 1000)
        }

        if self.bus:
            self.bus.publish_to_module("ag-mem-03", "refresh_complete", self.module_id, receipt)

        # 重置所有状态
        self._last_refresh_complete_time = time.time()
        self._reset_all_states()
        self.state = SchedulerState.IDLE
        self._log_event("REFRESH_COMPLETED", receipt)

    # ====================== 工具方法 ======================
    def _start_refresh(self, reason: str):
        """启动刷新流程，向03请求条目"""
        self.state = SchedulerState.SCANNING
        if self.bus:
            self.bus.publish_to_module("ag-mem-03", "query_all_entries", self.module_id, {})
        self._log_event("REFRESH_TRIGGERED", {"reason": reason})

    def _reset_c_phase_stats(self):
        self._c_start_time = time.time()
        self._c_success_count = 0
        self._error_entries.clear()

    def _reset_i_phase_stats(self):
        self._i_start_time = time.time()
        self._i_success_count = 0

    def _reset_all_states(self):
        self._all_entries.clear()
        self._current_batch_index = 0
        self._total_batches = 0
        self._last_batch_send_time = 0

    def _publish_progress(self):
        """实时进度上报"""
        if not self.bus:
            return
        phase = "C值刷新" if self.state == SchedulerState.C_REFRESHING else "I值重算"
        data = {
            "phase": phase,
            "total_batches": self._total_batches,
            "completed": self._current_batch_index,
            "remaining": self._total_batches - self._current_batch_index
        }
        self.bus.publish_to_module("ag-mem-03", "refresh_progress", self.module_id, data)

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        """紧急停机，缓存未完成条目"""
        self._cache_unfinished_entries()
        self.state = SchedulerState.SYSTEM_PAUSED
        self._pending_logs.clear()
        self._log_event("EMERGENCY_SHUTDOWN", {})

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        """标准化日志"""
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
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs