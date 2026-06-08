#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-38
模块名称: 晋升双条件判定单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 接收来自各层级存储的晋升候选条目，逐一校验是否同时满足留存时长与综合重要度I值
          的双条件阈值。满足条件的条目整理为“晋升候选清单”，发送至 ag-mem-39 执行物理晋升；
          不满足条件的条目返回来源层级继续保留或进入遗忘评估。同时校验条目是否带有警示标签
          （CAUTION/PERMANENT_CAUTION）—— CAUTION 条目默认禁止晋升至 L4，需先通过 ag-mem-43
          安全仲裁；PERMANENT_CAUTION 条目编译期禁止任何晋升。不参与搬运执行或内容修改，
          仅负责晋升条件的客观校验与清单生成。

依赖模块:
    ag-mem-21/22/24/26(各层级存储), ag-mem-35(三维权重系数配置单元),
    ag-mem-37(重要度增量定时刷新单元)
被依赖模块:
    ag-mem-39(层级单向搬运写入单元), ag-mem-43(失败经验安全仲裁三道校验单元)

安全约束:
  P-01: 警示标签为 PERMANENT_CAUTION 的条目编译期禁止任何层级的晋升，仅人工可解除
  P-02: 警示标签为 CAUTION 的条目禁止直接晋升至 L4，必须先通过 ag-mem-43 三道安全仲裁
  P-03: 晋升双条件（留存时长 + I值）必须同时满足，任一条不满足即拒绝晋升
  P-04: 晋升阈值必须使用各分槽专属配置，不得使用全局默认值一刀切

版本: V1.0 (生产级标准化优化版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class JudgeState(Enum):
    IDLE = "idle"
    JUDGING = "judging"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


class PromotionConditionJudge:
    module_id = "ag-mem-38"
    module_name = "晋升双条件判定单元"
    version = "V1.0"

    # 各层级最小留存时长（小时）
    MIN_RETENTION = {
        ("L1", "L2"): 24,
        ("L2", "L3"): 168,       # 7天
        ("L3", "L4"): 720,       # 30天
        ("L4", "L5"): 2160,      # 90天
    }

    # 分槽默认晋升阈值（当 ag-mem-35 未同步时使用）
    DEFAULT_SLOT_THRESHOLDS = {
        "ag-mem-15": {"L1_L2": 0.35, "L2_L3": 0.55, "L3_L4": 0.75, "L4_L5": 0.90},
        "ag-mem-16": {"L1_L2": 0.40, "L2_L3": 0.60, "L3_L4": 0.80, "L4_L5": 0.90},
        "ag-mem-17": {"L1_L2": 0.38, "L2_L3": 0.58, "L3_L4": 0.78, "L4_L5": 0.88},
        "ag-mem-18": {"L1_L2": 0.38, "L2_L3": 0.58, "L3_L4": 0.78, "L4_L5": 0.90},
        "ag-mem-19": {"L1_L2": 0.42, "L2_L3": 0.62, "L3_L4": 0.82, "L4_L5": 0.92},
    }

    STATUS_REPORT_INTERVAL_SEC = 120
    # 默认分槽ID（兜底配置）
    DEFAULT_SLOT_ID = "ag-mem-19"

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = JudgeState.IDLE
        # 分槽阈值配置（P-04 优先使用35同步的配置）
        self._slot_thresholds: Dict[str, Dict[str, float]] = self.DEFAULT_SLOT_THRESHOLDS.copy()
        # 统计指标
        self._total_judged: int = 0
        self._total_approved: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 统一主循环入口（全系统对齐） ======================
    def run_cycle(self):
        self.promotion_judge_main_loop()

    def promotion_judge_main_loop(self):
        if self.state == JudgeState.SYSTEM_PAUSED:
            return

        # 处理总线消息
        if self.bus:
            self.bus.process_batch(10)

        # 定期状态上报
        now = time.time()
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息分发（异常防护） ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict) or self.state == JudgeState.SYSTEM_PAUSED:
            return

        try:
            if msg.topic == "ag-mem-38.promotion_candidates":
                self._handle_promotion_request(msg)
            elif msg.topic == "ag-mem-38.weight_update":
                self._sync_slot_thresholds(msg.data)
        except Exception as e:
            self._log_event("MESSAGE_PROCESS_ERROR", {
                "topic": msg.topic,
                "error": str(e)
            })

    def _sync_slot_thresholds(self, data: Dict[str, Any]):
        """同步ag-mem-35的分槽配置（P-04 核心实现）"""
        try:
            slot_configs = data.get("slot_configs", {})
            for slot_id, config in slot_configs.items():
                if "promotion" in config and isinstance(config["promotion"], dict):
                    self._slot_thresholds[slot_id] = config["promotion"]
            self._log_event("CONFIG_SYNC_SUCCESS", {"updated_slots": list(slot_configs.keys())})
        except Exception as e:
            self._log_event("CONFIG_SYNC_FAILED", {"error": str(e)})

    # ====================== 核心晋升判定逻辑 ======================
    def _handle_promotion_request(self, msg: Message):
        """处理晋升判定请求（严格遵守P-01~P-04）"""
        data = msg.data
        source_layer = data.get("source_layer", "").strip()
        target_layer = data.get("target_layer", "").strip()
        entries = data.get("entries", [])

        # 基础参数校验
        if not all([source_layer, target_layer, entries]):
            self._log_event("INVALID_REQUEST", {"reason": "缺失必要参数"})
            return

        self.state = JudgeState.JUDGING
        min_retention_h = self.MIN_RETENTION.get((source_layer, target_layer), 24)
        approved_entries = []
        rejection_stats = {}

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            # 安全提取条目核心字段
            entry_id = entry.get("entry_id", "unknown")
            caution_label = entry.get("caution_label", "NORMAL").upper()
            source_slot = entry.get("source_slot_id", self.DEFAULT_SLOT_ID)
            
            # 安全数值转换（防止类型错误崩溃）
            i_value = self._safe_float(entry.get("i_value"))
            retention_h = self._safe_float(entry.get("retention_duration_h"))
            s_value = self._safe_float(entry.get("s_value"))
            rule_confidence = self._safe_float(entry.get("rule_confidence"))

            # ============== P-01: 永久警示标签禁止晋升 ==============
            if caution_label == "PERMANENT_CAUTION":
                self._count_reject(rejection_stats, "P-01 永久警示标签禁止晋升")
                continue

            # ============== P-02: CAUTION标签禁止晋升L4 ==============
            if caution_label == "CAUTION" and target_layer == "L4":
                self._count_reject(rejection_stats, "P-02 警示标签需安全仲裁")
                self._send_caution_intercept(entry_id, source_layer)
                continue

            # ============== P-03: 留存时长校验 ==============
            if retention_h < min_retention_h:
                self._count_reject(rejection_stats, "P-03 留存时长不足")
                continue

            # ============== P-04: 分槽专属I值阈值校验 ==============
            threshold = self._get_promotion_threshold(source_slot, source_layer, target_layer)
            if i_value < threshold:
                self._count_reject(rejection_stats, "P-03 I值未达分槽阈值")
                continue

            # L4→L5 特殊高阶条件
            if source_layer == "L4" and target_layer == "L5":
                if s_value < 0.9 or rule_confidence < 0.85:
                    self._count_reject(rejection_stats, "L5特殊条件不满足")
                    continue

            # 所有条件通过
            approved_entries.append(entry)

        # 发送通过清单至晋升执行单元
        self._send_promotion_list(source_layer, target_layer, approved_entries)
        # 回复判定结果至请求方
        self._send_judge_result(msg, len(entries), len(approved_entries), rejection_stats)

        # 更新统计
        self._total_judged += len(entries)
        self._total_approved += len(approved_entries)
        self.state = JudgeState.IDLE

    # ====================== 工具方法 ======================
    def _safe_float(self, value: Any) -> float:
        """安全浮点数转换，杜绝类型异常"""
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def _count_reject(self, stats: Dict[str, int], reason: str):
        """统计拒绝原因"""
        stats[reason] = stats.get(reason, 0) + 1

    def _send_caution_intercept(self, entry_id: str, source_layer: str):
        """发送CAUTION标签拦截通知至ag-mem-43"""
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-43",
                event_type="caution_intercept",
                source_module=self.module_id,
                data={"entry_id": entry_id, "source_layer": source_layer}
            )

    def _get_promotion_threshold(self, slot_id: str, source: str, target: str) -> float:
        """获取分槽专属晋升阈值（P-04 兜底保障）"""
        # 层级映射键
        level_key = {
            ("L1", "L2"): "L1_L2",
            ("L2", "L3"): "L2_L3",
            ("L3", "L4"): "L3_L4",
            ("L4", "L5"): "L4_L5",
        }.get((source, target), "L1_L2")

        # 优先使用分槽专属配置，其次默认配置
        slot_config = self._slot_thresholds.get(slot_id, self._slot_thresholds[self.DEFAULT_SLOT_ID])
        return self._safe_float(slot_config.get(level_key, 0.40))

    def _send_promotion_list(self, source: str, target: str, entries: List[Dict]):
        """发送晋升清单至ag-mem-39"""
        if entries and self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-39",
                event_type="promotion_list",
                source_module=self.module_id,
                data={"source_layer": source, "target_layer": target, "entries": entries}
            )

    def _send_judge_result(self, msg: Message, total: int, approved: int, reasons: Dict[str, int]):
        """回复判定结果至请求方"""
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.judge_result",
                source_module=self.module_id,
                data={
                    "source_layer": msg.data.get("source_layer"),
                    "target_layer": msg.data.get("target_layer"),
                    "total_candidates": total,
                    "approved_count": approved,
                    "rejected_count": total - approved,
                    "rejection_reasons": reasons
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 状态与日志 ======================
    def _report_status(self):
        """定期状态上报"""
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_judged": self._total_judged,
                    "total_approved": self._total_approved
                }
            )

    def emergency_shutdown(self):
        """紧急停机"""
        self.state = JudgeState.SYSTEM_PAUSED
        self._pending_logs.clear()
        self._log_event("EMERGENCY_SHUTDOWN", {})

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
        """收集待上报日志"""
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs