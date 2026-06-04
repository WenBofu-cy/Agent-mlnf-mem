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
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class JudgeState(Enum):
    IDLE = "idle"
    JUDGING = "judging"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


class CautionLabel(Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    PERMANENT_CAUTION = "PERMANENT_CAUTION"


@dataclass
class PromotionCandidate:
    entry_id: str = ""
    source_layer: str = ""
    target_layer: str = ""
    i_value: float = 0.0
    retention_duration_h: float = 0.0          # 留存时长（小时）
    source_slot_id: str = ""
    caution_label: str = "NORMAL"
    s_value: float = 0.0                       # 用于L4→L5特殊条件
    rule_confidence: float = 0.0               # 用于L4→L5特殊条件


@dataclass
class SlotPromotionThresholds:
    l1_to_l2: float = 0.40
    l2_to_l3: float = 0.60
    l3_to_l4: float = 0.80
    l4_to_l5: float = 0.90


@dataclass
class PromotionCandidateList:
    source_layer: str = ""
    target_layer: str = ""
    entries: List[PromotionCandidate] = field(default_factory=list)


@dataclass
class JudgeResult:
    source_layer: str = ""
    total_candidates: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    approved_list: List[PromotionCandidate] = field(default_factory=list)
    rejection_reasons: Dict[str, int] = field(default_factory=dict)


@dataclass
class CautionInterceptNotice:
    entry_id: str = ""
    source_layer: str = ""
    target_layer: str = ""
    reason: str = ""
    action: str = ""


class PromotionConditionJudge:
    # 各层级最小留存时长（小时）
    MIN_RETENTION = {
        ("L1", "L2"): 24,
        ("L2", "L3"): 168,       # 7天
        ("L3", "L4"): 720,       # 30天
        ("L4", "L5"): 2160,      # 90天
    }

    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.module_id = "ag-mem-38"
        self.module_name = "晋升双条件判定单元"
        self.version = "V1.0"

        self.state = JudgeState.IDLE
        # 晋升阈值缓存（从 ag-mem-35 获取）
        self._slot_thresholds: Dict[str, SlotPromotionThresholds] = {}
        # 统计
        self._total_judged: int = 0
        self._total_approved: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_candidate_list = None       # 获取来自各层级的晋升候选
        self._query_slot_thresholds = None      # 获取分槽专属阈值
        self._query_arbitration_result = None   # 获取安全仲裁结果（未来扩展）

        self._publish_approved_list = None      # 向 ag-mem-39 发布晋升清单
        self._publish_caution_intercept = None  # 向 ag-mem-43 发布警示拦截
        self._publish_judge_result = None       # 向来源返回判定结果
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_candidate_list_query(self, callback: Callable[[], Optional[PromotionCandidateList]]):
        self._query_candidate_list = callback

    def set_slot_thresholds_query(self, callback: Callable[[], Optional[Dict[str, SlotPromotionThresholds]]]):
        self._query_slot_thresholds = callback

    def set_arbitration_result_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_arbitration_result = callback

    def set_approved_list_publisher(self, callback: Callable[[PromotionCandidateList], None]):
        self._publish_approved_list = callback

    def set_caution_intercept_publisher(self, callback: Callable[[CautionInterceptNotice], None]):
        self._publish_caution_intercept = callback

    def set_judge_result_publisher(self, callback: Callable[[JudgeResult], None]):
        self._publish_judge_result = callback

    def set_status_report_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_judge_cycle(self):
        now = time.time()

        if self.state == JudgeState.SYSTEM_PAUSED:
            return

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 同步阈值配置
        if self._query_slot_thresholds:
            thresholds = self._query_slot_thresholds()
            if thresholds:
                self._slot_thresholds = thresholds

        # 接收候选列表
        candidate_list = self._query_candidate_list() if self._query_candidate_list else None
        if candidate_list is None:
            return

        self.state = JudgeState.JUDGING
        result = self._judge_candidates(candidate_list)
        self.state = JudgeState.OUTPUTTING

        # 发送晋升清单
        if result.approved_list and self._publish_approved_list:
            self._publish_approved_list(PromotionCandidateList(
                source_layer=candidate_list.source_layer,
                target_layer=candidate_list.target_layer,
                entries=result.approved_list
            ))

        # 返回判定结果给来源
        if self._publish_judge_result:
            self._publish_judge_result(result)

        # 更新统计
        self._total_judged += result.total_candidates
        self._total_approved += result.approved_count

        self.state = JudgeState.IDLE

    # ========== 核心判定 ==========
    def _judge_candidates(self, candidate_list: PromotionCandidateList) -> JudgeResult:
        source = candidate_list.source_layer
        target = candidate_list.target_layer
        min_hours = self.MIN_RETENTION.get((source, target), 24)

        approved = []
        rejected_reasons = {}

        for entry in candidate_list.entries:
            # 1. PERMANENT_CAUTION 编译期禁止任何晋升
            if entry.caution_label == CautionLabel.PERMANENT_CAUTION.value:
                rejected_reasons["永久警示标签禁止晋升"] = rejected_reasons.get("永久警示标签禁止晋升", 0) + 1
                self._log_event("PERMANENT_CAUTION_BLOCKED", {"entry_id": entry.entry_id})
                continue

            # 2. CAUTION 禁止晋升至 L4
            if entry.caution_label == CautionLabel.CAUTION.value and target == "L4":
                rejected_reasons["警示标签需先通过安全仲裁"] = rejected_reasons.get("警示标签需先通过安全仲裁", 0) + 1
                self._send_caution_intercept(entry)
                continue

            # 3. 校验留存时长
            if entry.retention_duration_h < min_hours:
                rejected_reasons["留存时长不足"] = rejected_reasons.get("留存时长不足", 0) + 1
                continue

            # 4. 校验 I 值（使用分槽专属阈值）
            threshold = self._get_promotion_threshold(entry.source_slot_id, source, target)
            if entry.i_value < threshold:
                rejected_reasons["I值不满足晋升阈值"] = rejected_reasons.get("I值不满足晋升阈值", 0) + 1
                continue

            # 5. L4→L5 特殊条件：S≥0.9 或 规则置信度≥0.85
            if source == "L4" and target == "L5":
                if entry.s_value < 0.9 and entry.rule_confidence < 0.85:
                    rejected_reasons["不满足L5特殊晋升条件"] = rejected_reasons.get("不满足L5特殊晋升条件", 0) + 1
                    continue

            # 全部通过
            approved.append(entry)

        return JudgeResult(
            source_layer=source,
            total_candidates=len(candidate_list.entries),
            approved_count=len(approved),
            rejected_count=len(candidate_list.entries) - len(approved),
            approved_list=approved,
            rejection_reasons=rejected_reasons
        )

    def _get_promotion_threshold(self, slot_id: str, source: str, target: str) -> float:
        slot_cfg = self._slot_thresholds.get(slot_id, SlotPromotionThresholds())
        if (source, target) == ("L1", "L2"):
            return slot_cfg.l1_to_l2
        elif (source, target) == ("L2", "L3"):
            return slot_cfg.l2_to_l3
        elif (source, target) == ("L3", "L4"):
            return slot_cfg.l3_to_l4
        elif (source, target) == ("L4", "L5"):
            return slot_cfg.l4_to_l5
        return 0.40  # 默认

    def _send_caution_intercept(self, entry: PromotionCandidate):
        if self._publish_caution_intercept:
            self._publish_caution_intercept(CautionInterceptNotice(
                entry_id=entry.entry_id,
                source_layer=entry.source_layer,
                target_layer="L4",
                reason="CAUTION标签需先通过安全仲裁",
                action="请将条目发送至 ag-mem-43 进行三道校验"
            ))

    # ========== 辅助 ==========
    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report({
                "state": self.state.value,
                "total_judged": self._total_judged,
                "total_approved": self._total_approved
            })

    def emergency_shutdown(self):
        self.state = JudgeState.SYSTEM_PAUSED
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


# ========== 演示与测试 ==========
def print_separator(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_main():
    print("=" * 70)
    print("  Agent-mlnf-mem 晋升双条件判定单元 (ag-mem-38) 演示")
    print("=" * 70)

    judge = PromotionConditionJudge()
    # 注入阈值配置
    judge.set_slot_thresholds_query(lambda: {
        "ag-mem-16": SlotPromotionThresholds(l1_to_l2=0.40, l2_to_l3=0.60, l3_to_l4=0.80, l4_to_l5=0.90),
        "ag-mem-15": SlotPromotionThresholds(l1_to_l2=0.35, l2_to_l3=0.55, l3_to_l4=0.75, l4_to_l5=0.90),
    })

    print_separator("STEP 1: L2→L3 正常晋升")
    judge.set_candidate_list_query(lambda: PromotionCandidateList(
        source_layer="L2", target_layer="L3",
        entries=[
            PromotionCandidate(entry_id="E01", source_layer="L2", i_value=0.65,
                               retention_duration_h=200, source_slot_id="ag-mem-16"),
            PromotionCandidate(entry_id="E02", source_layer="L2", i_value=0.55,
                               retention_duration_h=200, source_slot_id="ag-mem-16"),  # I值不足
        ]
    ))
    judge.run_judge_cycle()

    print_separator("STEP 2: L3→L4 含 CAUTION 拦截")
    judge.set_candidate_list_query(lambda: PromotionCandidateList(
        source_layer="L3", target_layer="L4",
        entries=[
            PromotionCandidate(entry_id="E03", source_layer="L3", i_value=0.85,
                               retention_duration_h=800, source_slot_id="ag-mem-16",
                               caution_label="CAUTION"),  # 应被拦截
            PromotionCandidate(entry_id="E04", source_layer="L3", i_value=0.85,
                               retention_duration_h=800, source_slot_id="ag-mem-16",
                               caution_label="NORMAL"),    # 正常
        ]
    ))
    judge.run_judge_cycle()

    print("\n✅ 晋升双条件判定单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-38 晋升双条件判定单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_judge():
            j = PromotionConditionJudge()
            j.set_slot_thresholds_query(lambda: {
                "ag-mem-16": SlotPromotionThresholds(l1_to_l2=0.40, l2_to_l3=0.60, l3_to_l4=0.80, l4_to_l5=0.90),
                "ag-mem-19": SlotPromotionThresholds(l1_to_l2=0.42, l2_to_l3=0.62, l3_to_l4=0.82, l4_to_l5=0.92),
            })
            return j

        # TC-M38-01: 满足双条件，批准晋升
        print("\n[TC-M38-01] 满足双条件批准晋升")
        try:
            j = setup_judge()
            j.set_candidate_list_query(lambda: PromotionCandidateList(
                source_layer="L2", target_layer="L3",
                entries=[PromotionCandidate(entry_id="T01", source_layer="L2", i_value=0.65,
                                            retention_duration_h=200, source_slot_id="ag-mem-16")]
            ))
            j.run_judge_cycle()
            assert j._total_approved == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M38-02: I值不足，拒绝晋升
        print("\n[TC-M38-02] I值不足拒绝晋升")
        try:
            j = setup_judge()
            j.set_candidate_list_query(lambda: PromotionCandidateList(
                source_layer="L2", target_layer="L3",
                entries=[PromotionCandidate(entry_id="T02", source_layer="L2", i_value=0.50,
                                            retention_duration_h=200, source_slot_id="ag-mem-16")]
            ))
            j.run_judge_cycle()
            assert j._total_approved == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M38-03: 留存时长不足，拒绝晋升
        print("\n[TC-M38-03] 留存时长不足拒绝晋升")
        try:
            j = setup_judge()
            j.set_candidate_list_query(lambda: PromotionCandidateList(
                source_layer="L2", target_layer="L3",
                entries=[PromotionCandidate(entry_id="T03", source_layer="L2", i_value=0.65,
                                            retention_duration_h=100, source_slot_id="ag-mem-16")]
            ))
            j.run_judge_cycle()
            assert j._total_approved == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M38-04: CAUTION 标签禁止晋升至 L4
        print("\n[TC-M38-04] CAUTION 标签禁止晋升至 L4")
        try:
            j = setup_judge()
            j.set_candidate_list_query(lambda: PromotionCandidateList(
                source_layer="L3", target_layer="L4",
                entries=[PromotionCandidate(entry_id="T04", source_layer="L3", i_value=0.85,
                                            retention_duration_h=800, source_slot_id="ag-mem-16",
                                            caution_label="CAUTION")]
            ))
            j.run_judge_cycle()
            assert j._total_approved == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M38-05: PERMANENT_CAUTION 编译期禁止任何晋升
        print("\n[TC-M38-05] PERMANENT_CAUTION 禁止任何晋升")
        try:
            j = setup_judge()
            j.set_candidate_list_query(lambda: PromotionCandidateList(
                source_layer="L2", target_layer="L3",
                entries=[PromotionCandidate(entry_id="T05", source_layer="L2", i_value=0.65,
                                            retention_duration_h=200, source_slot_id="ag-mem-16",
                                            caution_label="PERMANENT_CAUTION")]
            ))
            j.run_judge_cycle()
            assert j._total_approved == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M38-06: 紧急熔断
        print("\n[TC-M38-06] 紧急熔断")
        try:
            j = setup_judge()
            j.emergency_shutdown()
            assert j.state == JudgeState.SYSTEM_PAUSED
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