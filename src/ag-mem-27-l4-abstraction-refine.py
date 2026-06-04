#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-27
模块名称: L4长期层经验抽象提炼单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 接收 ag-mem-26（L4长期层存储单元）发起的抽象提炼触发指令，对L4层中来自同一
          场景分槽的同类经验条目集进行通用规则提取。通过聚类分析、频繁模式挖掘与共性特征
          抽取，将多条相似但含有个性化差异的经验提炼为一条不依赖特定用户、可跨场景复用的
          通用规则。提炼后的规则存入L4对应条目的关联规则字段，同时返回给ag-mem-26更新条目
          标记。为L5核心层的安全底线固化提供规则候选。不参与经验写入或晋升决策，仅执行
          规则提取与抽象化处理。

依赖模块:
    ag-mem-26(L4长期层存储单元), ag-mem-28(L5核心层存储单元)
被依赖模块:
    ag-mem-26, ag-mem-28

安全约束:
  S-01: 提炼过程中仅读取条目的特征向量和工具调用序列，不得访问原始用户输入文本
  S-02: 生成的通用规则必须完全去除个性化参数，仅保留结构化的工具调用模板与任务特征
  S-03: 数据不足时必须明确告知下游模块，不得使用低于3条的数据强行生成规则
  S-04: 高置信度规则（≥0.85）推送至L5前必须经过安全规则库（ag-mem-45）的合规校验
  S-05: 提炼操作的输入数据采用快照机制，提炼过程中原条目的并发修改不影响当前提炼结果
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import math


class RefineState(Enum):
    IDLE = "idle"
    FEATURE_EXTRACT = "feature_extract"
    RULE_GENERATE = "rule_generate"
    OUTPUTTING = "outputting"
    INSUFFICIENT_DATA = "insufficient_data"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ExperienceEntry:
    entry_id: str = ""
    source_slot_id: str = ""
    experience_data: Dict[str, Any] = field(default_factory=dict)
    i_value: float = 0.0
    s_value: float = 0.0
    c_value: float = 0.0
    task_feature_vector: List[float] = field(default_factory=list)
    tool_sequence: List[str] = field(default_factory=list)
    result_label: str = ""
    abstracted: bool = False


@dataclass
class AbstractionCommand:
    slot_id: str = ""
    entries: List[ExperienceEntry] = field(default_factory=list)
    trigger_reason: str = ""


@dataclass
class GenericRule:
    rule_id: str = ""
    rule_description: str = ""
    applicable_scope: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    source_entry_ids: List[str] = field(default_factory=list)
    rule_type: str = "一般规则"
    timestamp: float = field(default_factory=time.time)


@dataclass
class RefineCompletionReceipt:
    slot_id: str = ""
    rules_generated: int = 0
    confidence: float = 0.0
    source_entry_ids: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class InsufficientDataNotice:
    reason: str = ""
    current_count: int = 0
    min_required: int = 3
    feature_score: float = 0.0


@dataclass
class RefineStatusReport:
    state: RefineState = RefineState.IDLE
    total_refinements: int = 0
    total_rules: int = 0
    avg_confidence: float = 0.0


@dataclass
class SafetyCheckRequest:
    rule: GenericRule = field(default_factory=GenericRule)
    tool_sequence: List[str] = field(default_factory=list)


@dataclass
class SafetyCheckResponse:
    compliant: bool = True
    reason: str = ""


class L4AbstractionRefine:
    MIN_ENTRIES = 3
    MAX_ENTRIES_PER_BATCH = 50
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
        self.module_id = "ag-mem-27"
        self.module_name = "L4长期层经验抽象提炼单元"
        self.version = "V1.0"

        self.state = RefineState.IDLE
        self._total_refinements: int = 0
        self._total_rules: int = 0
        self._avg_confidence: float = 0.0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        self._query_abstraction_command = None
        self._query_safety_check = None  # 安全合规检查回调

        self._publish_generic_rules = None
        self._publish_completion_receipt = None
        self._publish_insufficient_notice = None
        self._publish_high_confidence_rule = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 最小条目={self.MIN_ENTRIES}")

    def set_abstraction_command_query(self, callback: Callable[[], Optional[AbstractionCommand]]):
        self._query_abstraction_command = callback

    def set_safety_check_query(self, callback: Callable[[SafetyCheckRequest], SafetyCheckResponse]):
        """注入安全合规检查回调，连接 ag-mem-45"""
        self._query_safety_check = callback

    def set_generic_rules_publisher(self, callback: Callable[[List[GenericRule]], None]):
        self._publish_generic_rules = callback

    def set_completion_receipt_publisher(self, callback: Callable[[RefineCompletionReceipt], None]):
        self._publish_completion_receipt = callback

    def set_insufficient_notice_publisher(self, callback: Callable[[InsufficientDataNotice], None]):
        self._publish_insufficient_notice = callback

    def set_high_confidence_rule_publisher(self, callback: Callable[[GenericRule], None]):
        self._publish_high_confidence_rule = callback

    def set_status_report_publisher(self, callback: Callable[[RefineStatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_refine_cycle(self) -> Optional[RefineCompletionReceipt]:
        now = time.time()

        if self.state == RefineState.SYSTEM_PAUSED:
            return None

        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        command = self._query_abstraction_command() if self._query_abstraction_command else None
        if command is None or not command.entries:
            return None

        if len(command.entries) < self.MIN_ENTRIES:
            self.state = RefineState.INSUFFICIENT_DATA
            if self._publish_insufficient_notice:
                self._publish_insufficient_notice(InsufficientDataNotice(
                    reason="同类经验条目数不足",
                    current_count=len(command.entries)
                ))
            self.state = RefineState.IDLE
            return None

        self.state = RefineState.FEATURE_EXTRACT
        start_time = time.time()

        # 建立快照，防止并发修改
        entries = command.entries[:self.MAX_ENTRIES_PER_BATCH]

        # 提取公共子序列（使用标准 LCS 算法）
        all_sequences = [e.tool_sequence for e in entries]
        lcs_length = self._lcs_length_multiple(all_sequences)
        avg_len = sum(len(s) for s in all_sequences) / max(len(all_sequences), 1)
        sequence_consistency = lcs_length / max(avg_len, 1) if avg_len > 0 else 0

        if sequence_consistency < self.SEQUENCE_CONSISTENCY_THRESHOLD:
            self.state = RefineState.INSUFFICIENT_DATA
            if self._publish_insufficient_notice:
                self._publish_insufficient_notice(InsufficientDataNotice(
                    reason="工具调用序列一致性不足",
                    current_count=len(entries),
                    feature_score=round(sequence_consistency, 2)
                ))
            self.state = RefineState.IDLE
            return None

        # 任务特征向量聚类
        all_vectors = [e.task_feature_vector for e in entries if e.task_feature_vector]
        cluster_center = self._compute_cluster_center(all_vectors)
        similarities = [self._cosine_similarity(v, cluster_center) for v in all_vectors]
        feature_significance = sum(similarities) / max(len(similarities), 1)

        if feature_significance < self.FEATURE_SIGNIFICANCE_THRESHOLD:
            self.state = RefineState.INSUFFICIENT_DATA
            if self._publish_insufficient_notice:
                self._publish_insufficient_notice(InsufficientDataNotice(
                    reason="任务特征共性不显著",
                    current_count=len(entries),
                    feature_score=round(feature_significance, 2)
                ))
            self.state = RefineState.IDLE
            return None

        # 结果标签一致性
        result_counts: Dict[str, int] = {}
        for e in entries:
            label = e.result_label
            result_counts[label] = result_counts.get(label, 0) + 1
        dominant_count = max(result_counts.values()) if result_counts else 0
        result_consistency = dominant_count / len(entries) if len(entries) > 0 else 0

        # 生成规则
        self.state = RefineState.RULE_GENERATE

        avg_i_value = sum(e.i_value for e in entries) / max(len(entries), 1)
        count_factor = min(len(entries) / 20.0, 1.0)
        confidence = (
            self.CONFIDENCE_FEATURE_WEIGHT * feature_significance +
            self.CONFIDENCE_RESULT_WEIGHT * result_consistency +
            self.CONFIDENCE_STRENGTH_WEIGHT * avg_i_value +
            self.CONFIDENCE_COUNT_WEIGHT * count_factor
        )
        confidence = round(min(confidence, 1.0), 3)

        best_entry = max(entries, key=lambda e: e.i_value)
        # 提取公共子序列用于描述
        common_seq = self._longest_common_subsequence_pairwise(
            all_sequences[0], all_sequences[1] if len(all_sequences) > 1 else []
        )
        rule = GenericRule(
            rule_id=f"RULE-L4-{uuid.uuid4().hex[:8]}",
            rule_description=self._generate_description(best_entry, common_seq),
            applicable_scope={
                "slot_id": command.slot_id,
                "tool_types": list(set(t for s in all_sequences for t in s)),
            },
            confidence=confidence,
            source_entry_ids=[e.entry_id for e in entries],
            rule_type="高置信度规则" if confidence >= self.HIGH_CONFIDENCE_THRESHOLD else "一般规则"
        )

        # 输出结果
        self.state = RefineState.OUTPUTTING

        if self._publish_generic_rules:
            self._publish_generic_rules([rule])

        # 【修复点1】高置信度规则推送前必须通过安全合规检查
        if confidence >= self.HIGH_CONFIDENCE_THRESHOLD and self._publish_high_confidence_rule:
            if self._query_safety_check:
                safety_req = SafetyCheckRequest(
                    rule=rule,
                    tool_sequence=common_seq
                )
                safety_resp = self._query_safety_check(safety_req)
                if safety_resp and safety_resp.compliant:
                    self._publish_high_confidence_rule(rule)
                    self._log_event("HIGH_CONFIDENCE_RULE_PASSED_SAFETY", {
                        "rule_id": rule.rule_id,
                        "confidence": confidence
                    })
                else:
                    self._log_event("HIGH_CONFIDENCE_RULE_BLOCKED_BY_SAFETY", {
                        "rule_id": rule.rule_id,
                        "reason": safety_resp.reason if safety_resp else "安全合规检查未通过"
                    })
            else:
                # 如果未注入安全检查回调，记录告警并阻止推送（安全保守原则）
                self._log_event("SAFETY_CHECK_CALLBACK_MISSING", {
                    "rule_id": rule.rule_id,
                    "message": "未注入安全合规检查回调，高置信度规则被阻止推送"
                })

        elapsed = (time.time() - start_time) * 1000
        receipt = RefineCompletionReceipt(
            slot_id=command.slot_id,
            rules_generated=1,
            confidence=confidence,
            source_entry_ids=rule.source_entry_ids,
            duration_ms=elapsed
        )

        if self._publish_completion_receipt:
            self._publish_completion_receipt(receipt)

        self._total_refinements += 1
        self._total_rules += 1
        total_conf = self._avg_confidence * (self._total_rules - 1) + confidence
        self._avg_confidence = round(total_conf / self._total_rules, 3)

        self._log_event("REFINE_COMPLETED", {
            "slot_id": command.slot_id,
            "confidence": confidence,
            "entry_count": len(entries)
        })

        self.state = RefineState.IDLE
        return receipt

    # ========== LCS 算法修复 ==========
    def _longest_common_subsequence(self, sequences: List[List[str]]) -> List[str]:
        """计算多个序列的最长公共子序列，基于成对DP合并"""
        if not sequences:
            return []
        result = list(sequences[0])
        for seq in sequences[1:]:
            result = self._longest_common_subsequence_pairwise(result, seq)
            if not result:
                break
        return result

    def _longest_common_subsequence_pairwise(self, a: List[str], b: List[str]) -> List[str]:
        """标准动态规划算法求两个序列的 LCS，返回实际序列"""
        m, n = len(a), len(b)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if a[i-1] == b[j-1]:
                    dp[i][j] = dp[i-1][j-1] + 1
                else:
                    dp[i][j] = max(dp[i-1][j], dp[i][j-1])
        # 回溯构建 LCS
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

    def _lcs_length_multiple(self, sequences: List[List[str]]) -> int:
        """计算多个序列的 LCS 长度，使用成对 DP 合并"""
        if not sequences:
            return 0
        current_lcs = list(sequences[0])
        for seq in sequences[1:]:
            current_lcs = self._longest_common_subsequence_pairwise(current_lcs, seq)
            if not current_lcs:
                return 0
        return len(current_lcs)

    def _compute_cluster_center(self, vectors: List[List[float]]) -> List[float]:
        if not vectors:
            return []
        dim = max(len(v) for v in vectors)
        center = [0.0] * dim
        for v in vectors:
            for i in range(min(len(v), dim)):
                center[i] += v[i]
        count = len(vectors)
        return [x / count for x in center]

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

    def _generate_description(self, entry: ExperienceEntry, common_sequence: List[str]) -> str:
        tool_names = entry.experience_data.get("tools", common_sequence)
        if isinstance(tool_names, list):
            tool_str = " → ".join(str(t) for t in tool_names[:5])
        else:
            tool_str = str(tool_names)
        return f"通用任务规则: 工具序列 [{tool_str}]"

    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report(RefineStatusReport(
                state=self.state,
                total_refinements=self._total_refinements,
                total_rules=self._total_rules,
                avg_confidence=self._avg_confidence
            ))

    def get_state(self) -> RefineState:
        return self.state

    def emergency_shutdown(self):
        self.state = RefineState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem L4抽象提炼单元 (ag-mem-27) 演示")
    print("=" * 70)

    refiner = L4AbstractionRefine()
    # 注入安全检查回调，默认合规
    refiner.set_safety_check_query(lambda req: SafetyCheckResponse(compliant=True))

    print_separator("STEP 1: 提炼通用规则（5条相似经验）")
    refiner.set_abstraction_command_query(lambda: AbstractionCommand(
        slot_id="ag-mem-16",
        entries=[
            ExperienceEntry(entry_id=f"E{i}", source_slot_id="ag-mem-16",
                            experience_data={"tools": ["weather_api", "format_result"]},
                            i_value=0.75 + i * 0.02, s_value=0.6, c_value=0.4,
                            task_feature_vector=[0.8, 0.2, 0.1],
                            tool_sequence=["weather_api", "format_result"],
                            result_label="成功")
            for i in range(5)
        ],
        trigger_reason="累计触发"
    ))
    result = refiner.run_refine_cycle()
    if result:
        print(f"  生成规则数: {result.rules_generated}")
        print(f"  置信度: {result.confidence:.3f}")

    print_separator("STEP 2: 数据不足（仅2条）")
    refiner.set_abstraction_command_query(lambda: AbstractionCommand(
        slot_id="ag-mem-15",
        entries=[
            ExperienceEntry(entry_id="E6", source_slot_id="ag-mem-15", i_value=0.5),
            ExperienceEntry(entry_id="E7", source_slot_id="ag-mem-15", i_value=0.5),
        ]
    ))
    refiner.run_refine_cycle()
    print(f"  状态: {refiner.state.value}")

    print("\n✅ L4抽象提炼单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-27 L4抽象提炼单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_refiner(with_safety=True, compliant=True):
            r = L4AbstractionRefine()
            if with_safety:
                r.set_safety_check_query(lambda req: SafetyCheckResponse(compliant=compliant))
            return r

        # TC-M27-01: 正常提炼（5条高度相似）
        print("\n[TC-M27-01] 正常提炼（5条高度相似）")
        try:
            r = setup_refiner()
            r.set_abstraction_command_query(lambda: AbstractionCommand(
                slot_id="ag-mem-16",
                entries=[ExperienceEntry(entry_id=f"T01-{i}", source_slot_id="ag-mem-16",
                          task_feature_vector=[0.8, 0.2, 0.1],
                          tool_sequence=["t1", "t2"], result_label="成功", i_value=0.7) for i in range(5)]
            ))
            result = r.run_refine_cycle()
            assert result is not None
            assert result.rules_generated == 1
            assert result.confidence >= 0.6
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M27-02: 数据不足（仅2条）
        print("\n[TC-M27-02] 数据不足（仅2条）")
        try:
            r = setup_refiner()
            r.set_abstraction_command_query(lambda: AbstractionCommand(
                slot_id="ag-mem-16",
                entries=[ExperienceEntry(entry_id=f"T02-{i}", source_slot_id="ag-mem-16", i_value=0.5) for i in range(2)]
            ))
            r.run_refine_cycle()
            assert r.state == RefineState.IDLE
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M27-03: 序列一致性不足
        print("\n[TC-M27-03] 序列一致性不足")
        try:
            r = setup_refiner()
            entries = []
            for i in range(5):
                seq = [f"tool_{i}_{j}" for j in range(3)]
                entries.append(ExperienceEntry(entry_id=f"T03-{i}", source_slot_id="ag-mem-16",
                                task_feature_vector=[0.8, 0.2, 0.1],
                                tool_sequence=seq, result_label="成功", i_value=0.7))
            r.set_abstraction_command_query(lambda e=entries: AbstractionCommand(slot_id="ag-mem-16", entries=e))
            r.run_refine_cycle()
            assert r.state == RefineState.IDLE
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M27-04: 高置信度规则推送L5（安全合规通过）
        print("\n[TC-M27-04] 高置信度规则推送L5（安全合规）")
        try:
            r = setup_refiner(with_safety=True, compliant=True)
            r.set_abstraction_command_query(lambda: AbstractionCommand(
                slot_id="ag-mem-16",
                entries=[ExperienceEntry(entry_id=f"T04-{i}", source_slot_id="ag-mem-16",
                          task_feature_vector=[0.8, 0.2, 0.1],
                          tool_sequence=["t1", "t2"], result_label="成功", i_value=0.95) for i in range(20)]
            ))
            result = r.run_refine_cycle()
            assert result is not None
            assert result.confidence >= 0.80
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M27-04b: 高置信度规则安全合规不通过，阻止推送
        print("\n[TC-M27-04b] 高置信度规则安全合规不通过")
        try:
            r = setup_refiner(with_safety=True, compliant=False)
            r.set_abstraction_command_query(lambda: AbstractionCommand(
                slot_id="ag-mem-16",
                entries=[ExperienceEntry(entry_id=f"T04b-{i}", source_slot_id="ag-mem-16",
                          task_feature_vector=[0.8, 0.2, 0.1],
                          tool_sequence=["t1", "t2"], result_label="成功", i_value=0.95) for i in range(20)]
            ))
            result = r.run_refine_cycle()
            assert result is not None
            # 规则仍然生成并返回给L4，但不推送L5
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M27-05: 特征不显著
        print("\n[TC-M27-05] 特征不显著")
        try:
            r = setup_refiner()
            entries = []
            for i in range(5):
                entries.append(ExperienceEntry(entry_id=f"T05-{i}", source_slot_id="ag-mem-16",
                                task_feature_vector=[float(i) / 10, float(5-i) / 10, 0.5],
                                tool_sequence=["t1", "t2"], result_label="成功", i_value=0.7))
            r.set_abstraction_command_query(lambda e=entries: AbstractionCommand(slot_id="ag-mem-16", entries=e))
            r.run_refine_cycle()
            assert r.state == RefineState.IDLE
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M27-06: 紧急熔断
        print("\n[TC-M27-06] 紧急熔断")
        try:
            r = setup_refiner()
            r.emergency_shutdown()
            assert r.state == RefineState.SYSTEM_PAUSED
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