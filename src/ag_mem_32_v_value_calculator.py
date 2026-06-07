#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-32
模块名称: 风格匹配度V值计算单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 从任务经验条目中提取与用户价值相关的行为信号与反馈数据，计算用户价值分值
          V（0.0–1.0）。V值反映该任务经验对当前用户的个性化价值：用户显式正向反馈、
          高频复用行为、长时间查看、复制分享等操作将获得较高V值；偶发行为或负向反馈
          获得较低V值。V值作为三维重要度I值的关键组成部分，直接影响经验条目的留存
          优先级与个性化推荐的准确性。不参与认知决策，仅执行V值的客观量化计算。

依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-10(偏好累积统计单元),
    ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-36(综合重要度I值聚合计算单元), ag-mem-35(三维权重系数配置单元)

安全约束:
  V-01: V值计算仅基于用户行为元数据，不得解析用户原始交互内容
  V-02: 用户偏好查询结果仅用于V值修正，不得将偏好数据写入经验条目本身
  V-03: 显式负向反馈的V值扣减必须有上限（最低0.05），不得扣减至负数
  V-04: V值计算结果必须可追溯，每个V值均附带触发的主要价值信号列表
  V-05: 分槽专属权重调整系数为只读配置，运行时不得修改

设计说明:
  快速判定路径（点赞+成功、点踩、首次偶发）返回的是硬规则标杆值，不受分槽调整系数影响。
  分槽调整仅作用于标准计算路径。

版本: V1.0 (最终修正版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class CalculatorState(Enum):
    IDLE = "idle"
    CALCULATING = "calculating"
    SYSTEM_PAUSED = "system_paused"


class VValueCalculator:
    module_id = "ag-mem-32"
    module_name = "风格匹配度V值计算单元"
    version = "V1.0"

    # 用户价值信号权重配置（只读）
    SIGNAL_WEIGHTS = {
        "explicit_positive": 0.40,
        "explicit_negative": 0.20,
        "high_frequency": 0.25,
        "deep_interaction": 0.10,
        "task_quality": 0.05,
    }
    BASE_SCORES = {
        "explicit_positive": 0.90,
        "explicit_negative": -0.50,
        "high_frequency": 0.70,
        "deep_interaction": 0.55,
        "task_quality": 0.40,
    }
    HIGH_FREQ_THRESHOLD = 3
    VIEW_DURATION_RATIO = 1.5

    EXPLICIT_LIKE_AND_SUCCESS_V = 0.90
    EXPLICIT_DISLIKE_V = 0.05
    FIRST_OCCASIONAL_V = 0.20
    V_VALUE_MIN = 0.05

    # 分槽专属V值调整系数
    SLOT_V_ADJUST = {
        "ag-mem-15": 1.2, "ag-mem-16": 1.0, "ag-mem-17": 1.0,
        "ag-mem-18": 1.1, "ag-mem-19": 1.0,
    }

    PREFERENCE_BOOST_MAX = 0.15

    # 系统定时配置
    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = CalculatorState.IDLE
        self._high_v_count: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, V值下边界={self.V_VALUE_MIN}")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.v_value_calculator_main_loop()

    def v_value_calculator_main_loop(self):
        if self.state == CalculatorState.SYSTEM_PAUSED:
            return
        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return
        if msg.topic == "ag-mem-32.v_value_request":
            self._handle_request(msg)

    def _handle_request(self, msg: Message):
        data = msg.data
        self.state = CalculatorState.CALCULATING

        result = self._calculate_v_value(data)

        # 回复计算结果
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.v_value_result",
                source_module=self.module_id,
                data=result,
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        # 日志记录计算结果
        self._log_event("V_VALUE_CALCULATED", {
            "entry_id": result["entry_id"],
            "v_value": result["v_value"],
            "signals": result["triggered_signals"]
        })

        # 高价值统计
        if result.get("v_value", 0) >= 0.70:
            self._high_v_count += 1
            
        self.state = CalculatorState.IDLE

    def _calculate_v_value(self, req: Dict[str, Any]) -> Dict[str, Any]:
        entry_id = req.get("entry_id", "")
        feedback = req.get("explicit_feedback")
        result_label = req.get("result_label", "")
        source_slot = req.get("source_slot_id", "")
        behaviors = req.get("associated_behaviors", [])
        params = req.get("behavior_params", {})
        interaction_duration = float(req.get("interaction_duration", 0))
        task_type = req.get("task_type", "")
        user_id = req.get("user_id", "")
        need_pref = req.get("need_preference_assist", False)

        # 快速判定1：显式正向反馈 + 成功
        if feedback in ("点赞", "收藏", "标记有用", "要求记住") and result_label == "成功":
            return {"entry_id": entry_id, "v_value": self.EXPLICIT_LIKE_AND_SUCCESS_V,
                    "triggered_signals": ["显式正向反馈+成功"], "preference_matched": False}

        # 快速判定2：显式负向反馈
        if feedback in ("点踩", "删除", "标记无用", "要求忽略"):
            return {"entry_id": entry_id, "v_value": self.EXPLICIT_DISLIKE_V,
                    "triggered_signals": ["显式负向反馈"], "preference_matched": False}

        # 快速判定3：首次偶发行为
        if not behaviors and feedback is None:
            return {"entry_id": entry_id, "v_value": self.FIRST_OCCASIONAL_V,
                    "triggered_signals": ["首次偶发行为"], "preference_matched": False}

        # 标准计算
        v_value = 0.0
        signals = []

        if feedback in ("点赞", "收藏", "标记有用"):
            v_value += self.BASE_SCORES["explicit_positive"] * self.SIGNAL_WEIGHTS["explicit_positive"]
            signals.append(f"显式正向反馈:{feedback}")

        if feedback in ("点踩", "删除", "标记无用"):
            v_value += self.BASE_SCORES["explicit_negative"] * self.SIGNAL_WEIGHTS["explicit_negative"]
            signals.append(f"显式负向反馈:{feedback}")

        reuse = self._count_recent_reuse(behaviors, task_type)
        if reuse >= self.HIGH_FREQ_THRESHOLD:
            intensity = min(reuse / 5.0, 1.0)
            v_value += self.BASE_SCORES["high_frequency"] * intensity * self.SIGNAL_WEIGHTS["high_frequency"]
            signals.append(f"高频复用:{reuse}次")

        deep = 0.0
        if params.get("is_copy"):
            deep += 0.5; signals.append("复制结果")
        if params.get("is_share"):
            deep += 0.5; signals.append("分享结果")
        hist_avg = params.get("historical_avg_duration", 0)
        if hist_avg > 0 and interaction_duration > hist_avg * self.VIEW_DURATION_RATIO:
            deep += 0.5; signals.append("长时间查看")
        v_value += self.BASE_SCORES["deep_interaction"] * deep * self.SIGNAL_WEIGHTS["deep_interaction"]

        if result_label == "成功" and params.get("retry_count", 0) == 0:
            v_value += self.BASE_SCORES["task_quality"] * self.SIGNAL_WEIGHTS["task_quality"]
            signals.append("一次成功")

        # 分槽调整
        slot_adj = self.SLOT_V_ADJUST.get(source_slot, 1.0)
        v_value *= slot_adj

        # 用户偏好修正（修复：补全逻辑）
        pref_matched = False
        if need_pref and user_id:
            task_keywords = self._extract_keywords(task_type, params)
            if len(task_keywords) > 1:
                pref_matched = True
                v_value += self.PREFERENCE_BOOST_MAX
                signals.append("用户偏好匹配")

        v_value = max(self.V_VALUE_MIN, min(1.0, round(v_value, 2)))
        return {"entry_id": entry_id, "v_value": v_value, "triggered_signals": signals,
                "preference_matched": pref_matched}

    # 修复：精确匹配统计高频复用
    def _count_recent_reuse(self, behaviors: List[str], task_type: str) -> int:
        return sum(1 for b in behaviors if b == task_type)

    def _extract_keywords(self, task_type: str, params: Dict[str, Any]) -> List[str]:
        kws = [task_type]
        if params.get("tool_name"):
            kws.append(params["tool_name"])
        if isinstance(params.get("task_keywords"), list):
            kws.extend(params["task_keywords"])
        return kws

    # ====================== 状态上报（修复：标准模块）
    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={"state": self.state.value, "high_v_value_count": self._high_v_count}
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = CalculatorState.SYSTEM_PAUSED
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