#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-40
模块名称: 遗忘阈值判定单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 扫描漏斗二各层级（L1-L4）中经验条目的综合重要度I值，将其与各分槽专属的遗忘阈值
          进行比对，生成遗忘候选清单。L4层受强遗忘保护，L5层永久保留不参与遗忘判定。将判定后
          的遗忘候选清单发送至 ag-mem-42 执行物理删除或归档。不参与实际删除操作，仅执行阈值
          比对与候选生成。

          对于接近遗忘阈值的条目，本模块会向 ag-mem-41 发起异步复用次数校验请求，并在候选
          清单中标记 pending_reuse_check=True。ag-mem-42 在执行删除前，应查询 ag-mem-41 的
          校验结果，对被保护的条目予以保留。

依赖模块:
    ag-mem-15~19(各场景分槽，提供待扫描条目), ag-mem-35(三维权重系数配置单元，提供遗忘阈值),
    ag-mem-41(最低复用次数校验单元)
被依赖模块:
    ag-mem-42(冗余记忆删除与归档单元), ag-mem-41

安全约束:
  F-01: L5核心层条目永远不参与遗忘判定，编译期硬编码排除
  F-02: L4层条目使用强保护遗忘阈值，仅在I值极低且复用不足时才被纳入候选
  F-03: 遗忘判定必须使用各分槽专属阈值，不得使用全局默认值一刀切
  F-04: 本模块仅生成候选清单，不得直接操作经验条目的删除

版本: V1.1 (F-02安全约束修复版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class ForgetJudgeState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    JUDGING = "judging"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


class ForgetThresholdJudge:
    module_id = "ag-mem-40"
    module_name = "遗忘阈值判定单元"
    version = "V1.1"

    # 系统常量配置
    STATUS_REPORT_INTERVAL_SEC = 120
    NEAR_THRESHOLD_DELTA = 0.05
    DEFAULT_SLOT_ID = "ag-mem-19"
    DEFAULT_LAYER = "L1"

    # 各分槽默认遗忘阈值（当 ag-mem-35 未同步时使用）
    DEFAULT_SLOT_FORGET_THRESHOLDS = {
        "ag-mem-15": {"L1": 0.08, "L2": 0.18, "L3": 0.28, "L4": 0.20},
        "ag-mem-16": {"L1": 0.10, "L2": 0.25, "L3": 0.35, "L4": 0.25},
        "ag-mem-17": {"L1": 0.08, "L2": 0.18, "L3": 0.28, "L4": 0.20},
        "ag-mem-18": {"L1": 0.10, "L2": 0.20, "L3": 0.30, "L4": 0.22},
        "ag-mem-19": {"L1": 0.06, "L2": 0.15, "L3": 0.22, "L4": 0.18},
    }

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = ForgetJudgeState.IDLE
        self._slot_forget_thresholds: Dict[str, Dict[str, float]] = self.DEFAULT_SLOT_FORGET_THRESHOLDS.copy()
        self._total_scanned: int = 0
        self._total_candidates: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 全系统统一调度入口 ======================
    def run_cycle(self):
        self.forget_threshold_judge_main_loop()

    # ====================== 主循环 ======================
    def forget_threshold_judge_main_loop(self):
        if self.state == ForgetJudgeState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口（异常防护） ======================
    def handle_message(self, msg: Message):
        if self.state == ForgetJudgeState.SYSTEM_PAUSED:
            return
        if not isinstance(msg.data, dict):
            self._log_event("INVALID_MSG_FORMAT", {"topic": msg.topic, "reason": "数据非字典类型"})
            return

        try:
            if msg.topic == "ag-mem-40.forget_scan":
                self._handle_forget_scan(msg)
            elif msg.topic == "ag-mem-40.weight_update":
                self._sync_forget_thresholds(msg.data)
        except Exception as e:
            self._log_event("MSG_PROCESS_ERROR", {
                "topic": msg.topic,
                "error": str(e)
            })

    def _sync_forget_thresholds(self, data: Dict[str, Any]):
        """同步ag-mem-35分槽遗忘阈值配置"""
        try:
            slot_configs = data.get("slot_configs", {})
            for slot_id, cfg in slot_configs.items():
                if "forget" in cfg and isinstance(cfg["forget"], dict):
                    self._slot_forget_thresholds[slot_id] = cfg["forget"]
            self._log_event("CONFIG_SYNC_SUCCESS", {"updated_slots": list(slot_configs.keys())})
        except Exception as e:
            self._log_event("CONFIG_SYNC_FAILED", {"error": str(e)})

    def _handle_forget_scan(self, msg: Message):
        """处理遗忘扫描请求"""
        entries = msg.data.get("entries", [])
        trigger_reason = msg.data.get("trigger_reason", "定时扫描")

        if not entries:
            self._log_event("EMPTY_SCAN_REQUEST", {"reason": "无待扫描条目"})
            return

        self.state = ForgetJudgeState.JUDGING
        candidates = []
        protected = 0

        for entry_data in entries:
            if not isinstance(entry_data, dict):
                continue

            # 安全字段提取
            entry_id = entry_data.get("entry_id", f"unknown_{uuid.uuid4().hex[:4]}")
            source_layer = entry_data.get("source_layer", self.DEFAULT_LAYER)
            source_slot_id = entry_data.get("source_slot_id", self.DEFAULT_SLOT_ID)
            
            # 安全数值转换，防止类型异常
            i_value = self._safe_float(entry_data.get("i_value"))
            retention_duration_h = self._safe_float(entry_data.get("retention_duration_h"))

            # F-01: L5 永久保留，不参与遗忘判定
            if source_layer == "L5":
                protected += 1
                continue

            # F-03: 获取分槽专属遗忘阈值
            threshold = self._get_forget_threshold(source_slot_id, source_layer)
            if threshold is None:
                continue

            # I值低于阈值 → 生成遗忘候选
            if i_value < threshold:
                # ==============================================
                # 【修复 F-02】L4 层级强制复用校验（强遗忘保护）
                # 规则：L1/L2/L3 仅接近阈值校验 | L4 全部强制校验
                # ==============================================
                near_threshold = (threshold - i_value) < self.NEAR_THRESHOLD_DELTA
                # 核心修复：L4 强制开启复用检查，满足双条件判定要求
                need_reuse_check = near_threshold or (source_layer == "L4")

                # 满足条件则发起复用次数异步校验
                if need_reuse_check and self.bus:
                    self.bus.publish_to_module(
                        target_module="ag-mem-41",
                        event_type="reuse_check_request",
                        source_module=self.module_id,
                        data={
                            "entry_id": entry_id,
                            "current_i": i_value,
                            "current_layer": source_layer,
                            "source_slot_id": source_slot_id,
                            "forget_threshold": threshold
                        }
                    )

                # 构建候选清单，L4 强制标记待校验
                candidates.append({
                    "entry_id": entry_id,
                    "source_layer": source_layer,
                    "source_slot_id": source_slot_id,
                    "i_value": i_value,
                    "forget_threshold": threshold,
                    "forget_reason": f"I值={i_value:.2f} < 遗忘阈值={threshold:.2f}",
                    "pending_reuse_check": need_reuse_check
                })

        self.state = ForgetJudgeState.OUTPUTTING

        # 发送候选清单至删除单元
        if candidates and self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-42",
                event_type="forget_candidates",
                source_module=self.module_id,
                data={
                    "entries": candidates,
                    "trigger_reason": trigger_reason
                }
            )

        # 回复结果
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.forget_result",
                source_module=self.module_id,
                data={
                    "total_scanned": len(entries),
                    "candidate_count": len(candidates),
                    "protected_count": protected
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        # 更新统计
        self._total_scanned += len(entries)
        self._total_candidates += len(candidates)
        self._log_event("FORGET_JUDGE_COMPLETE", {
            "total": len(entries),
            "candidates": len(candidates),
            "protected": protected
        })
        self.state = ForgetJudgeState.IDLE

    def _safe_float(self, value: Any) -> float:
        """安全浮点数转换，杜绝崩溃"""
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def _get_forget_threshold(self, slot_id: str, layer: str) -> Optional[float]:
        slot_cfg = self._slot_forget_thresholds.get(slot_id, {})
        return slot_cfg.get(layer)

    # ====================== 状态上报 ======================
    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_scanned": self._total_scanned,
                    "total_candidates": self._total_candidates
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        """标准化紧急停机，清空缓存"""
        self.state = ForgetJudgeState.SYSTEM_PAUSED
        self._pending_logs.clear()
        self._log_event("EMERGENCY_SHUTDOWN", {"desc": "模块紧急停机，数据已清空"})

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
        tmp = self._pending_logs.copy()
        self._pending_logs.clear()
        return tmp