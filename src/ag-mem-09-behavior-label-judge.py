#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-09
模块名称: 偏好判定标签单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 接收 ag-mem-07 输出的结构化行为观测条目，将其与当前活跃画像槽中的历史偏好
          基线进行比对。根据用户交互的显式反馈信号、行为频率及模式一致性，将每条行为
          判定为“显式偏好”、“隐式倾向”或“偶发行为”三类标签之一。将带标签的行为条目
          输出至 ag-mem-10 供累积统计使用。不参与任何认知决策，仅负责行为标签的客观判定。

依赖模块:
    ag-mem-07(用户行为观测记录单元), ag-mem-10(偏好累积统计单元),
    ag-mem-02(漏斗一专属调度单元), ag-mem-06(画像槽数据隔离管控单元)
被依赖模块:
    ag-mem-10(偏好累积统计单元), ag-mem-11(个性化建议生成单元)

安全约束:
  S-01: 判定过程中获取的历史偏好统计摘要为聚合数据，不得包含其他用户的任何信息
  S-02: 输入内容的关键词匹配仅基于已脱敏的偏好关键词集合，不得存储或分析原始输入文本
  S-03: 低置信度标签（<0.5）不得触发任何自动化偏好更新，仅作为统计参考
  S-04: 本模块仅附加标签，不修改原始行为观测条目的任何字段
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class JudgmentState(Enum):
    IDLE = "idle"
    JUDGING = "judging"
    INSUFFICIENT_BASELINE = "insufficient_baseline"
    SYSTEM_PAUSED = "system_paused"


class PreferenceLabel(Enum):
    EXPLICIT = "显式偏好"
    IMPLICIT = "隐式倾向"
    OCCASIONAL = "偶发行为"


@dataclass
class BehaviorEntry:
    entry_id: str = ""
    behavior_type: str = ""
    behavior_params: Dict[str, Any] = field(default_factory=dict)
    scene_label: Optional[Dict[str, str]] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ActiveSlotInfo:
    session_id: str = ""
    slot_id: str = ""
    user_id: str = ""


@dataclass
class PreferenceBaseline:
    slot_id: str = ""
    total_entries: int = 0
    behavior_frequencies: Dict[str, int] = field(default_factory=dict)
    label_distribution: Dict[str, int] = field(default_factory=dict)
    preference_keywords: List[str] = field(default_factory=list)
    baseline_available: bool = False


@dataclass
class ReadToken:
    token_id: str = ""
    authorized_slot_id: str = ""
    expires_at: float = 0.0


@dataclass
class LabeledBehaviorEntry:
    entry_id: str = ""
    behavior_type: str = ""
    behavior_params: Dict[str, Any] = field(default_factory=dict)
    preference_label: PreferenceLabel = PreferenceLabel.OCCASIONAL
    confidence: float = 0.5
    judgment_basis: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class JudgmentStatus:
    state: JudgmentState = JudgmentState.IDLE
    total_judged: int = 0
    label_distribution: Dict[str, int] = field(default_factory=dict)
    baseline_availability: float = 0.0


class PreferenceLabelJudge:
    # 高频复用阈值（7日内调用次数）
    HIGH_FREQ_THRESHOLD = 5
    # 连续跳过阈值
    CONSECUTIVE_SKIP_THRESHOLD = 3
    # 重试阈值
    RETRY_THRESHOLD = 2
    # 查看时长超标比例
    VIEW_DURATION_RATIO = 1.5

    def __init__(self):
        self.module_id = "ag-mem-09"
        self.module_name = "偏好判定标签单元"
        self.version = "V1.0"

        self.state = JudgmentState.IDLE
        self._total_judged: int = 0
        self._label_counts = {label: 0 for label in PreferenceLabel}
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_behavior_entries = None
        self._query_active_slot = None
        self._query_preference_baseline = None
        self._query_read_token = None

        self._publish_labeled_entry = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_behavior_entries_query(self, callback: Callable[[], Optional[List[BehaviorEntry]]]):
        self._query_behavior_entries = callback

    def set_active_slot_query(self, callback: Callable[[], Optional[ActiveSlotInfo]]):
        self._query_active_slot = callback

    def set_preference_baseline_query(self, callback: Callable[[], Optional[PreferenceBaseline]]):
        self._query_preference_baseline = callback

    def set_read_token_query(self, callback: Callable[[], Optional[ReadToken]]):
        self._query_read_token = callback

    def set_labeled_entry_publisher(self, callback: Callable[[LabeledBehaviorEntry], None]):
        self._publish_labeled_entry = callback

    def set_status_report_publisher(self, callback: Callable[[JudgmentStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_judgment_cycle(self):
        if self.state == JudgmentState.SYSTEM_PAUSED:
            return

        entries = self._query_behavior_entries() if self._query_behavior_entries else None
        if entries is None or len(entries) == 0:
            return

        self.state = JudgmentState.JUDGING

        # 获取读取权限
        token = self._query_read_token() if self._query_read_token else None
        if token is None:
            self.state = JudgmentState.INSUFFICIENT_BASELINE
            # 无法获取令牌时，使用保守标签
            for entry in entries:
                self._publish_conservative_label(entry)
            self.state = JudgmentState.IDLE
            return

        # 获取历史偏好基线
        baseline = self._query_preference_baseline() if self._query_preference_baseline else None
        if baseline is None or not baseline.baseline_available:
            self.state = JudgmentState.INSUFFICIENT_BASELINE
            for entry in entries:
                self._publish_new_user_label(entry)
            self.state = JudgmentState.IDLE
            return

        # 逐条判定
        for entry in entries:
            label, confidence, basis = self._judge_entry(entry, baseline)
            labeled = LabeledBehaviorEntry(
                entry_id=entry.entry_id,
                behavior_type=entry.behavior_type,
                behavior_params=entry.behavior_params,
                preference_label=label,
                confidence=confidence,
                judgment_basis=basis,
                timestamp=entry.timestamp
            )
            self._total_judged += 1
            self._label_counts[label] += 1

            if self._publish_labeled_entry:
                self._publish_labeled_entry(labeled)

        self.state = JudgmentState.IDLE

    def _judge_entry(self, entry: BehaviorEntry, baseline: PreferenceBaseline) -> Tuple[PreferenceLabel, float, str]:
        behavior_type = entry.behavior_type
        params = entry.behavior_params

        # 规则1：显式正向反馈
        if behavior_type in ("FEEDBACK_LIKE", "FEEDBACK_DISLIKE"):
            return PreferenceLabel.EXPLICIT, 0.95, "用户主动给出反馈"

        # 规则2：用户明确要求记住
        if params.get("save_preference", False):
            return PreferenceLabel.EXPLICIT, 0.90, "用户明确要求记住偏好"

        # 规则3：高频工具调用
        if behavior_type == "TOOL_INVOKE":
            tool_name = params.get("tool_name", "")
            call_count = baseline.behavior_frequencies.get(f"TOOL_INVOKE_{tool_name}", 0)
            if call_count >= self.HIGH_FREQ_THRESHOLD:
                return PreferenceLabel.IMPLICIT, 0.75, f"近7日该工具调用≥{self.HIGH_FREQ_THRESHOLD}次"

        # 规则4：复制/分享
        if behavior_type == "RESULT_COPY":
            return PreferenceLabel.IMPLICIT, 0.70, "用户复制了结果内容"
        if behavior_type == "RESULT_SHARE":
            return PreferenceLabel.IMPLICIT, 0.80, "用户分享了结果"

        # 规则5：长时间查看
        if behavior_type == "RESULT_VIEW":
            view_duration = params.get("view_duration", 0)
            avg_duration = params.get("historical_avg_duration", 0)
            if avg_duration > 0 and view_duration > avg_duration * self.VIEW_DURATION_RATIO:
                return PreferenceLabel.IMPLICIT, 0.65, "查看时长超过历史均值150%"

        # 规则6：连续跳过
        if behavior_type == "FEEDBACK_SKIP":
            skip_count = baseline.behavior_frequencies.get("FEEDBACK_SKIP", 0)
            if skip_count >= self.CONSECUTIVE_SKIP_THRESHOLD:
                return PreferenceLabel.IMPLICIT, 0.60, f"连续跳过同类内容≥{self.CONSECUTIVE_SKIP_THRESHOLD}次"

        # 规则7：重复重试
        if behavior_type == "RETRY_ACTION":
            retry_count = params.get("retry_count", 0)
            if retry_count >= self.RETRY_THRESHOLD:
                return PreferenceLabel.IMPLICIT, 0.70, "用户重复尝试同一操作"

        # 规则8：输入关键词匹配历史偏好
        if behavior_type == "TEXT_INPUT":
            text = params.get("text", "")
            keywords = baseline.preference_keywords
            if keywords and any(kw in text for kw in keywords):
                return PreferenceLabel.IMPLICIT, 0.55, "输入内容与历史偏好关键词匹配"

        # 规则9：首次出现
        if baseline.behavior_frequencies.get(behavior_type, 0) == 0:
            return PreferenceLabel.OCCASIONAL, 0.40, "首次出现的用户行为"

        # 默认：偶发行为
        return PreferenceLabel.OCCASIONAL, 0.50, "与历史基线无显著相关性"

    def _publish_conservative_label(self, entry: BehaviorEntry):
        labeled = LabeledBehaviorEntry(
            entry_id=entry.entry_id,
            behavior_type=entry.behavior_type,
            behavior_params=entry.behavior_params,
            preference_label=PreferenceLabel.OCCASIONAL,
            confidence=0.30,
            judgment_basis="无法获取画像槽读取权限"
        )
        if self._publish_labeled_entry:
            self._publish_labeled_entry(labeled)

    def _publish_new_user_label(self, entry: BehaviorEntry):
        labeled = LabeledBehaviorEntry(
            entry_id=entry.entry_id,
            behavior_type=entry.behavior_type,
            behavior_params=entry.behavior_params,
            preference_label=PreferenceLabel.OCCASIONAL,
            confidence=0.40,
            judgment_basis="新用户，历史偏好基线不足"
        )
        if self._publish_labeled_entry:
            self._publish_labeled_entry(labeled)

    def get_state(self) -> JudgmentState:
        return self.state

    def emergency_shutdown(self):
        self.state = JudgmentState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 偏好判定标签单元 (ag-mem-09) 演示")
    print("=" * 70)

    judge = PreferenceLabelJudge()
    judge.set_read_token_query(lambda: ReadToken(token_id="T-001", authorized_slot_id="SLOT-001", expires_at=time.time()+300))
    judge.set_preference_baseline_query(lambda: PreferenceBaseline(
        slot_id="SLOT-001",
        total_entries=100,
        behavior_frequencies={"TOOL_INVOKE_weather_api": 6, "FEEDBACK_SKIP": 4},
        preference_keywords=["天气", "AI", "编程"],
        baseline_available=True
    ))

    print_separator("STEP 1: 显式偏好（点赞）")
    judge.set_behavior_entries_query(lambda: [
        BehaviorEntry(entry_id="E01", behavior_type="FEEDBACK_LIKE")
    ])
    judge.run_judgment_cycle()
    print(f"  已判定条目数: {judge._total_judged}")

    print_separator("STEP 2: 隐式倾向（高频工具调用）")
    judge.set_behavior_entries_query(lambda: [
        BehaviorEntry(entry_id="E02", behavior_type="TOOL_INVOKE",
                      behavior_params={"tool_name": "weather_api"})
    ])
    judge.run_judgment_cycle()
    print(f"  已判定条目数: {judge._total_judged}")

    print_separator("STEP 3: 偶发行为（首次出现）")
    judge.set_behavior_entries_query(lambda: [
        BehaviorEntry(entry_id="E03", behavior_type="BUTTON_CLICK")
    ])
    judge.run_judgment_cycle()
    print(f"  已判定条目数: {judge._total_judged}")
    print(f"  标签分布: { {k.value: v for k, v in judge._label_counts.items()} }")

    print("\n✅ 偏好判定标签单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-09 偏好判定标签单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_judge(with_baseline=True):
            j = PreferenceLabelJudge()
            j.set_read_token_query(lambda: ReadToken(token_id="T", authorized_slot_id="S", expires_at=time.time()+300))
            if with_baseline:
                j.set_preference_baseline_query(lambda: PreferenceBaseline(
                    slot_id="S", total_entries=100,
                    behavior_frequencies={"TOOL_INVOKE_weather_api": 6, "FEEDBACK_SKIP": 4},
                    preference_keywords=["AI"], baseline_available=True
                ))
            else:
                j.set_preference_baseline_query(lambda: PreferenceBaseline(baseline_available=False))
            return j

        # TC-M09-01: 显式正向反馈
        print("\n[TC-M09-01] 显式正向反馈")
        try:
            j = setup_judge()
            j.set_behavior_entries_query(lambda: [BehaviorEntry(entry_id="T01", behavior_type="FEEDBACK_LIKE")])
            j.run_judgment_cycle()
            assert j._total_judged == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M09-02: 高频工具调用 -> 隐式倾向
        print("\n[TC-M09-02] 高频工具调用")
        try:
            j = setup_judge()
            j.set_behavior_entries_query(lambda: [BehaviorEntry(entry_id="T02", behavior_type="TOOL_INVOKE",
                                             behavior_params={"tool_name": "weather_api"})])
            j.run_judgment_cycle()
            assert j._total_judged == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M09-03: 新用户基线不足
        print("\n[TC-M09-03] 新用户基线不足")
        try:
            j = setup_judge(with_baseline=False)
            j.set_behavior_entries_query(lambda: [BehaviorEntry(entry_id="T03", behavior_type="TEXT_INPUT")])
            j.run_judgment_cycle()
            assert j._total_judged == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M09-04: 复制结果 -> 隐式倾向
        print("\n[TC-M09-04] 复制结果")
        try:
            j = setup_judge()
            j.set_behavior_entries_query(lambda: [BehaviorEntry(entry_id="T04", behavior_type="RESULT_COPY")])
            j.run_judgment_cycle()
            assert j._total_judged == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M09-05: 无法获取读取权限
        print("\n[TC-M09-05] 无法获取读取权限")
        try:
            j = PreferenceLabelJudge()
            j.set_read_token_query(lambda: None)
            j.set_behavior_entries_query(lambda: [BehaviorEntry(entry_id="T05", behavior_type="TEXT_INPUT")])
            j.run_judgment_cycle()
            assert j.state == JudgmentState.INSUFFICIENT_BASELINE
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M09-06: 紧急熔断
        print("\n[TC-M09-06] 紧急熔断")
        try:
            j = setup_judge()
            j.emergency_shutdown()
            assert j.state == JudgmentState.SYSTEM_PAUSED
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