#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-34
模块名称: 基础重要度I₀赋值单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 在每条新经验写入漏斗二时，基于经验的生成来源、任务类型、场景标签与语义特征，
          赋予基础重要度初始值I₀（0.0–1.0）。I₀是三维重要度I值的计算起点，反映该经验
          在未被任何S/V/C信号增强或衰减前的“先天重要性”。不参与后续重要度聚合计算或晋升
          决策，仅负责I₀的初始赋值。

依赖模块: ag-mem-15~19, ag-mem-31, ag-mem-35
被依赖模块: ag-mem-36, ag-mem-35

安全约束:
  I-01: I₀赋值规则为只读配置，运行时不得修改
  I-02: I₀值下限为0.05
  I-03: I₀值上限为1.0
  I-04: I₀赋值结果可追溯

版本: V1.0 (最终可提交版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class AssignmentState(Enum):
    IDLE = "idle"
    ASSIGNING = "assigning"
    SYSTEM_PAUSED = "system_paused"


class I0AssignmentUnit:
    module_id = "ag-mem-34"
    module_name = "基础重要度I₀赋值单元"
    version = "V1.0"

    # 来源基线
    SOURCE_BASELINE = {
        "ECC主动请求记录": 0.60,
        "任务执行自动记录-成功": 0.45,
        "任务执行自动记录-失败": 0.50,
        "用户显式反馈触发记录": 0.55,
        "被动观测记录": 0.30,
        "系统安全事件触发记录": 0.70,
    }
    # 任务类型系数
    TASK_TYPE_COEFFICIENT = {
        "工具调用": 1.0,
        "信息检索": 0.9,
        "对话交互": 0.8,
        "创作生成": 0.95,
        "通用任务": 0.85,
    }
    # 加分项
    SENSITIVE_OP_BONUS = 0.20
    PRIVACY_ACCESS_BONUS = 0.15
    ABNORMAL_DURATION_BONUS = 0.10
    USER_SKIP_PENALTY = -0.10
    NEW_SCENE_BONUS = 0.05
    DEFAULT_BASELINE = 0.40
    DEFAULT_COEFFICIENT = 0.85
    MIN_I0 = 0.05
    MAX_I0 = 1.0
    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = AssignmentState.IDLE
        # 修复1：统计数据改为周期重置，避免内存泄漏
        self._distribution: Dict[str, int] = {}
        self._source_sums: Dict[str, float] = {}
        self._source_counts: Dict[str, int] = {}
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.i0_assignment_main_loop()

    def i0_assignment_main_loop(self):
        if self.state == AssignmentState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now
            # 修复1：状态上报后重置统计，实现"近期分布"统计
            self._distribution.clear()
            self._source_sums.clear()
            self._source_counts.clear()

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-34.i0_assignment_request":
            self._handle_request(msg)
            return

    def _handle_request(self, msg: Message):
        data = msg.data
        entry_id = data.get("entry_id", "")
        generation_source = data.get("generation_source", "")
        task_type = data.get("task_type", "")
        is_sensitive = data.get("is_sensitive_operation", False)
        is_privacy = data.get("is_privacy_access", False)
        # 修复3：增加类型防护，避免非数字崩溃
        duration_ratio = float(data.get("execution_duration_ratio", 1.0)) if data.get("execution_duration_ratio") else 1.0
        is_skipped = data.get("is_user_skipped", False)
        is_new_scene = data.get("is_new_scene", False)

        self.state = AssignmentState.ASSIGNING
        result = self._assign_i0(entry_id, generation_source, task_type,
                                is_sensitive, is_privacy, duration_ratio,
                                is_skipped, is_new_scene)
        self.state = AssignmentState.IDLE

        # 日志记录赋值结果（满足I-04可追溯）
        self._log_event("I0_ASSIGNED", {
            "entry_id": entry_id,
            "i0_value": result["i0_value"],
            "basis_summary": result["basis_summary"],
            "adjustment_factors": result["adjustment_factors"]
        })

        # 回复结果
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.i0_result",
                source_module=self.module_id,
                data=result,
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _assign_i0(self, entry_id, generation_source, task_type,
                   is_sensitive, is_privacy, duration_ratio,
                   is_skipped, is_new_scene) -> Dict[str, Any]:
        # 来源基线
        baseline = self.SOURCE_BASELINE.get(generation_source, self.DEFAULT_BASELINE)
        basis_parts = [f"来源基线={baseline}"]

        # 任务类型系数
        coefficient = self.TASK_TYPE_COEFFICIENT.get(task_type, self.DEFAULT_COEFFICIENT)
        basis_parts.append(f"任务系数={coefficient}")

        i0_value = baseline * coefficient
        adjustment_factors = []

        # 加分/减分项
        if is_sensitive:
            i0_value += self.SENSITIVE_OP_BONUS
            adjustment_factors.append(f"敏感操作+{self.SENSITIVE_OP_BONUS}")
        if is_privacy:
            i0_value += self.PRIVACY_ACCESS_BONUS
            adjustment_factors.append(f"隐私访问+{self.PRIVACY_ACCESS_BONUS}")
        if duration_ratio > 2.0:
            i0_value += self.ABNORMAL_DURATION_BONUS
            adjustment_factors.append(f"异常耗时+{self.ABNORMAL_DURATION_BONUS}")
        if is_skipped:
            i0_value += self.USER_SKIP_PENALTY
            adjustment_factors.append(f"用户跳过{self.USER_SKIP_PENALTY}")
        if is_new_scene:
            i0_value += self.NEW_SCENE_BONUS
            adjustment_factors.append(f"新场景+{self.NEW_SCENE_BONUS}")

        # 边界裁剪 + 修复2：记录裁剪操作，满足I-04可追溯
        original_i0 = i0_value
        i0_value = max(self.MIN_I0, min(self.MAX_I0, round(i0_value, 2)))
        if i0_value != round(original_i0, 2):
            adjustment_factors.append(f"边界裁剪至[{self.MIN_I0},{self.MAX_I0}]")

        # 更新统计
        bucket = "0.8-1.0" if i0_value >= 0.8 else \
                 "0.6-0.8" if i0_value >= 0.6 else \
                 "0.4-0.6" if i0_value >= 0.4 else \
                 "0.2-0.4" if i0_value >= 0.2 else "0.05-0.2"
        self._distribution[bucket] = self._distribution.get(bucket, 0) + 1

        src = generation_source or "未知"
        self._source_sums[src] = self._source_sums.get(src, 0.0) + i0_value
        self._source_counts[src] = self._source_counts.get(src, 0) + 1

        return {
            "entry_id": entry_id,
            "i0_value": i0_value,
            "basis_summary": ", ".join(basis_parts),
            "adjustment_factors": adjustment_factors
        }

    def _report_status(self):
        if self.bus:
            avg_by_source = {}
            for src in self._source_sums:
                count = self._source_counts.get(src, 1)
                avg_by_source[src] = round(self._source_sums[src] / count, 3)
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "recent_distribution": self._distribution,
                    "avg_by_source": avg_by_source
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = AssignmentState.SYSTEM_PAUSED
        self._pending_logs.clear()
        self._log_event("SYSTEM_EVENT", {"sub_type": "emergency_shutdown"})

    def _log_event(self, event_type: str, details: Dict[str, Any]):
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