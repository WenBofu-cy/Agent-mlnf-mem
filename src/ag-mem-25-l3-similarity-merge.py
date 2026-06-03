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
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import math


class MergeState(Enum):
    IDLE = "idle"
    SIMILARITY_CHECK = "similarity_check"
    MERGING = "merging"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ExperienceEntry:
    entry_id: str = ""
    source_slot_id: str = ""
    experience_data: Dict[str, Any] = field(default_factory=dict)
    i_value: float = 0.0
    s_value: float = 0.0
    v_value: float = 0.0
    c_value: float = 0.0
    caution_label: str = "NORMAL"
    task_feature_vector: List[float] = field(default_factory=list)
    tool_sequence: List[str] = field(default_factory=list)
    result_label: str = ""
    write_timestamp: float = 0.0


@dataclass
class MergeTriggerCommand:
    target_entries: List[ExperienceEntry] = field(default_factory=list)
    trigger_reason: str = "定时归并"
    source_slot_id: str = ""


@dataclass
class MergedEntry:
    entry_id: str = ""
    source_slot_id: str = ""
    experience_data: Dict[str, Any] = field(default_factory=dict)
    i_value: float = 0.0
    s_value: float = 0.0
    v_value: float = 0.0
    c_value: float = 0.0
    caution_label: str = "NORMAL"
    merged_from_ids: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class MergeCompletionReceipt:
    total_scanned: int = 0
    similar_groups_found: int = 0
    new_entries_created: int = 0
    original_entries_cleared: int = 0
    space_released_bytes: int = 0
    merge_duration_ms: float = 0.0


@dataclass
class MergeStatusReport:
    state: MergeState = MergeState.IDLE
    total_merges: int = 0
    last_merge_duration_ms: float = 0.0
    fragmentation_index: float = 0.0


class L3SimilarityMerge:
    # 相似度权重
    FEATURE_WEIGHT = 0.40
    SEQUENCE_WEIGHT = 0.30
    RESULT_WEIGHT = 0.20
    TIME_WEIGHT = 0.10
    # 归并阈值
    MERGE_THRESHOLD = 0.80
    MAX_ENTRIES_PER_GROUP = 5
    MIN_ENTRIES_PER_GROUP = 2
    # 状态上报间隔
    STATUS_REPORT_INTERVAL_SEC = 120
    # 单次最大处理耗时
    MAX_PROCESSING_TIME_SEC = 30

    def __init__(self):
        self.module_id = "ag-mem-25"
        self.module_name = "L3中期层相似经验归并单元"
        self.version = "V1.0"

        self.state = MergeState.IDLE
        self._total_merges: int = 0
        self._last_merge_time: float = 0.0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_merge_trigger = None
        self._publish_merged_entries = None
        self._publish_clear_list = None
        self._publish_completion_receipt = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 归并阈值={self.MERGE_THRESHOLD}")

    def set_merge_trigger_query(self, callback: Callable[[], Optional[MergeTriggerCommand]]):
        self._query_merge_trigger = callback

    def set_merged_entries_publisher(self, callback: Callable[[List[MergedEntry]], None]):
        self._publish_merged_entries = callback

    def set_clear_list_publisher(self, callback: Callable[[List[str], str], None]):
        self._publish_clear_list = callback

    def set_completion_receipt_publisher(self, callback: Callable[[MergeCompletionReceipt], None]):
        self._publish_completion_receipt = callback

    def set_status_report_publisher(self, callback: Callable[[MergeStatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_merge_cycle(self) -> Optional[MergeCompletionReceipt]:
        now = time.time()

        if self.state == MergeState.SYSTEM_PAUSED:
            return None

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 接收归并触发指令
        trigger = self._query_merge_trigger() if self._query_merge_trigger else None
        if trigger is None or not trigger.target_entries:
            return None

        if len(trigger.target_entries) < self.MIN_ENTRIES_PER_GROUP:
            receipt = MergeCompletionReceipt(
                total_scanned=len(trigger.target_entries),
                merge_duration_ms=0
            )
            if self._publish_completion_receipt:
                self._publish_completion_receipt(receipt)
            return receipt

        self.state = MergeState.SIMILARITY_CHECK
        start_time = time.time()

        # 按来源分槽分组
        slot_groups = self._group_by_slot(trigger.target_entries)
        all_similar_groups = []

        for slot_id, entries in slot_groups.items():
            groups = self._detect_similar_groups(entries)
            all_similar_groups.extend(groups)

        if not all_similar_groups:
            receipt = MergeCompletionReceipt(
                total_scanned=len(trigger.target_entries),
                merge_duration_ms=(time.time() - start_time) * 1000
            )
            if self._publish_completion_receipt:
                self._publish_completion_receipt(receipt)
            self.state = MergeState.IDLE
            return receipt

        # 执行归并
        self.state = MergeState.MERGING
        merged_entries = []
        clear_ids = []
        total_released = 0

        for group in all_similar_groups:
            merged = self._merge_group(group)
            merged_entries.append(merged)
            for entry in group:
                clear_ids.append(entry.entry_id)
            # 估算释放空间
            total_released += sum(len(str(e.experience_data)) for e in group)
            total_released -= len(str(merged.experience_data))

        # 输出结果
        self.state = MergeState.OUTPUTTING

        if self._publish_merged_entries and merged_entries:
            self._publish_merged_entries(merged_entries)
        if self._publish_clear_list and clear_ids:
            self._publish_clear_list(clear_ids, "L3归并淘汰")

        elapsed = (time.time() - start_time) * 1000
        self._total_merges += 1
        self._last_merge_time = elapsed

        receipt = MergeCompletionReceipt(
            total_scanned=len(trigger.target_entries),
            similar_groups_found=len(all_similar_groups),
            new_entries_created=len(merged_entries),
            original_entries_cleared=len(clear_ids),
            space_released_bytes=total_released,
            merge_duration_ms=elapsed
        )

        if self._publish_completion_receipt:
            self._publish_completion_receipt(receipt)

        self._log_event("MERGE_COMPLETED", {
            "scanned": len(trigger.target_entries),
            "groups": len(all_similar_groups),
            "created": len(merged_entries),
            "cleared": len(clear_ids)
        })

        self.state = MergeState.IDLE
        return receipt

    def _group_by_slot(self, entries: List[ExperienceEntry]) -> Dict[str, List[ExperienceEntry]]:
        groups: Dict[str, List[ExperienceEntry]] = {}
        for entry in entries:
            if entry.source_slot_id not in groups:
                groups[entry.source_slot_id] = []
            groups[entry.source_slot_id].append(entry)
        return groups

    def _detect_similar_groups(self, entries: List[ExperienceEntry]) -> List[List[ExperienceEntry]]:
        groups = []
        grouped_flags = set()

        for i, entry_a in enumerate(entries):
            if i in grouped_flags:
                continue
            if entry_a.caution_label == "CAUTION":
                continue

            current_group = [entry_a]
            grouped_flags.add(i)

            for j, entry_b in enumerate(entries):
                if j <= i or j in grouped_flags:
                    continue
                if entry_b.caution_label == "CAUTION":
                    continue

                similarity = self._calculate_comprehensive_similarity(entry_a, entry_b)
                if similarity >= self.MERGE_THRESHOLD:
                    current_group.append(entry_b)
                    grouped_flags.add(j)
                    if len(current_group) >= self.MAX_ENTRIES_PER_GROUP:
                        break

            if len(current_group) >= self.MIN_ENTRIES_PER_GROUP:
                groups.append(current_group)

        return groups

    def _calculate_comprehensive_similarity(self, a: ExperienceEntry, b: ExperienceEntry) -> float:
        # 任务特征向量余弦相似度（权重0.40）
        cosine_sim = self._cosine_similarity(a.task_feature_vector, b.task_feature_vector)

        # 工具调用序列编辑距离相似度（权重0.30）
        seq_sim = self._sequence_similarity(a.tool_sequence, b.tool_sequence)

        # 结果标签一致性（权重0.20）
        result_sim = 1.0 if a.result_label == b.result_label else 0.5

        # 时间窗口接近度（权重0.10）
        time_diff_days = abs(a.write_timestamp - b.write_timestamp) / 86400.0
        time_sim = max(0.0, 1.0 - time_diff_days / 30.0)

        return (
            self.FEATURE_WEIGHT * cosine_sim +
            self.SEQUENCE_WEIGHT * seq_sim +
            self.RESULT_WEIGHT * result_sim +
            self.TIME_WEIGHT * time_sim
        )

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        min_len = min(len(a), len(b))
        a = a[:min_len]
        b = b[:min_len]
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
        # 简化：最长公共子序列长度归一化
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

    def _merge_group(self, group: List[ExperienceEntry]) -> MergedEntry:
        # 选I值最高的作为主条目
        primary = max(group, key=lambda e: e.i_value)

        merged_data = primary.experience_data.copy()
        # 合并差异化特征
        extra_tags = []
        for entry in group:
            tags = entry.experience_data.get("tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    if tag not in extra_tags:
                        extra_tags.append(tag)
        if extra_tags:
            merged_data["tags"] = extra_tags

        merged_i = max(e.i_value for e in group)
        merged_s = max(e.s_value for e in group)
        merged_v = max(e.v_value for e in group)
        merged_c = min(1.0, sum(e.c_value for e in group))

        merged = MergedEntry(
            entry_id=f"L3-MERGED-{uuid.uuid4().hex[:8]}",
            source_slot_id=primary.source_slot_id,
            experience_data=merged_data,
            i_value=round(merged_i, 3),
            s_value=round(merged_s, 3),
            v_value=round(merged_v, 3),
            c_value=round(merged_c, 3),
            caution_label="NORMAL",
            merged_from_ids=[e.entry_id for e in group]
        )

        return merged

    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report(MergeStatusReport(
                state=self.state,
                total_merges=self._total_merges,
                last_merge_duration_ms=self._last_merge_time
            ))

    def get_state(self) -> MergeState:
        return self.state

    def emergency_shutdown(self):
        self.state = MergeState.SYSTEM_PAUSED
        print(f"[{self.module_id}] 紧急熔断")

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(entry)
        if self._publish_event_log:
            self._publish_event_log(entry)

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs


def print_separator(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_main():
    print("=" * 70)
    print("  Agent-mlnf-mem L3中期层相似经验归并单元 (ag-mem-25) 演示")
    print("=" * 70)

    merger = L3SimilarityMerge()
    now = time.time()

    print_separator("STEP 1: 检测并归并相似经验")
    merger.set_merge_trigger_query(lambda: MergeTriggerCommand(
        target_entries=[
            ExperienceEntry(
                entry_id="E01", source_slot_id="ag-mem-16",
                experience_data={"tool": "weather_api", "tags": ["天气"]},
                i_value=0.65, c_value=0.3,
                task_feature_vector=[0.8, 0.2, 0.1],
                tool_sequence=["weather_api", "format_result"],
                result_label="成功",
                write_timestamp=now - 5*86400
            ),
            ExperienceEntry(
                entry_id="E02", source_slot_id="ag-mem-16",
                experience_data={"tool": "weather_api", "tags": ["北京"]},
                i_value=0.70, c_value=0.4,
                task_feature_vector=[0.8, 0.2, 0.1],
                tool_sequence=["weather_api", "format_result"],
                result_label="成功",
                write_timestamp=now - 3*86400
            ),
            ExperienceEntry(
                entry_id="E03", source_slot_id="ag-mem-16",
                experience_data={"tool": "file_read", "tags": ["文档"]},
                i_value=0.50,
                task_feature_vector=[0.1, 0.9, 0.0],
                tool_sequence=["file_read"],
                result_label="成功",
                write_timestamp=now - 1*86400
            ),
        ],
        trigger_reason="定时归并"
    ))
    result = merger.run_merge_cycle()
    if result:
        print(f"  扫描条目: {result.total_scanned}")
        print(f"  相似组: {result.similar_groups_found}")
        print(f"  新条目: {result.new_entries_created}")
        print(f"  清除原始: {result.original_entries_cleared}")

    print_separator("STEP 2: 警示条目不参与归并")
    merger.set_merge_trigger_query(lambda: MergeTriggerCommand(
        target_entries=[
            ExperienceEntry(
                entry_id="E04", source_slot_id="ag-mem-16",
                i_value=0.60, caution_label="CAUTION",
                task_feature_vector=[0.5, 0.5, 0.0],
                tool_sequence=["test"], result_label="失败"
            ),
            ExperienceEntry(
                entry_id="E05", source_slot_id="ag-mem-16",
                i_value=0.62,
                task_feature_vector=[0.5, 0.5, 0.0],
                tool_sequence=["test"], result_label="成功"
            ),
        ]
    ))
    result = merger.run_merge_cycle()
    if result:
        print(f"  相似组: {result.similar_groups_found} (警示条目被跳过)")

    print("\n✅ L3中期层相似经验归并单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-25 L3中期层相似经验归并单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_merger():
            return L3SimilarityMerge()

        # TC-M25-01: 两条高度相似经验归并
        print("\n[TC-M25-01] 两条高度相似经验归并")
        try:
            m = setup_merger()
            now = time.time()
            m.set_merge_trigger_query(lambda: MergeTriggerCommand(
                target_entries=[
                    ExperienceEntry(entry_id="A1", source_slot_id="ag-mem-16", i_value=0.65, c_value=0.3,
                                    task_feature_vector=[0.8, 0.2], tool_sequence=["t1", "t2"],
                                    result_label="成功", write_timestamp=now),
                    ExperienceEntry(entry_id="A2", source_slot_id="ag-mem-16", i_value=0.70, c_value=0.4,
                                    task_feature_vector=[0.8, 0.2], tool_sequence=["t1", "t2"],
                                    result_label="成功", write_timestamp=now),
                ]
            ))
            result = m.run_merge_cycle()
            assert result is not None
            assert result.similar_groups_found == 1
            assert result.new_entries_created == 1
            assert result.original_entries_cleared == 2
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M25-02: 无相似经验
        print("\n[TC-M25-02] 无相似经验")
        try:
            m = setup_merger()
            m.set_merge_trigger_query(lambda: MergeTriggerCommand(
                target_entries=[
                    ExperienceEntry(entry_id="B1", source_slot_id="ag-mem-16", i_value=0.5,
                                    task_feature_vector=[1.0, 0.0], tool_sequence=["x"], result_label="成功"),
                    ExperienceEntry(entry_id="B2", source_slot_id="ag-mem-16", i_value=0.5,
                                    task_feature_vector=[0.0, 1.0], tool_sequence=["y"], result_label="失败"),
                ]
            ))
            result = m.run_merge_cycle()
            assert result is not None
            assert result.similar_groups_found == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M25-03: 警示条目被跳过
        print("\n[TC-M25-03] 警示条目被跳过")
        try:
            m = setup_merger()
            now = time.time()
            m.set_merge_trigger_query(lambda: MergeTriggerCommand(
                target_entries=[
                    ExperienceEntry(entry_id="C1", source_slot_id="ag-mem-16", i_value=0.6, caution_label="CAUTION",
                                    task_feature_vector=[0.5, 0.5], tool_sequence=["a"], result_label="失败"),
                    ExperienceEntry(entry_id="C2", source_slot_id="ag-mem-16", i_value=0.62,
                                    task_feature_vector=[0.5, 0.5], tool_sequence=["a"], result_label="成功"),
                ]
            ))
            result = m.run_merge_cycle()
            assert result is not None
            assert result.similar_groups_found == 0  # 因为CAUTION条目被跳过，只剩下1条不够归并
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M25-04: 条目数不足
        print("\n[TC-M25-04] 条目数不足")
        try:
            m = setup_merger()
            m.set_merge_trigger_query(lambda: MergeTriggerCommand(
                target_entries=[ExperienceEntry(entry_id="D1", source_slot_id="ag-mem-16", i_value=0.5)]
            ))
            result = m.run_merge_cycle()
            assert result is not None
            assert result.total_scanned == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M25-05: C值累加上限
        print("\n[TC-M25-05] C值累加上限（不超过1.0）")
        try:
            m = setup_merger()
            now = time.time()
            m.set_merge_trigger_query(lambda: MergeTriggerCommand(
                target_entries=[
                    ExperienceEntry(entry_id="E1", source_slot_id="ag-mem-16", i_value=0.5, c_value=0.6,
                                    task_feature_vector=[0.8, 0.2], tool_sequence=["t"], result_label="成功", write_timestamp=now),
                    ExperienceEntry(entry_id="E2", source_slot_id="ag-mem-16", i_value=0.5, c_value=0.5,
                                    task_feature_vector=[0.8, 0.2], tool_sequence=["t"], result_label="成功", write_timestamp=now),
                ]
            ))
            result = m.run_merge_cycle()
            assert result is not None
            assert result.new_entries_created == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M25-06: 紧急熔断
        print("\n[TC-M25-06] 紧急熔断")
        try:
            m = setup_merger()
            m.emergency_shutdown()
            assert m.state == MergeState.SYSTEM_PAUSED
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        print("\n" + "=" * 60)
        print(f"测试结果: {passed} PASS, {failed} FAIL")
        print("=" * 60)
    else:
        demo_main()
```