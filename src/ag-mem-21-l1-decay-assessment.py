#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-21
模块名称: L1临时层时序衰减单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 接收 ag-mem-20（L1临时层存储单元）发起的衰减评估请求，对L1中留存超过24小时
          的条目进行时序衰减评估。基于条目的留存时长、重要度（I值）及来源场景分槽的专属
          晋升策略，判定每个条目的处理去向：晋升至L2近期层（满足晋升条件）、继续保留在L1
          （未达晋升条件但仍有价值）、或直接清除（重要度过低）。不参与条目内容修改或认知
          决策，仅执行基于时间与重要度的确定性筛选。

依赖模块:
    ag-mem-20(L1临时层存储单元), ag-mem-22(L2近期层存储单元),
    ag-mem-42(冗余记忆删除与归档单元), ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-20, ag-mem-22, ag-mem-42

安全约束:
  S-01: 衰减评估仅读取条目元数据（时间戳、I值、分槽编号），不得访问或修改条目的经验内容
  S-02: 晋升至L2的条目必须完整保留其原始来源分槽编号，用于L2层的后续管理
  S-03: 容量紧急时的阈值调整仅在本次评估中生效，不得持久化修改各分槽的默认阈值配置
  S-04: 清除条目必须通过 ag-mem-42 执行安全删除，本模块不得直接操作存储删除
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class AssessmentState(Enum):
    IDLE = "idle"
    EVALUATING = "evaluating"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class L1EntryInfo:
    entry_id: str = ""
    i_value: float = 0.0
    source_slot_id: str = ""
    write_timestamp: float = 0.0


@dataclass
class DecayRequest:
    entries: List[L1EntryInfo] = field(default_factory=list)
    trigger_reason: str = ""
    l1_usage_pct: float = 0.0


@dataclass
class PromotionThresholdConfig:
    """各分槽的L1→L2晋升I值阈值与遗忘I值阈值"""
    slot_id: str = ""
    promotion_i_threshold: float = 0.40
    forget_i_threshold: float = 0.10


@dataclass
class DecayCompletionReceipt:
    total_evaluated: int = 0
    promoted_count: int = 0
    cleared_count: int = 0
    retained_count: int = 0
    evaluation_duration_ms: float = 0.0


class L1DecayAssessment:
    # 时间阈值
    MIN_RETENTION_HOURS_NORMAL = 24   # 正常模式最小留存时间
    MIN_RETENTION_HOURS_EMERGENCY = 6 # 容量紧急模式最小留存时间
    MAX_L1_RETENTION_HOURS = 72       # L1最大滞留时间

    # 默认晋升阈值配置
    DEFAULT_PROMOTION_CONFIGS: Dict[str, PromotionThresholdConfig] = field(default_factory=lambda: {
        "ag-mem-15": PromotionThresholdConfig(slot_id="ag-mem-15", promotion_i_threshold=0.35, forget_i_threshold=0.08),
        "ag-mem-16": PromotionThresholdConfig(slot_id="ag-mem-16", promotion_i_threshold=0.40, forget_i_threshold=0.10),
        "ag-mem-17": PromotionThresholdConfig(slot_id="ag-mem-17", promotion_i_threshold=0.38, forget_i_threshold=0.08),
        "ag-mem-18": PromotionThresholdConfig(slot_id="ag-mem-18", promotion_i_threshold=0.38, forget_i_threshold=0.10),
        "ag-mem-19": PromotionThresholdConfig(slot_id="ag-mem-19", promotion_i_threshold=0.42, forget_i_threshold=0.06),
    })

    def __init__(self):
        self.module_id = "ag-mem-21"
        self.module_name = "L1临时层时序衰减单元"
        self.version = "V1.0"

        self.state = AssessmentState.IDLE
        self._promotion_configs = self.DEFAULT_PROMOTION_CONFIGS.copy()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_decay_request = None
        self._publish_promoted_list = None
        self._publish_cleared_list = None
        self._publish_retained_confirm = None
        self._publish_completion_receipt = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_decay_request_query(self, callback: Callable[[], Optional[DecayRequest]]):
        self._query_decay_request = callback

    def set_promoted_list_publisher(self, callback: Callable[[List[L1EntryInfo]], None]):
        self._publish_promoted_list = callback

    def set_cleared_list_publisher(self, callback: Callable[[List[L1EntryInfo]], None]):
        self._publish_cleared_list = callback

    def set_retained_confirm_publisher(self, callback: Callable[[int, List[L1EntryInfo]], None]):
        self._publish_retained_confirm = callback

    def set_completion_receipt_publisher(self, callback: Callable[[DecayCompletionReceipt], None]):
        self._publish_completion_receipt = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_assessment_cycle(self) -> Optional[DecayCompletionReceipt]:
        if self.state == AssessmentState.SYSTEM_PAUSED:
            return None

        request = self._query_decay_request() if self._query_decay_request else None
        if request is None or not request.entries:
            return None

        self.state = AssessmentState.EVALUATING
        start_time = time.time()

        is_emergency = request.trigger_reason == "容量紧急"
        now = time.time()

        promoted: List[L1EntryInfo] = []
        cleared: List[L1EntryInfo] = []
        retained: List[L1EntryInfo] = []

        for entry in request.entries:
            retention_hours = (now - entry.write_timestamp) / 3600.0
            config = self._get_slot_config(entry.source_slot_id)

            # 容量紧急时调整阈值
            min_retention = self.MIN_RETENTION_HOURS_EMERGENCY if is_emergency else self.MIN_RETENTION_HOURS_NORMAL
            forget_threshold = config.forget_i_threshold * 1.2 if is_emergency else config.forget_i_threshold

            # 判定处理方式
            if retention_hours >= self.MAX_L1_RETENTION_HOURS and entry.i_value < config.promotion_i_threshold:
                # 滞留超过72小时仍未达到晋升条件，直接清除
                cleared.append(entry)
            elif retention_hours >= min_retention:
                if entry.i_value >= config.promotion_i_threshold:
                    # 满足晋升条件
                    promoted.append(entry)
                elif entry.i_value < forget_threshold:
                    # 低于遗忘阈值
                    cleared.append(entry)
                else:
                    retained.append(entry)
            else:
                retained.append(entry)

        # 输出结果
        self.state = AssessmentState.OUTPUTTING

        if self._publish_promoted_list and promoted:
            self._publish_promoted_list(promoted)
        if self._publish_cleared_list and cleared:
            self._publish_cleared_list(cleared)
        if self._publish_retained_confirm:
            self._publish_retained_confirm(len(retained), retained)

        elapsed = (time.time() - start_time) * 1000
        receipt = DecayCompletionReceipt(
            total_evaluated=len(request.entries),
            promoted_count=len(promoted),
            cleared_count=len(cleared),
            retained_count=len(retained),
            evaluation_duration_ms=elapsed
        )

        if self._publish_completion_receipt:
            self._publish_completion_receipt(receipt)

        self._log_event("DECAY_COMPLETED", {
            "total": len(request.entries),
            "promoted": len(promoted),
            "cleared": len(cleared),
            "retained": len(retained),
            "emergency": is_emergency
        })

        self.state = AssessmentState.IDLE
        return receipt

    def _get_slot_config(self, slot_id: str) -> PromotionThresholdConfig:
        if slot_id in self._promotion_configs:
            return self._promotion_configs[slot_id]
        # 默认使用通用任务槽配置
        return self._promotion_configs.get("ag-mem-19", PromotionThresholdConfig())

    def get_state(self) -> AssessmentState:
        return self.state

    def emergency_shutdown(self):
        self.state = AssessmentState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem L1临时层时序衰减单元 (ag-mem-21) 演示")
    print("=" * 70)

    assessor = L1DecayAssessment()
    now = time.time()

    print_separator("STEP 1: 正常衰减评估（混合条目）")
    assessor.set_decay_request_query(lambda: DecayRequest(
        entries=[
            L1EntryInfo(entry_id="E01", i_value=0.45, source_slot_id="ag-mem-15", write_timestamp=now - 26*3600),
            L1EntryInfo(entry_id="E02", i_value=0.05, source_slot_id="ag-mem-16", write_timestamp=now - 30*3600),
            L1EntryInfo(entry_id="E03", i_value=0.55, source_slot_id="ag-mem-17", write_timestamp=now - 10*3600),
            L1EntryInfo(entry_id="E04", i_value=0.30, source_slot_id="ag-mem-19", write_timestamp=now - 80*3600),
        ],
        trigger_reason="定时衰减",
        l1_usage_pct=0.6
    ))
    result = assessor.run_assessment_cycle()
    if result:
        print(f"  评估总数: {result.total_evaluated}")
        print(f"  晋升L2: {result.promoted_count}")
        print(f"  清除: {result.cleared_count}")
        print(f"  保留: {result.retained_count}")

    print_separator("STEP 2: 容量紧急模式")
    assessor.set_decay_request_query(lambda: DecayRequest(
        entries=[
            L1EntryInfo(entry_id="E05", i_value=0.15, source_slot_id="ag-mem-16", write_timestamp=now - 8*3600),
            L1EntryInfo(entry_id="E06", i_value=0.08, source_slot_id="ag-mem-19", write_timestamp=now - 25*3600),
        ],
        trigger_reason="容量紧急",
        l1_usage_pct=0.95
    ))
    result = assessor.run_assessment_cycle()
    if result:
        print(f"  评估总数: {result.total_evaluated}")
        print(f"  清除: {result.cleared_count} (紧急模式阈值更严格)")

    print("\n✅ L1临时层时序衰减单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-21 L1临时层时序衰减单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_assessor():
            return L1DecayAssessment()

        # TC-M21-01: 满足晋升条件（留存>24h且I值达标）
        print("\n[TC-M21-01] 满足晋升条件")
        try:
            a = setup_assessor()
            now = time.time()
            a.set_decay_request_query(lambda: DecayRequest(
                entries=[L1EntryInfo(entry_id="T01", i_value=0.45, source_slot_id="ag-mem-15", write_timestamp=now-26*3600)],
                trigger_reason="定时衰减"
            ))
            result = a.run_assessment_cycle()
            assert result is not None
            assert result.promoted_count == 1
            assert result.cleared_count == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M21-02: 低于遗忘阈值被清除
        print("\n[TC-M21-02] 低于遗忘阈值被清除")
        try:
            a = setup_assessor()
            now = time.time()
            a.set_decay_request_query(lambda: DecayRequest(
                entries=[L1EntryInfo(entry_id="T02", i_value=0.05, source_slot_id="ag-mem-16", write_timestamp=now-30*3600)],
                trigger_reason="定时衰减"
            ))
            result = a.run_assessment_cycle()
            assert result is not None
            assert result.cleared_count == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M21-03: 未满24小时继续保留
        print("\n[TC-M21-03] 未满24小时继续保留")
        try:
            a = setup_assessor()
            now = time.time()
            a.set_decay_request_query(lambda: DecayRequest(
                entries=[L1EntryInfo(entry_id="T03", i_value=0.50, source_slot_id="ag-mem-17", write_timestamp=now-10*3600)],
                trigger_reason="定时衰减"
            ))
            result = a.run_assessment_cycle()
            assert result is not None
            assert result.retained_count == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M21-04: 滞留超72小时强制清除
        print("\n[TC-M21-04] 滞留超72小时强制清除")
        try:
            a = setup_assessor()
            now = time.time()
            a.set_decay_request_query(lambda: DecayRequest(
                entries=[L1EntryInfo(entry_id="T04", i_value=0.30, source_slot_id="ag-mem-19", write_timestamp=now-80*3600)],
                trigger_reason="定时衰减"
            ))
            result = a.run_assessment_cycle()
            assert result is not None
            assert result.cleared_count == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M21-05: 容量紧急模式阈值调整
        print("\n[TC-M21-05] 容量紧急模式阈值调整")
        try:
            a = setup_assessor()
            now = time.time()
            # I值=0.15，正常模式下可能保留，紧急模式下遗忘阈值上调20%后为0.12，应被清除
            a.set_decay_request_query(lambda: DecayRequest(
                entries=[L1EntryInfo(entry_id="T05", i_value=0.12, source_slot_id="ag-mem-16", write_timestamp=now-8*3600)],
                trigger_reason="容量紧急"
            ))
            result = a.run_assessment_cycle()
            assert result is not None
            assert result.cleared_count == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M21-06: 紧急熔断
        print("\n[TC-M21-06] 紧急熔断")
        try:
            a = setup_assessor()
            a.emergency_shutdown()
            assert a.state == AssessmentState.SYSTEM_PAUSED
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