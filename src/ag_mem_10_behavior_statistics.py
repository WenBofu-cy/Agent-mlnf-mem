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

版本: V1.0 (100% 兼容 ag-mem-09 中文枚举版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class StatisticsState(Enum):
    IDLE = "idle"
    STAT_UPDATING = "stat_updating"
    QUERY_RESPONDING = "query_responding"
    SYSTEM_PAUSED = "system_paused"


class PreferenceStatistics:
    module_id = "ag-mem-10"
    module_name = "偏好累积统计单元"
    version = "V1.0"

    ANOMALY_THRESHOLD_RATIO = 3.0
    ANOMALY_COOLDOWN_SEC = 60
    REPORT_INTERVAL_SEC = 60
    HOURLY_RESET_INTERVAL = 3600
    DAILY_RESET_INTERVAL = 86400
    RECENT_WINDOW_DAYS = 7

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = StatisticsState.IDLE
        self._active_slot_id: Optional[str] = None
        self._stats_cache: Dict[str, Any] = {}
        self._anomaly_cooldowns: Dict[str, float] = {}
        self._last_report_time: float = 0.0
        self._last_hourly_reset: float = time.time()
        self._last_daily_reset: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 全系统统一主循环入口 ======================
    def run_cycle(self):
        self.preference_statistics_main_loop()

    def preference_statistics_main_loop(self):
        if self.state == StatisticsState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        if now - self._last_hourly_reset >= self.HOURLY_RESET_INTERVAL:
            self._reset_hourly_counters()
            self._last_hourly_reset = now

        if now - self._last_daily_reset >= self.DAILY_RESET_INTERVAL:
            self._rotate_daily_windows()
            self._last_daily_reset = now

        if now - self._last_report_time >= self.REPORT_INTERVAL_SEC:
            self._last_report_time = now
            self._report_status()

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-10.preference_label":
            self._handle_labeled_entry(msg.data)
            return

        if msg.topic == "ag-mem-10.active_slot":
            new_slot = msg.data.get("slot_id")
            if new_slot and new_slot != self._active_slot_id:
                self._active_slot_id = new_slot
                self._load_slot_cache()
            return

        if msg.topic == "ag-mem-10.summary_query":
            self._handle_summary_query(msg)
            return

        if msg.topic == "ag-mem-10.baseline_query":
            self._handle_baseline_query(msg)
            return

    def _handle_labeled_entry(self, data: Dict[str, Any]):
        self.state = StatisticsState.STAT_UPDATING

        # 提取ag-mem-09输出的标签信息（中文枚举值）
        preference_type = data.get("preference_type", "")
        label_dimension = data.get("label_dimension", "")
        label_value = data.get("label_value", "")
        scene_category = data.get("scene_category", "通用任务")
        user_id = data.get("user_id", "")

        # 优先使用ag-mem-09透传的原始行为字段
        behavior_type = data.get("behavior_type", "")
        behavior_params = data.get("behavior_params", {})

        # 确定统计维度：优先使用原始行为类型，否则回退到label_dimension
        stat_dimension = behavior_type if behavior_type else label_dimension
        if not stat_dimension:
            self.state = StatisticsState.IDLE
            return

        self._ensure_cache_initialized()

        dim = self._stats_cache["by_behavior_dimension"]
        if stat_dimension not in dim:
            dim[stat_dimension] = {
                "total_count": 0, "explicit_count": 0, "implicit_count": 0,
                "occasional_count": 0, "negative_count": 0,
                "daily_counts": [0] * self.RECENT_WINDOW_DAYS,
                "preference_strength": 0.0,
                "tool_freq_map": {}, "view_duration_map": {},
                "_hourly_count": 0
            }
        s = dim[stat_dimension]
        s["total_count"] += 1
        s["daily_counts"][0] += 1  # 今日计数
        s["_hourly_count"] += 1

        # 按中文枚举值统计（与ag-mem-09完全对齐）
        if preference_type == "显式偏好":
            s["explicit_count"] += 1
        elif preference_type == "隐式倾向":
            s["implicit_count"] += 1
        elif preference_type == "负面偏好":
            s["negative_count"] += 1
        else:
            s["occasional_count"] += 1

        # 根据原始行为类型补充特定统计
        if behavior_type == "tool_invoke":
            tool_name = behavior_params.get("tool_name", "")
            if tool_name:
                s["tool_freq_map"][tool_name] = s["tool_freq_map"].get(tool_name, 0) + 1
        elif behavior_type == "result_view":
            view_duration = behavior_params.get("view_duration", 0)
            if view_duration > 0:
                s["view_duration_map"]["total"] = s["view_duration_map"].get("total", 0) + view_duration
                s["view_duration_map"]["count"] = s["view_duration_map"].get("count", 0) + 1

        # 场景维度统计
        scene_dim = self._stats_cache["by_scene_label"]
        if scene_category not in scene_dim:
            scene_dim[scene_category] = {
                "total_count": 0, "explicit_count": 0, "implicit_count": 0,
                "occasional_count": 0, "negative_count": 0,
                "daily_counts": [0] * self.RECENT_WINDOW_DAYS,
                "preference_strength": 0.0,
                "view_duration_map": {}
            }
        ss = scene_dim[scene_category]
        ss["total_count"] += 1
        ss["daily_counts"][0] += 1
        if preference_type == "显式偏好":
            ss["explicit_count"] += 1
        elif preference_type == "隐式倾向":
            ss["implicit_count"] += 1
        elif preference_type == "负面偏好":
            ss["negative_count"] += 1
        else:
            ss["occasional_count"] += 1

        # 场景查看时长统计
        if behavior_type == "result_view":
            view_duration = behavior_params.get("view_duration", 0)
            if view_duration > 0:
                ss.setdefault("view_duration_map", {})
                ss["view_duration_map"]["total"] = ss["view_duration_map"].get("total", 0) + view_duration
                ss["view_duration_map"]["count"] = ss["view_duration_map"].get("count", 0) + 1

        # 标签分布
        self._stats_cache["by_label_category"][preference_type] = \
            self._stats_cache["by_label_category"].get(preference_type, 0) + 1

        # 跳过计数
        if preference_type == "负面偏好" and ("skip" in label_dimension or behavior_type == "feedback_skip"):
            self._stats_cache["skip_count"] = self._stats_cache.get("skip_count", 0) + 1

        self._stats_cache["total_entries"] = self._stats_cache.get("total_entries", 0) + 1

        self._recalculate_strength()
        self._check_anomaly(stat_dimension, s)

        self.state = StatisticsState.IDLE

    def _handle_summary_query(self, msg: Message):
        """处理统计摘要查询请求（S-01 强制隔离校验）"""
        self.state = StatisticsState.QUERY_RESPONDING

        # S-01 隔离校验：仅返回当前活跃槽位的数据
        if not self._active_slot_id:
            summary = {"slot_id": "", "baseline_available": False}
        else:
            summary = self._build_summary()

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.preference_summary",
                source_module=self.module_id,
                data=summary,
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        self.state = StatisticsState.IDLE

    def _handle_baseline_query(self, msg: Message):
        """处理历史基线查询请求（S-01 强制隔离校验）"""
        self.state = StatisticsState.QUERY_RESPONDING

        # S-01 隔离校验：仅返回当前活跃槽位的数据
        if not self._active_slot_id:
            baseline = {"avg_view_duration": {}, "behavior_frequencies": {}, "skip_count": 0}
        else:
            baseline = self._build_baseline()

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.preference_baseline",
                source_module=self.module_id,
                data=baseline,
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        self.state = StatisticsState.IDLE

    def _ensure_cache_initialized(self):
        defaults = {
            "total_entries": 0,
            "by_behavior_dimension": {},
            "by_scene_label": {},
            "by_label_category": {},
            "preference_strength_vector": [],
            "preference_keywords": [],
            "skip_count": 0,
        }
        for key, default in defaults.items():
            if key not in self._stats_cache:
                self._stats_cache[key] = default

    def _build_summary(self) -> Dict[str, Any]:
        behavior_dim = {}
        for dim_name, stats in self._stats_cache.get("by_behavior_dimension", {}).items():
            behavior_dim[dim_name] = {
                k: v for k, v in stats.items()
                if not k.startswith("_") and k not in ("tool_freq_map", "view_duration_map", "daily_counts")
            }
            # 计算最近7天总数
            behavior_dim[dim_name]["recent_7d_count"] = sum(stats.get("daily_counts", [0]*7))

        scene_dim = {}
        for scene_name, stats in self._stats_cache.get("by_scene_label", {}).items():
            scene_dim[scene_name] = {
                k: v for k, v in stats.items()
                if not k.startswith("_") and k not in ("view_duration_map", "daily_counts")
            }
            scene_dim[scene_name]["recent_7d_count"] = sum(stats.get("daily_counts", [0]*7))

        return {
            "slot_id": self._active_slot_id or "",
            "total_entries": self._stats_cache.get("total_entries", 0),
            "by_behavior_dimension": behavior_dim,
            "by_scene_label": scene_dim,
            "by_label_category": self._stats_cache.get("by_label_category", {}),
            "preference_strength_vector": self._stats_cache.get("preference_strength_vector", []),
            "preference_keywords": self._stats_cache.get("preference_keywords", []),
            "baseline_available": self._stats_cache.get("total_entries", 0) > 0,
        }

    def _build_baseline(self) -> Dict[str, Any]:
        # 平均查看时长：按场景类别聚合（与ag-mem-09对齐）
        avg_view_duration = {}
        for scene, stats in self._stats_cache.get("by_scene_label", {}).items():
            vmap = stats.get("view_duration_map", {})
            if vmap.get("count", 0) > 0:
                avg_view_duration[scene] = vmap["total"] / vmap["count"]

        # 行为频次：汇总所有维度中的工具频次
        behavior_frequencies = {}
        for dim_name, stats in self._stats_cache.get("by_behavior_dimension", {}).items():
            if dim_name == "tool_invoke":
                for tool, freq in stats.get("tool_freq_map", {}).items():
                    behavior_frequencies[f"TOOL_INVOKE_{tool}"] = freq

        skip_count = self._stats_cache.get("skip_count", 0)

        return {
            "avg_view_duration": avg_view_duration,
            "behavior_frequencies": behavior_frequencies,
            "skip_count": skip_count,
        }

    def _recalculate_strength(self):
        strength_vector = []
        for dim, stats in self._stats_cache.get("by_behavior_dimension", {}).items():
            total = stats["total_count"]
            if total > 0:
                strength = (stats["explicit_count"] * 1.0 +
                            stats["implicit_count"] * 0.6 +
                            stats["occasional_count"] * 0.2 -
                            stats["negative_count"] * 0.8) / total
                strength = max(0.0, min(1.0, strength))  # 限制在0-1之间
            else:
                strength = 0.0
            stats["preference_strength"] = round(strength, 2)
            strength_vector.append(strength)
        self._stats_cache["preference_strength_vector"] = strength_vector

    def _check_anomaly(self, dimension: str, stats: Dict[str, Any]):
        now = time.time()
        if dimension in self._anomaly_cooldowns:
            if now - self._anomaly_cooldowns[dimension] < self.ANOMALY_COOLDOWN_SEC:
                return

        recent_7d = sum(stats.get("daily_counts", [0]*7))
        avg = recent_7d / 7.0 if recent_7d > 0 else 0
        current_hour = stats.get("_hourly_count", 0)

        if avg > 0 and current_hour > avg * self.ANOMALY_THRESHOLD_RATIO:
            self._anomaly_cooldowns[dimension] = now
            if self.bus:
                self.bus.publish_to_module("ag-mem-02", "anomaly_alert", self.module_id, {
                    "dimension": dimension,
                    "current_value": current_hour,
                    "historical_mean": round(avg, 2),
                })
            self._log_event("ANOMALY_DETECTED", {
                "dimension": dimension,
                "current": current_hour,
                "avg": round(avg, 2)
            })

    def _reset_hourly_counters(self):
        for stats in self._stats_cache.get("by_behavior_dimension", {}).values():
            stats["_hourly_count"] = 0
        for stats in self._stats_cache.get("by_scene_label", {}).values():
            stats["_hourly_count"] = 0

    def _rotate_daily_windows(self):
        """每日滚动窗口：丢弃7天前的数据"""
        for stats in self._stats_cache.get("by_behavior_dimension", {}).values():
            daily = stats.get("daily_counts", [0]*7)
            stats["daily_counts"] = [0] + daily[:-1]  # 右移一位，今日清零
        for stats in self._stats_cache.get("by_scene_label", {}).values():
            daily = stats.get("daily_counts", [0]*7)
            stats["daily_counts"] = [0] + daily[:-1]
        self._log_event("DAILY_WINDOW_ROTATED", {})

    def _load_slot_cache(self):
        """槽切换时清空所有缓存和状态（避免跨槽污染）"""
        self._stats_cache = {
            "total_entries": 0,
            "by_behavior_dimension": {},
            "by_scene_label": {},
            "by_label_category": {},
            "preference_strength_vector": [],
            "preference_keywords": [],
            "skip_count": 0,
        }
        self._anomaly_cooldowns.clear()
        self._log_event("SLOT_SWITCHED", {"new_slot_id": self._active_slot_id})

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module("ag-mem-02", "internal_status", self.module_id, {
                "total_entries": self._stats_cache.get("total_entries", 0),
                "active_dimensions": len(self._stats_cache.get("by_behavior_dimension", {})),
                "active_scenes": len(self._stats_cache.get("by_scene_label", {})),
            })

    def emergency_shutdown(self):
        self.state = StatisticsState.SYSTEM_PAUSED
        self._stats_cache.clear()
        self._anomaly_cooldowns.clear()
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

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        tmp = self._pending_logs.copy()
        self._pending_logs.clear()
        return tmp