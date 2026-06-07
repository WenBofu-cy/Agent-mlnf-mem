#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-27
模块名称: L4长期层经验抽象提炼单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 接收 ag-mem-26 发起的抽象提炼触发指令，对L4层中来自同一场景分槽的同类经验
          条目集进行通用规则提取。通过聚类分析、频繁模式挖掘与共性特征抽取，将多条相似
          经验提炼为不依赖特定用户、可跨场景复用的通用规则。为L5核心层的安全底线固化
          提供规则候选。不参与经验写入或晋升决策，仅执行规则提取与抽象化处理。

依赖模块: ag-mem-26, ag-mem-28, ag-mem-45
被依赖模块: ag-mem-26, ag-mem-28

安全约束:
  S-01: 提炼过程中仅读取条目的特征向量和工具调用序列，不得访问原始用户输入文本
  S-02: 生成的通用规则必须完全去除个性化参数，仅保留结构化的工具调用模板与任务特征
  S-03: 数据不足时必须明确告知下游模块，不得使用低于3条的数据强行生成规则
  S-04: 高置信度规则推送至L5前必须经过安全规则库（ag-mem-45）的合规校验
  S-05: 提炼操作的输入数据采用快照机制，提炼过程中原条目的并发修改不影响当前提炼结果

版本: V1.0
"""

import time
import uuid
import math
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class RefineState(Enum):
    IDLE = "idle"
    FEATURE_EXTRACT = "feature_extract"
    RULE_GENERATE = "rule_generate"
    OUTPUTTING = "outputting"
    INSUFFICIENT_DATA = "insufficient_data"
    SYSTEM_PAUSED = "system_paused"


class L4AbstractionRefiner:
    module_id = "ag-mem-27"
    module_name = "L4长期层经验抽象提炼单元"
    version = "V1.0"

    MIN_ENTRIES = 3
    MAX_ENTRIES_PER_BATCH = 50
    MAX_CONCURRENT_REFINEMENTS = 3
    SEQUENCE_CONSISTENCY_THRESHOLD = 0.60
    FEATURE_SIGNIFICANCE_THRESHOLD = 0.60
    RESULT_CONSISTENCY_THRESHOLD = 0.70
    HIGH_CONFIDENCE_THRESHOLD = 0.85
    CONFIDENCE_FEATURE_WEIGHT = 0.40
    CONFIDENCE_RESULT_WEIGHT = 0.30
    CONFIDENCE_STRENGTH_WEIGHT = 0.20
    CONFIDENCE_COUNT_WEIGHT = 0.10
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = RefineState.IDLE
        self._total_refinements: int = 0
        self._total_rules: int = 0
        self._avg_confidence: float = 0.0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []
        self._pending_high_conf_rules: Dict[str, Dict[str, Any]] = {}

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"最小条目={self.MIN_ENTRIES}, 最大并发={self.MAX_CONCURRENT_REFINEMENTS}")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l4_abstraction_main_loop()

    def l4_abstraction_main_loop(self):
        if self.state == RefineState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-27.abstract_refine":
            self._handle_abstraction_request(msg)
            return

        if msg.topic == "ag-mem-27.safety_check_response":
            self._handle_safety_check_response(msg)
            return

    def _handle_abstraction_request(self, msg: Message):
        """处理来自 ag-mem-26 的抽象提炼请求"""
        slot_id = msg.data.get("slot_id", "")
        entries_data = msg.data.get("entries", [])
        reason = msg.data.get("reason", "")

        if len(self._pending_high_conf_rules) >= self.MAX_CONCURRENT_REFINEMENTS:
            self._send_insufficient_notice(msg.source_module, "系统繁忙，请稍后重试", len(entries_data))
            return

        if len(entries_data) < self.MIN_ENTRIES:
            self._send_insufficient_notice(msg.source_module, "同类经验条目数不足", len(entries_data))
            return

        self.state = RefineState.FEATURE_EXTRACT
        start_time = time.time()
        batch = entries_data[:self.MAX_ENTRIES_PER_BATCH]

        all_sequences = [e.get("tool_sequence", []) for e in batch]
        avg_len = sum(len(s) for s in all_sequences) / max(len(all_sequences), 1) if all_sequences else 0
        lcs_len = self._lcs_length_multiple(all_sequences)
        sequence_consistency = lcs_len / avg_len if avg_len > 0 else 0

        if sequence_consistency < self.SEQUENCE_CONSISTENCY_THRESHOLD:
            self._send_insufficient_notice(msg.source_module, "工具调用序列一致性不足",
                                           len(batch), round(sequence_consistency, 2))
            self.state = RefineState.IDLE
            return

        all_vectors = [e.get("task_feature_vector", []) for e in batch if e.get("task_feature_vector")]
        cluster_center = self._compute_cluster_center(all_vectors) if all_vectors else []
        similarities = [self._cosine_similarity(v, cluster_center) for v in all_vectors]
        feature_significance = sum(similarities) / max(len(similarities), 1) if similarities else 0

        if feature_significance < self.FEATURE_SIGNIFICANCE_THRESHOLD:
            self._send_insufficient_notice(msg.source_module, "任务特征共性不显著",
                                           len(batch), round(feature_significance, 2))
            self.state = RefineState.IDLE
            return

        result_counts: Dict[str, int] = {}
        for e in batch:
            label = e.get("result_label", "")
            result_counts[label] = result_counts.get(label, 0) + 1
        dominant_count = max(result_counts.values()) if result_counts else 0
        result_consistency = dominant_count / len(batch) if batch else 0

        self.state = RefineState.RULE_GENERATE

        avg_i_value = sum(float(e.get("i_value", 0)) for e in batch) / max(len(batch), 1)
        count_factor = min(len(batch) / 20.0, 1.0)
        confidence = (
            self.CONFIDENCE_FEATURE_WEIGHT * feature_significance +
            self.CONFIDENCE_RESULT_WEIGHT * result_consistency +
            self.CONFIDENCE_STRENGTH_WEIGHT * avg_i_value +
            self.CONFIDENCE_COUNT_WEIGHT * count_factor
        )
        confidence = round(min(confidence, 1.0), 3)

        best_entry = max(batch, key=lambda e: float(e.get("i_value", 0)))
        common_seq = self._longest_common_subsequence_pairwise(
            all_sequences[0], all_sequences[1] if len(all_sequences) > 1 else []
        )

        rule = {
            "rule_id": f"RULE-L4-{uuid.uuid4().hex[:8]}",
            "rule_description": self._generate_description(best_entry, common_seq),
            "applicable_scope": {
                "slot_id": slot_id,
                "tool_types": list(set(t for s in all_sequences for t in s)),
            },
            "confidence": confidence,
            "source_entry_ids": [e.get("entry_id", "") for e in batch],
            "rule_type": "高置信度规则" if confidence >= self.HIGH_CONFIDENCE_THRESHOLD else "一般规则",
            "timestamp": time.time()
        }

        inner_corr_id = msg.correlation_id or str(uuid.uuid4())
        self.state = RefineState.OUTPUTTING

        if confidence >= self.HIGH_CONFIDENCE_THRESHOLD:
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-45",
                    event_type="safety_check",
                    source_module=self.module_id,
                    data={
                        "rule": rule,
                        "tool_sequence": common_seq,
                        "_correlation_id": inner_corr_id
                    }
                )
            self._pending_high_conf_rules[inner_corr_id] = {
                "rule": rule,
                "start_time": start_time,
                "correlation_id": inner_corr_id,
                "target": msg.source_module,
                "slot_id": slot_id,
                "batch": batch
            }
        else:
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-26",
                    event_type="generic_rule",
                    source_module=self.module_id,
                    data=rule
                )
            self._finalize_request(msg.source_module, slot_id, rule, start_time, inner_corr_id)

    def _handle_safety_check_response(self, msg: Message):
        """处理安全合规校验回执"""
        corr_id = msg.correlation_id or msg.data.get("_correlation_id", "")
        pending = self._pending_high_conf_rules.pop(corr_id, None)
        if not pending:
            return

        rule = pending["rule"]
        start_time = pending["start_time"]
        target = pending["target"]
        slot_id = pending["slot_id"]

        if msg.data.get("compliant", False):
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-26",
                    event_type="generic_rule",
                    source_module=self.module_id,
                    data=rule
                )
                self.bus.publish_to_module(
                    target_module="ag-mem-28",
                    event_type="high_confidence_rule",
                    source_module=self.module_id,
                    data=rule
                )
            self._log_event("HIGH_CONFIDENCE_RULE_PUSHED_TO_L5", {
                "rule_id": rule["rule_id"]
            })
        else:
            self._log_event("HIGH_CONFIDENCE_RULE_BLOCKED_BY_SAFETY", {
                "rule_id": rule["rule_id"],
                "reason": msg.data.get("reason", "安全合规检查未通过")
            })

        self._finalize_request(target, slot_id, rule, start_time, corr_id)

    def _finalize_request(self, target: str, slot_id: str, rule: Dict, start_time: float, correlation_id: str = ""):
        """发送最终回执给 ag-mem-26"""
        if self.bus:
            self.bus.publish(
                topic=f"{target}.abstraction_complete",
                source_module=self.module_id,
                data={
                    "slot_id": slot_id,
                    "rules_generated": 1,
                    "confidence": rule["confidence"],
                    "source_entry_ids": rule["source_entry_ids"],
                    "rule_id": rule["rule_id"],
                    "duration_ms": (time.time() - start_time) * 1000 if start_time > 0 else 0
                },
                target_module=target,
                correlation_id=correlation_id
            )

        self._total_refinements += 1
        self._total_rules += 1
        total_conf = self._avg_confidence * (self._total_rules - 1) + rule["confidence"]
        self._avg_confidence = round(total_conf / self._total_rules, 3)

        self.state = RefineState.IDLE

    def _send_insufficient_notice(self, target: str, reason: str, current_count: int,
                                  feature_score: float = 0.0):
        if self.bus:
            self.bus.publish(
                topic=f"{target}.insufficient_data",
                source_module=self.module_id,
                data={
                    "reason": reason,
                    "current_count": current_count,
                    "min_required": self.MIN_ENTRIES,
                    "feature_score": feature_score
                },
                target_module=target
            )
        self.state = RefineState.IDLE

    # ========== 算法工具 ==========
    def _lcs_length_multiple(self, sequences: List[List[str]]) -> int:
        if not sequences:
            return 0
        current = list(sequences[0])
        for seq in sequences[1:]:
            current = self._longest_common_subsequence_pairwise(current, seq)
            if not current:
                return 0
        return len(current)

    def _longest_common_subsequence_pairwise(self, a: List[str], b: List[str]) -> List[str]:
        m, n = len(a), len(b)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if a[i-1] == b[j-1]:
                    dp[i][j] = dp[i-1][j-1] + 1
                else:
                    dp[i][j] = max(dp[i-1][j], dp[i][j-1])
        lcs = []
        i, j = m, n
        while i > 0 and j > 0:
            if a[i-1] == b[j-1]:
                lcs.append(a[i-1])
                i -= 1
                j -= 1
            elif dp[i-1][j] > dp[i][j-1]:
                i -= 1
            else:
                j -= 1
        lcs.reverse()
        return lcs

    def _compute_cluster_center(self, vectors: List[List[float]]) -> List[float]:
        if not vectors:
            return []
        dim = max(len(v) for v in vectors)
        center = [0.0] * dim
        for v in vectors:
            for i in range(min(len(v), dim)):
                center[i] += v[i]
        return [x / len(vectors) for x in center]

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        min_len = min(len(a), len(b))
        a, b = a[:min_len], b[:min_len]
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _generate_description(self, best_entry: Dict, common_sequence: List[str]) -> str:
        tools = best_entry.get("experience_data", {}).get("tools", common_sequence)
        if isinstance(tools, list):
            tool_str = " → ".join(str(t) for t in tools[:5])
        else:
            tool_str = str(tools)
        return f"通用任务规则: 工具序列 [{tool_str}]"

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "total_refinements": self._total_refinements,
                    "total_rules": self._total_rules,
                    "avg_confidence": self._avg_confidence,
                    "pending_safety_checks": len(self._pending_high_conf_rules)
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = RefineState.SYSTEM_PAUSED
        self._total_refinements = 0
        self._total_rules = 0
        self._avg_confidence = 0.0
        self._pending_high_conf_rules.clear()
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