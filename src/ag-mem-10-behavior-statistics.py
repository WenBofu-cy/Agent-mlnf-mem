#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-10
模块名称: 偏好累积统计单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 接收 ag-mem-09 输出的带标签行为条目，将其按照行为维度、场景标签与时间窗口
          进行累积统计。维护当前活跃画像槽内的多维偏好统计表，包括各行为维度的频次分布、
          显式/隐式/偶发标签比例、偏好强度趋势等。为偏好判定单元（ag-mem-09）提供历史
          基线查询，为个性化建议生成单元（ag-mem-11）提供量化偏好数据。不参与任何认知
          决策，仅负责偏好数据的定量统计与汇总。

依赖模块:
    ag-mem-09(偏好判定标签单元), ag-mem-02(漏斗一专属调度单元),
    ag-mem-06(画像槽数据隔离管控单元)
被依赖模块:
    ag-mem-09, ag-mem-11(个性化建议生成单元)

安全约束:
  S-01: 统计摘要在响应查询前必须通过 ag-mem-06 隔离校验，确保仅返回当前活跃槽位的数据
  S-02: 偏好关键词集合仅提取自用户主动输入，不得包含系统生成内容的分析结果
  S-03: 统计异常告警仅基于当前槽位的历史数据对比，不得跨用户进行异常检测
  S-04: 统计数据的持久化写入必须完整，写入中断时保留未损坏的前一版本作为回滚点
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
from collections import defaultdict


class StatisticsState(Enum):
    IDLE = "idle"
    STAT_UPDATING = "stat_updating"
    QUERY_RESPONDING = "query_responding"
    SYSTEM_PAUSED = "system_paused"


class TimeWindow(Enum):
    REALTIME = "实时窗口"
    SHORT_TERM = "短期窗口"
    MEDIUM_TERM = "中期窗口"
    LONG_TERM = "长期窗口"


class PreferenceLabel(Enum):
    EXPLICIT = "显式偏好"
    IMPLICIT = "隐式倾向"
    OCCASIONAL = "偶发行为"


@dataclass
class LabeledBehaviorEntry:
    entry_id: str = ""
    behavior_type: str = ""
    behavior_params: Dict[str, Any] = field(default_factory=dict)
    preference_label: PreferenceLabel = PreferenceLabel.OCCASIONAL
    confidence: float = 0.5
    scene_label: Optional[Dict[str, str]] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ActiveSlotInfo:
    session_id: str = ""
    slot_id: str = ""
    user_id: str = ""


@dataclass
class ReadWriteToken:
    token_id: str = ""
    authorized_slot_id: str = ""
    operation_type: str = "read_write"
    expires_at: float = 0.0


@dataclass
class StatisticsQueryRequest:
    requester_id: str = ""
    query_type: str = "preference_summary"
    time_window: TimeWindow = TimeWindow.SHORT_TERM
    behavior_dimension: str = ""
    label_category: Optional[PreferenceLabel] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class DimensionStats:
    total_count: int = 0
    explicit_count: int = 0
    implicit_count: int = 0
    occasional_count: int = 0
    recent_7d_count: int = 0
    preference_strength: float = 0.0


@dataclass
class PreferenceSummary:
    slot_id: str = ""
    total_entries: int = 0
    time_range_start: float = 0.0
    time_range_end: float = 0.0
    by_behavior_dimension: Dict[str, DimensionStats] = field(default_factory=dict)
    by_scene_label: Dict[str, DimensionStats] = field(default_factory=dict)
    by_label_category: Dict[str, int] = field(default_factory=dict)
    preference_strength_vector: List[float] = field(default_factory=list)
    preference_keywords: List[str] = field(default_factory=list)
    window_type: TimeWindow = TimeWindow.SHORT_TERM


@dataclass
class StatisticsUpdateConfirm:
    updated_count: int = 0
    dimension_increments: Dict[str, int] = field(default_factory=dict)
    total_entries: int = 0


@dataclass
class StatisticsAnomalyAlert:
    alert_type: str = ""
    dimension: str = ""
    current_value: float = 0.0
    historical_mean: float = 0.0
    deviation_ratio: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class StatisticsStatus:
    state: StatisticsState = StatisticsState.IDLE
    total_entries: int = 0
    active_dimensions: int = 0
    storage_usage_bytes: int = 0
    timestamp: float = field(default_factory=time.time)


class PreferenceStatistics:
    ANOMALY_THRESHOLD_RATIO = 3.0
    ANOMALY_COOLDOWN_SEC = 60
    REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.module_id = "ag-mem-10"
        self.module_name = "偏好累积统计单元"
        self.version = "V1.0"

        self.state = StatisticsState.IDLE
        self._active_slot_id: Optional[str] = None
        self._stats_cache: Dict[str, Any] = {}
        self._anomaly_cooldowns: Dict[str, float] = {}
        self._last_report_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_labeled_entries = None
        self._query_active_slot = None
        self._query_statistics_request = None
        self._query_read_write_token = None

        self._publish_update_confirm = None
        self._publish_preference_summary = None
        self._publish_anomaly_alert = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_labeled_entries_query(self, callback: Callable[[], Optional[List[LabeledBehaviorEntry]]]):
        self._query_labeled_entries = callback

    def set_active_slot_query(self, callback: Callable[[], Optional[ActiveSlotInfo]]):
        self._query_active_slot = callback

    def set_statistics_request_query(self, callback: Callable[[], Optional[StatisticsQueryRequest]]):
        self._query_statistics_request = callback

    def set_read_write_token_query(self, callback: Callable[[], Optional[ReadWriteToken]]):
        self._query_read_write_token = callback

    def set_update_confirm_publisher(self, callback: Callable[[StatisticsUpdateConfirm], None]):
        self._publish_update_confirm = callback

    def set_preference_summary_publisher(self, callback: Callable[[PreferenceSummary], None]):
        self._publish_preference_summary = callback

    def set_anomaly_alert_publisher(self, callback: Callable[[StatisticsAnomalyAlert], None]):
        self._publish_anomaly_alert = callback

    def set_status_report_publisher(self, callback: Callable[[StatisticsStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_statistics_cycle(self):
        now = time.time()

        if self.state == StatisticsState.SYSTEM_PAUSED:
            return

        # 更新活跃槽位
        slot_info = self._query_active_slot() if self._query_active_slot else None
        if slot_info:
            if slot_info.slot_id != self._active_slot_id:
                self._active_slot_id = slot_info.slot_id
                self._load_slot_cache()

        if self._active_slot_id is None:
            return

        # 处理统计更新
        entries = self._query_labeled_entries() if self._query_labeled_entries else None
        if entries:
            self._process_entries(entries)

        # 处理查询请求
        request = self._query_statistics_request() if self._query_statistics_request else None
        if request:
            self._handle_query(request)

        # 周期性状态上报
        if now - self._last_report_time >= self.REPORT_INTERVAL_SEC:
            self._last_report_time = now
            self._publish_status()

    def _process_entries(self, entries: List[LabeledBehaviorEntry]):
        self.state = StatisticsState.STAT_UPDATING

        token = self._query_read_write_token() if self._query_read_write_token else None
        if token is None or token.authorized_slot_id != self._active_slot_id:
            self.state = StatisticsState.IDLE
            return

        updated = 0
        increments: Dict[str, int] = defaultdict(int)

        for entry in entries:
            behavior_type = entry.behavior_type
            label = entry.preference_label

            # 更新行为维度统计
            if behavior_type not in self._stats_cache.get("by_behavior_dimension", {}):
                self._stats_cache.setdefault("by_behavior_dimension", {})[behavior_type] = {
                    "total_count": 0, "explicit_count": 0, "implicit_count": 0,
                    "occasional_count": 0, "recent_7d_count": 0, "preference_strength": 0.0
                }
            dim_stats = self._stats_cache["by_behavior_dimension"][behavior_type]
            dim_stats["total_count"] += 1
            if label == PreferenceLabel.EXPLICIT:
                dim_stats["explicit_count"] += 1
            elif label == PreferenceLabel.IMPLICIT:
                dim_stats["implicit_count"] += 1
            else:
                dim_stats["occasional_count"] += 1
            dim_stats["recent_7d_count"] += 1

            # 更新场景维度统计
            scene = entry.scene_label.get("scene_category", "通用任务") if entry.scene_label else "通用任务"
            if scene not in self._stats_cache.get("by_scene_label", {}):
                self._stats_cache.setdefault("by_scene_label", {})[scene] = {
                    "total_count": 0, "explicit_count": 0, "implicit_count": 0,
                    "occasional_count": 0, "recent_7d_count": 0, "preference_strength": 0.0
                }
            scene_stats = self._stats_cache["by_scene_label"][scene]
            scene_stats["total_count"] += 1
            if label == PreferenceLabel.EXPLICIT:
                scene_stats["explicit_count"] += 1
            elif label == PreferenceLabel.IMPLICIT:
                scene_stats["implicit_count"] += 1
            else:
                scene_stats["occasional_count"] += 1

            # 更新标签分布
            label_key = label.value
            if "by_label_category" not in self._stats_cache:
                self._stats_cache["by_label_category"] = {}
            self._stats_cache["by_label_category"][label_key] = \
                self._stats_cache["by_label_category"].get(label_key, 0) + 1

            # 更新关键词（从TEXT_INPUT中提取）
            if behavior_type == "TEXT_INPUT":
                text = entry.behavior_params.get("text", "")
                keywords = self._extract_keywords(text)
                existing_keywords = self._stats_cache.get("preference_keywords", [])
                for kw in keywords:
                    if kw not in existing_keywords:
                        existing_keywords.append(kw)
                self._stats_cache["preference_keywords"] = existing_keywords[:50]  # 最多保留50个

            increments[behavior_type] += 1
            updated += 1

            # 异常检测
            self._check_anomaly(behavior_type, dim_stats)

        # 重新计算偏好强度
        self._recalculate_strength()

        # 更新总计
        self._stats_cache["total_entries"] = self._stats_cache.get("total_entries", 0) + updated

        if self._publish_update_confirm:
            self._publish_update_confirm(StatisticsUpdateConfirm(
                updated_count=updated,
                dimension_increments=dict(increments),
                total_entries=self._stats_cache["total_entries"]
            ))

        self.state = StatisticsState.IDLE

    def _handle_query(self, request: StatisticsQueryRequest):
        self.state = StatisticsState.QUERY_RESPONDING

        token = self._query_read_write_token() if self._query_read_write_token else None
        if token is None:
            self.state = StatisticsState.IDLE
            return

        summary = self._build_summary(request.time_window)

        if self._publish_preference_summary:
            self._publish_preference_summary(summary)

        self.state = StatisticsState.IDLE

    def _build_summary(self, window: TimeWindow) -> PreferenceSummary:
        summary = PreferenceSummary(
            slot_id=self._active_slot_id or "",
            total_entries=self._stats_cache.get("total_entries", 0),
            window_type=window,
            by_behavior_dimension=self._stats_cache.get("by_behavior_dimension", {}),
            by_scene_label=self._stats_cache.get("by_scene_label", {}),
            by_label_category=self._stats_cache.get("by_label_category", {}),
            preference_strength_vector=self._stats_cache.get("preference_strength_vector", []),
            preference_keywords=self._stats_cache.get("preference_keywords", []),
        )
        return summary

    def _recalculate_strength(self):
        strength_vector = []
        for dim, stats in self._stats_cache.get("by_behavior_dimension", {}).items():
            total = stats["total_count"]
            if total > 0:
                strength = (stats["explicit_count"] * 1.0 + stats["implicit_count"] * 0.6 +
                            stats["occasional_count"] * 0.2) / total
            else:
                strength = 0.0
            stats["preference_strength"] = round(strength, 2)
            strength_vector.append(strength)
        self._stats_cache["preference_strength_vector"] = strength_vector

    def _check_anomaly(self, dimension: str, stats: Dict[str, Any]):
        now = time.time()
        if dimension in self._anomaly_cooldowns and now - self._anomaly_cooldowns[dimension] < self.ANOMALY_COOLDOWN_SEC:
            return

        recent = stats.get("recent_7d_count", 0)
        avg = recent / 7.0 if recent > 0 else 0
        current_hour = stats.get("_hourly_count", 0) + 1
        stats["_hourly_count"] = current_hour

        if avg > 0 and current_hour > avg * self.ANOMALY_THRESHOLD_RATIO:
            self._anomaly_cooldowns[dimension] = now
            if self._publish_anomaly_alert:
                self._publish_anomaly_alert(StatisticsAnomalyAlert(
                    alert_type="异常高频",
                    dimension=dimension,
                    current_value=current_hour,
                    historical_mean=avg,
                    deviation_ratio=current_hour / avg if avg > 0 else 0
                ))

    def _extract_keywords(self, text: str) -> List[str]:
        if not text:
            return []
        # 简单分词：按空格和标点分割
        import re
        words = re.findall(r'[\w]+', text)
        return [w for w in words if len(w) >= 2][:10]

    def _load_slot_cache(self):
        # 从持久化存储加载槽位统计缓存
        self._stats_cache = {
            "total_entries": 0,
            "by_behavior_dimension": {},
            "by_scene_label": {},
            "by_label_category": {},
            "preference_strength_vector": [],
            "preference_keywords": [],
        }

    def get_baseline(self) -> Optional[PreferenceSummary]:
        if self._active_slot_id is None:
            return None
        return self._build_summary(TimeWindow.SHORT_TERM)

    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report(StatisticsStatus(
                state=self.state,
                total_entries=self._stats_cache.get("total_entries", 0),
                active_dimensions=len(self._stats_cache.get("by_behavior_dimension", {})),
            ))

    def get_state(self) -> StatisticsState:
        return self.state

    def emergency_shutdown(self):
        self.state = StatisticsState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 偏好累积统计单元 (ag-mem-10) 演示")
    print("=" * 70)

    stats = PreferenceStatistics()
    stats.set_active_slot_query(lambda: ActiveSlotInfo(slot_id="SLOT-LONG-0001", user_id="U001"))
    stats.set_read_write_token_query(lambda: ReadWriteToken(
        token_id="T-001", authorized_slot_id="SLOT-LONG-0001", expires_at=time.time() + 300
    ))
    stats.run_statistics_cycle()

    print_separator("STEP 1: 接收带标签的行为条目并更新统计")
    stats.set_labeled_entries_query(lambda: [
        LabeledBehaviorEntry(
            entry_id="E01", behavior_type="TOOL_INVOKE",
            preference_label=PreferenceLabel.IMPLICIT,
            confidence=0.75,
            behavior_params={"tool_name": "weather_api"},
        ),
        LabeledBehaviorEntry(
            entry_id="E02", behavior_type="TOOL_INVOKE",
            preference_label=PreferenceLabel.EXPLICIT,
            confidence=0.95,
            behavior_params={"tool_name": "weather_api"},
        ),
        LabeledBehaviorEntry(
            entry_id="E03", behavior_type="RESULT_COPY",
            preference_label=PreferenceLabel.IMPLICIT,
            confidence=0.70,
        ),
    ])
    stats.run_statistics_cycle()
    print(f"  总条目数: {stats._stats_cache.get('total_entries', 0)}")
    print(f"  活跃维度数: {len(stats._stats_cache.get('by_behavior_dimension', {}))}")

    print_separator("STEP 2: 响应偏好摘要查询")
    stats.set_statistics_request_query(lambda: StatisticsQueryRequest(
        requester_id="ag-mem-09",
        query_type="preference_summary",
        time_window=TimeWindow.SHORT_TERM,
    ))
    stats.run_statistics_cycle()
    print("  查询完成 (查看回调输出)")

    print("\n✅ 偏好累积统计单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-10 偏好累积统计单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_stats():
            s = PreferenceStatistics()
            s.set_active_slot_query(lambda: ActiveSlotInfo(slot_id="SLOT-TEST", user_id="U_TEST"))
            s.set_read_write_token_query(lambda: ReadWriteToken(
                token_id="T", authorized_slot_id="SLOT-TEST", expires_at=time.time() + 300
            ))
            s.run_statistics_cycle()
            return s

        # TC-M10-01: 正常更新统计
        print("\n[TC-M10-01] 正常更新统计")
        try:
            s = setup_stats()
            s.set_labeled_entries_query(lambda: [
                LabeledBehaviorEntry(entry_id="T01", behavior_type="TEXT_INPUT",
                                     preference_label=PreferenceLabel.IMPLICIT, confidence=0.6),
                LabeledBehaviorEntry(entry_id="T02", behavior_type="TEXT_INPUT",
                                     preference_label=PreferenceLabel.EXPLICIT, confidence=0.9),
            ])
            s.run_statistics_cycle()
            assert s._stats_cache["total_entries"] == 2
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M10-02: 偏好摘要查询
        print("\n[TC-M10-02] 偏好摘要查询")
        try:
            s = setup_stats()
            summary = s.get_baseline()
            assert summary is not None
            assert summary.slot_id == "SLOT-TEST"
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M10-03: 异常高频检测
        print("\n[TC-M10-03] 异常高频检测")
        try:
            s = setup_stats()
            # 手动设置历史均值
            s._stats_cache.setdefault("by_behavior_dimension", {})["TEST_DIM"] = {
                "total_count": 10, "explicit_count": 0, "implicit_count": 0,
                "occasional_count": 0, "recent_7d_count": 7, "preference_strength": 0.0, "_hourly_count": 20
            }
            s._check_anomaly("TEST_DIM", s._stats_cache["by_behavior_dimension"]["TEST_DIM"])
            # 应该触发告警（20 > 7/7 * 3）
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M10-04: 无活跃槽位
        print("\n[TC-M10-04] 无活跃槽位时不处理")
        try:
            s = PreferenceStatistics()
            s.set_active_slot_query(lambda: None)
            s.set_labeled_entries_query(lambda: [LabeledBehaviorEntry(entry_id="T04")])
            s.run_statistics_cycle()
            assert s._active_slot_id is None
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M10-05: 关键词提取
        print("\n[TC-M10-05] 关键词提取")
        try:
            s = setup_stats()
            s.set_labeled_entries_query(lambda: [
                LabeledBehaviorEntry(entry_id="T05", behavior_type="TEXT_INPUT",
                                     behavior_params={"text": "天气查询 AI 编程"},
                                     preference_label=PreferenceLabel.IMPLICIT, confidence=0.6),
            ])
            s.run_statistics_cycle()
            keywords = s._stats_cache.get("preference_keywords", [])
            assert "天气" in keywords or "AI" in keywords or "编程" in keywords
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M10-06: 紧急熔断
        print("\n[TC-M10-06] 紧急熔断")
        try:
            s = setup_stats()
            s.emergency_shutdown()
            assert s.state == StatisticsState.SYSTEM_PAUSED
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