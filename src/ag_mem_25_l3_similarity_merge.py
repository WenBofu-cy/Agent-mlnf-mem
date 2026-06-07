#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-25
模块名称: L3中期层相似经验归并单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 定期扫描L3中期层中来自同一场景分槽的经验条目，检测并合并场景特征高度相似的
          重复经验。归并操作将多条相似经验整合为一条更精炼的通用经验条目，原条目的重要度
          I值取归并集中最大值，复用频次C值累加，释放冗余条目的存储空间。通过归并减少L3
          层经验碎片化，提升检索效率，为L4长期层提供更高质量的经验候选。不参与晋升决策或
          遗忘判定，仅执行相似检测与归并整合。

依赖模块:
    ag-mem-24(L3中期层存储单元), ag-mem-42(冗余记忆删除与归档单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-24, ag-mem-42

安全约束:
  S-01: 警示条目标记为CAUTION的经验禁止参与归并，确保失败经验的原始数据完整可追溯
  S-02: 归并操作仅修改L3层经验的组织形式，不得改变经验的核心语义与来源归属
  S-03: 归并后的新条目必须完整记录所有被归并的原始条目ID列表，支持追溯
  S-04: 不同场景分槽的经验禁止跨槽归并，即使相似度极高也必须保持分槽隔离
  S-05: 归并过程中不得修改任何条目的警示标签状态

版本: V1.0
"""

import time
import uuid
import math
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class MergeState(Enum):
    IDLE = "idle"
    SIMILARITY_CHECK = "similarity_check"
    MERGING = "merging"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


class L3SimilarityMerge:
    module_id = "ag-mem-25"
    module_name = "L3中期层相似经验归并单元"
    version = "V1.0"

    FEATURE_WEIGHT = 0.40
    SEQUENCE_WEIGHT = 0.30
    RESULT_WEIGHT = 0.20
    TIME_WEIGHT = 0.10
    MERGE_THRESHOLD = 0.80
    MAX_ENTRIES_PER_GROUP = 5
    MIN_ENTRIES_PER_GROUP = 2
    STATUS_REPORT_INTERVAL_SEC = 120
    MAX_PROCESSING_TIME_SEC = 30

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = MergeState.IDLE
        self._total_merges: int = 0
        self._last_merge_time: float = 0.0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 归并阈值={self.MERGE_THRESHOLD}")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l3_similarity_merge_main_loop()

    def l3_similarity_merge_main_loop(self):
        if self.state == MergeState.SYSTEM_PAUSED:
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

        if msg.topic == "ag-mem-25.merge_scan":
            self._handle_merge_request(msg)
            return

    def _handle_merge_request(self, msg: Message):
        """接收来自 ag-mem-24 的归并触发指令"""
        entries_data = msg.data.get("entries", [])
        if not entries_data or len(entries_data) < self.MIN_ENTRIES_PER_GROUP:
            if self.bus:
                self.bus.publish(
                    topic=f"{msg.source_module}.merge_complete",
                    source_module=self.module_id,
                    data={"total_scanned": len(entries_data), "new_entries_created": 0},
                    target_module=msg.source_module,
                    correlation_id=msg.correlation_id
                )
            return

        self.state = MergeState.SIMILARITY_CHECK
        start_time = time.time()

        # 转换为内部使用的条目结构
        entries = []
        for e in entries_data:
            if not isinstance(e, dict):
                continue
            exp_data = e.get("experience_data", {})
            entries.append({
                "entry_id": e.get("entry_id", ""),
                "source_slot_id": e.get("source_slot_id", ""),
                "experience_data": exp_data,
                "i_value": float(e.get("i_value", 0)),
                "s_value": float(e.get("s_value", 0)),
                "v_value": float(e.get("v_value", 0)),
                "c_value": float(e.get("c_value", 0)),
                "caution_label": e.get("caution_label", "NORMAL"),
                "task_feature_vector": e.get("task_feature_vector", []),
                "tool_sequence": e.get("tool_sequence", []),
                "result_label": e.get("result_label", ""),
                "write_timestamp": float(e.get("promoted_at", e.get("timestamp", time.time())))
            })

        # 按来源分槽分组 (S-04)
        slot_groups: Dict[str, List[Dict]] = {}
        for entry in entries:
            slot_id = entry["source_slot_id"]
            if slot_id not in slot_groups:
                slot_groups[slot_id] = []
            slot_groups[slot_id].append(entry)

        all_similar_groups = []
        for slot_id, group in slot_groups.items():
            groups = self._detect_similar_groups(group)
            all_similar_groups.extend(groups)

        if not all_similar_groups:
            elapsed = (time.time() - start_time) * 1000
            if self.bus:
                self.bus.publish(
                    topic=f"{msg.source_module}.merge_complete",
                    source_module=self.module_id,
                    data={"total_scanned": len(entries), "similar_groups_found": 0, "merge_duration_ms": elapsed},
                    target_module=msg.source_module,
                    correlation_id=msg.correlation_id
                )
            self.state = MergeState.IDLE
            return

        # 执行归并
        self.state = MergeState.MERGING
        merged_entries = []
        clear_ids = []
        total_released = 0

        for group in all_similar_groups:
            merged = self._merge_group(group)
            merged_entries.append(merged)
            for entry in group:
                clear_ids.append(entry["entry_id"])
            total_released += sum(len(str(e.get("experience_data", ""))) for e in group) - len(str(merged.get("experience_data", "")))

        # 输出结果
        self.state = MergeState.OUTPUTTING

        if merged_entries and self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-24",
                event_type="merged_entries",
                source_module=self.module_id,
                data={"entries": merged_entries}
            )

        if clear_ids and self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-42",
                event_type="clear_entries",
                source_module=self.module_id,
                data={"entry_ids": clear_ids, "reason": "L3归并淘汰"}
            )

        elapsed = (time.time() - start_time) * 1000
        self._total_merges += 1
        self._last_merge_time = elapsed

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.merge_complete",
                source_module=self.module_id,
                data={
                    "total_scanned": len(entries),
                    "similar_groups_found": len(all_similar_groups),
                    "new_entries_created": len(merged_entries),
                    "original_entries_cleared": len(clear_ids),
                    "space_released_bytes": total_released,
                    "merge_duration_ms": elapsed
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        self._log_event("MERGE_COMPLETED", {"scanned": len(entries), "groups": len(all_similar_groups), "created": len(merged_entries), "cleared": len(clear_ids)})
        self.state = MergeState.IDLE

    def _detect_similar_groups(self, entries: List[Dict]) -> List[List[Dict]]:
        """在同一分槽内检测相似条目组"""
        groups = []
        grouped_flags = set()

        for i, entry_a in enumerate(entries):
            if i in grouped_flags:
                continue
            if entry_a.get("caution_label") == "CAUTION":
                continue

            current_group = [entry_a]
            grouped_flags.add(i)

            for j, entry_b in enumerate(entries):
                if j <= i or j in grouped_flags:
                    continue
                if entry_b.get("caution_label") == "CAUTION":
                    continue

                similarity = self._calculate_similarity(entry_a, entry_b)
                if similarity >= self.MERGE_THRESHOLD:
                    current_group.append(entry_b)
                    grouped_flags.add(j)
                    if len(current_group) >= self.MAX_ENTRIES_PER_GROUP:
                        break

            if len(current_group) >= self.MIN_ENTRIES_PER_GROUP:
                groups.append(current_group)

        return groups

    def _calculate_similarity(self, a: Dict, b: Dict) -> float:
        """计算两个条目的综合相似度"""
        # 特征向量相似度：优先使用 task_feature_vector，若缺失则从 experience_data 提取
        feature_a = a.get("task_feature_vector", [])
        feature_b = b.get("task_feature_vector", [])
        if not feature_a or not feature_b:
            feature_a, feature_b = self._extract_fallback_features(a, b)
        cosine_sim = self._cosine_similarity(feature_a, feature_b)

        # 工具序列相似度：优先使用 tool_sequence，若缺失则从 experience_data 提取
        seq_a = a.get("tool_sequence", [])
        seq_b = b.get("tool_sequence", [])
        if not seq_a or not seq_b:
            seq_a, seq_b = self._extract_fallback_sequences(a, b)
        seq_sim = self._sequence_similarity(seq_a, seq_b)

        # 结果标签一致性
        result_sim = 1.0 if a.get("result_label") == b.get("result_label") else 0.5

        # 时间窗口接近度
        time_diff = abs(a.get("write_timestamp", 0) - b.get("write_timestamp", 0)) / 86400.0
        time_sim = max(0.0, 1.0 - time_diff / 30.0)

        return (
            self.FEATURE_WEIGHT * cosine_sim +
            self.SEQUENCE_WEIGHT * seq_sim +
            self.RESULT_WEIGHT * result_sim +
            self.TIME_WEIGHT * time_sim
        )

    def _extract_fallback_features(self, a: Dict, b: Dict) -> tuple:
        """从 experience_data 中提取任务特征标识并转为简单数值向量"""
        exp_a = a.get("experience_data", {})
        exp_b = b.get("experience_data", {})
        # 组合工具列表和任务类型作为特征标识
        tools_a = str(exp_a.get("tools", exp_a.get("tool_name", "")))
        tools_b = str(exp_b.get("tools", exp_b.get("tool_name", "")))
        task_a = str(exp_a.get("task_type", ""))
        task_b = str(exp_b.get("task_type", ""))
        # 使用简单的哈希映射生成数值向量
        combined_a = f"{tools_a}:{task_a}"
        combined_b = f"{tools_b}:{task_b}"
        vec_a = self._text_to_simple_vector(combined_a)
        vec_b = self._text_to_simple_vector(combined_b)
        return vec_a, vec_b

    def _extract_fallback_sequences(self, a: Dict, b: Dict) -> tuple:
        """从 experience_data 中提取工具调用序列"""
        exp_a = a.get("experience_data", {})
        exp_b = b.get("experience_data", {})
        tools_a = exp_a.get("tools", exp_a.get("tool_name", ""))
        tools_b = exp_b.get("tools", exp_b.get("tool_name", ""))
        # 如果是列表则直接使用，否则按逗号分割
        seq_a = tools_a if isinstance(tools_a, list) else [x.strip() for x in str(tools_a).split(",") if x.strip()]
        seq_b = tools_b if isinstance(tools_b, list) else [x.strip() for x in str(tools_b).split(",") if x.strip()]
        return seq_a, seq_b

    def _text_to_simple_vector(self, text: str, dim: int = 16) -> List[float]:
        """将文本转为固定长度的简单数值向量（用于回退相似度计算）"""
        if not text:
            return [0.0] * dim
        # 使用简单的字符哈希生成向量
        vec = [0.0] * dim
        for i, ch in enumerate(text):
            vec[i % dim] += ord(ch) / 1000.0
        # 归一化
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

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

    def _sequence_similarity(self, a: List[str], b: List[str]) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        max_len = max(len(a), len(b))
        if max_len == 0:
            return 1.0
        lcs_len = self._lcs_length(a, b)
        return lcs_len / max_len

    def _lcs_length(self, a: List[str], b: List[str]) -> int:
        m, n = len(a), len(b)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if a[i-1] == b[j-1]:
                    dp[i][j] = dp[i-1][j-1] + 1
                else:
                    dp[i][j] = max(dp[i-1][j], dp[i][j-1])
        return dp[m][n]

    def _merge_group(self, group: List[Dict]) -> Dict[str, Any]:
        """归并一组相似条目"""
        primary = max(group, key=lambda e: e.get("i_value", 0))

        merged_data = primary.get("experience_data", {}).copy()
        extra_tags = []
        for entry in group:
            tags = entry.get("experience_data", {}).get("tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    if tag not in extra_tags:
                        extra_tags.append(tag)
        if extra_tags:
            merged_data["tags"] = extra_tags

        merged = {
            "entry_id": f"L3-MERGED-{uuid.uuid4().hex[:8]}",
            "source_slot_id": primary.get("source_slot_id", ""),
            "experience_data": merged_data,
            "i_value": round(max(e.get("i_value", 0) for e in group), 3),
            "s_value": round(max(e.get("s_value", 0) for e in group), 3),
            "v_value": round(max(e.get("v_value", 0) for e in group), 3),
            "c_value": round(min(1.0, sum(e.get("c_value", 0) for e in group)), 3),
            "caution_label": "NORMAL",
            "merged_from_ids": [e.get("entry_id", "") for e in group],
            "timestamp": time.time()
        }
        return merged

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={"total_merges": self._total_merges, "last_merge_duration_ms": self._last_merge_time}
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = MergeState.SYSTEM_PAUSED
        # 安全规范：紧急停机清空内存状态
        self._total_merges = 0
        self._last_merge_time = 0.0
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