#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-26
模块名称: L4长期层存储单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为漏斗二五层记忆存储架构的第四层，专门存储从L3晋升而来的跨场景可泛化复用的
          高阶任务技能经验。对写入的经验数据进行去个性化处理，通过ag-mem-27提取通用规则。
          为ag-mem-27提供待抽象提炼的同类经验条目集。L4层条目默认受遗忘保护，仅当I值降至
          遗忘阈值以下且复用频次验证不足时才被遗忘。晋升至L5核心层的经验需通过更高门槛。
          不参与晋升判定或遗忘决策，仅执行经验的接收、存储、泛化管理与基础管理。

依赖模块:
    ag-mem-24(L3中期层存储单元), ag-mem-27(L4抽象提炼单元),
    ag-mem-28(L5核心层存储单元), ag-mem-40(遗忘阈值判定单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-24, ag-mem-27, ag-mem-15~19(各场景分槽)

安全约束:
  S-01: 所有写入L4的经验必须经过去个性化处理，禁止保留任何可关联到特定用户的个人信息
  S-02: L4不接受警示条目标签为CAUTION的经验，确保长期层不包含失败策略
  S-03: L4层经验受强遗忘保护，遗忘阈值显著低于其他层级
  S-04: 去个性化后的重要度重算必须去除V值（用户价值）维度，仅基于安全显著性与复用频次
  S-05: L4层经验在晋升L5时必须额外通过安全底线校验（由ag-mem-43执行）
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class StorageState(Enum):
    NORMAL = "normal"
    CAPACITY_WARNING = "capacity_warning"
    CAPACITY_CRITICAL = "capacity_critical"
    ABSTRACTING = "abstracting"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ExperienceEntry:
    entry_id: str = ""
    source_slot_id: str = ""
    experience_data: Dict[str, Any] = field(default_factory=dict)
    i_value: float = 0.0
    s_value: float = 0.0
    v_value: float = 0.0  # 写入L4后置零
    c_value: float = 0.0
    result_label: str = "成功"
    caution_label: str = "NORMAL"
    promoted_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    abstracted: bool = False
    related_rule_id: Optional[str] = None


@dataclass
class L4PromotionList:
    entries: List[ExperienceEntry] = field(default_factory=list)
    source_slot_id: str = ""


@dataclass
class L4WriteConfirm:
    received_count: int = 0
    success_count: int = 0
    anonymized_count: int = 0
    l4_usage_pct: float = 0.0


@dataclass
class L4QueryRequest:
    request_id: str = ""
    query_conditions: Dict[str, Any] = field(default_factory=dict)
    source_slot_id: str = ""
    max_results: int = 20


@dataclass
class L4QueryResult:
    matched_entries: List[ExperienceEntry] = field(default_factory=list)
    layer: str = "L4"
    total_count: int = 0


@dataclass
class L4StatusReport:
    state: StorageState = StorageState.NORMAL
    total_entries: int = 0
    usage_pct: float = 0.0
    abstracted_count: int = 0
    recent_90d_writes: int = 0


@dataclass
class AbstractionTrigger:
    slot_id: str = ""
    entries: List[ExperienceEntry] = field(default_factory=list)
    reason: str = ""


class L4LongTermStorage:
    # 容量配置
    L4_CAPACITY_RATIO = 0.045  # 占漏斗二总容量的4.5%
    MAX_ENTRIES = 1000
    MAX_ENTRY_SIZE_BYTES = 25 * 1024
    CAPACITY_WARN_THRESHOLD = 0.80
    CAPACITY_CRITICAL_THRESHOLD = 0.95
    ABSTRACT_TRIGGER_COUNT = 20  # 每新增20条同类经验触发抽象提炼
    ABSTRACT_TIMED_INTERVAL_SEC = 72 * 3600  # 72小时
    FORGET_SCAN_INTERVAL_SEC = 24 * 3600  # 24小时
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.module_id = "ag-mem-26"
        self.module_name = "L4长期层存储单元"
        self.version = "V1.0"

        self.state = StorageState.NORMAL
        self._entries: Dict[str, ExperienceEntry] = {}
        self._entry_count: int = 0
        self._abstract_counter: Dict[str, int] = {}  # 分槽编号 → 新增条目数
        self._recent_90d_writes: int = 0
        self._last_abstract_time: float = time.time()
        self._last_forget_scan: float = time.time()
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_promotion_list = None
        self._query_query_request = None
        self._query_abstraction_confirm = None
        self._query_cleanup_confirm = None

        self._publish_write_confirm = None
        self._publish_query_result = None
        self._publish_abstraction_trigger = None
        self._publish_forget_trigger = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 最大条目={self.MAX_ENTRIES}")

    def set_promotion_list_query(self, callback: Callable[[], Optional[L4PromotionList]]):
        self._query_promotion_list = callback

    def set_query_request_query(self, callback: Callable[[], Optional[L4QueryRequest]]):
        self._query_query_request = callback

    def set_abstraction_confirm_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_abstraction_confirm = callback

    def set_cleanup_confirm_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_cleanup_confirm = callback

    def set_write_confirm_publisher(self, callback: Callable[[L4WriteConfirm], None]):
        self._publish_write_confirm = callback

    def set_query_result_publisher(self, callback: Callable[[L4QueryResult], None]):
        self._publish_query_result = callback

    def set_abstraction_trigger_publisher(self, callback: Callable[[AbstractionTrigger], None]):
        self._publish_abstraction_trigger = callback

    def set_forget_trigger_publisher(self, callback: Callable[[str, List[str]], None]):
        self._publish_forget_trigger = callback

    def set_status_report_publisher(self, callback: Callable[[L4StatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_storage_cycle(self) -> Optional[L4WriteConfirm]:
        now = time.time()

        if self.state == StorageState.SYSTEM_PAUSED:
            return None

        # 容量清理回执
        if self.state in (StorageState.CAPACITY_WARNING, StorageState.CAPACITY_CRITICAL):
            cleanup = self._query_cleanup_confirm() if self._query_cleanup_confirm else None
            if cleanup:
                cleaned = cleanup.get("cleaned_count", 0)
                self._entry_count -= cleaned
                if self._calculate_usage_pct() < self.CAPACITY_WARN_THRESHOLD:
                    self.state = StorageState.NORMAL

        # 定时遗忘扫描
        if now - self._last_forget_scan >= self.FORGET_SCAN_INTERVAL_SEC:
            self._trigger_forget("定时遗忘扫描")
            self._last_forget_scan = now

        # 定时抽象提炼
        if now - self._last_abstract_time >= self.ABSTRACT_TIMED_INTERVAL_SEC:
            self._trigger_timed_abstraction()
            self._last_abstract_time = now

        # 抽象提炼完成回执
        abstraction_confirm = self._query_abstraction_confirm() if self._query_abstraction_confirm else None
        if abstraction_confirm:
            self._handle_abstraction_complete(abstraction_confirm)

        # 状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 查询请求
        query_req = self._query_query_request() if self._query_query_request else None
        if query_req:
            self._handle_query(query_req)

        # 接收晋升条目
        promotion = self._query_promotion_list() if self._query_promotion_list else None
        if promotion is None or not promotion.entries:
            return None

        return self._handle_promotion(promotion)

    def _handle_promotion(self, promotion: L4PromotionList) -> L4WriteConfirm:
        # 容量检查
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CAPACITY_CRITICAL_THRESHOLD:
            self.state = StorageState.CAPACITY_CRITICAL
            self._trigger_forget("容量紧急")
        elif usage_pct >= self.CAPACITY_WARN_THRESHOLD:
            self.state = StorageState.CAPACITY_WARNING

        if self.state == StorageState.CAPACITY_CRITICAL and self._entry_count >= self.MAX_ENTRIES:
            self._trigger_forget("容量紧急")
            if self._entry_count >= self.MAX_ENTRIES:
                confirm = L4WriteConfirm(received_count=len(promotion.entries))
                if self._publish_write_confirm:
                    self._publish_write_confirm(confirm)
                return confirm

        received = len(promotion.entries)
        success_count = 0
        anonymized_count = 0

        for entry in promotion.entries:
            # 拒绝CAUTION条目
            if entry.caution_label == "CAUTION":
                self._log_event("CAUTION_REJECTED_L4", {"entry_id": entry.entry_id})
                continue

            # 去个性化处理
            self._anonymize_entry(entry)
            anonymized_count += 1

            # 重算I值（去除V值）
            entry.i_value = self._recalculate_i_value(entry)

            # 写入L4
            entry.promoted_at = time.time()
            self._entries[entry.entry_id] = entry
            self._entry_count += 1
            success_count += 1

            # 更新抽象计数器
            slot = entry.source_slot_id
            if slot not in self._abstract_counter:
                self._abstract_counter[slot] = 0
            self._abstract_counter[slot] += 1

            # 检查是否触发抽象提炼
            if self._abstract_counter[slot] >= self.ABSTRACT_TRIGGER_COUNT:
                self._trigger_abstraction_for_slot(slot)
                self._abstract_counter[slot] = 0

        self._recent_90d_writes += success_count

        confirm = L4WriteConfirm(
            received_count=received,
            success_count=success_count,
            anonymized_count=anonymized_count,
            l4_usage_pct=self._calculate_usage_pct()
        )

        if self._publish_write_confirm:
            self._publish_write_confirm(confirm)

        return confirm

    def _handle_query(self, request: L4QueryRequest):
        matched = []
        for entry in self._entries.values():
            if request.source_slot_id and entry.source_slot_id != request.source_slot_id:
                continue
            keywords = request.query_conditions.get("keywords", [])
            if keywords:
                if not any(kw in str(entry.experience_data) for kw in keywords):
                    continue
            entry.last_accessed_at = time.time()
            matched.append(entry)

        matched.sort(key=lambda x: x.i_value, reverse=True)
        matched = matched[:request.max_results]

        if self._publish_query_result:
            self._publish_query_result(L4QueryResult(
                matched_entries=matched,
                total_count=len(matched)
            ))

    def _anonymize_entry(self, entry: ExperienceEntry):
        # 删除个性化字段，替换为匿名标记
        entry.experience_data.pop("user_id", None)
        entry.experience_data.pop("session_id", None)
        entry.experience_data.pop("device_fingerprint", None)
        entry.experience_data.pop("geo_location", None)
        entry.experience_data.pop("raw_input_text", None)
        entry.experience_data["user_profile"] = "ANONYMOUS"
        # V值置零
        entry.v_value = 0.0

    def _recalculate_i_value(self, entry: ExperienceEntry) -> float:
        # I = I₀ + α·S + γ·C  (去除V值)
        alpha = 0.40
        gamma = 0.30
        i0 = entry.i_value - (0.40 * entry.s_value + 0.30 * entry.v_value + 0.30 * entry.c_value)
        if i0 < 0.05:
            i0 = 0.10
        new_i = i0 + alpha * entry.s_value + gamma * entry.c_value
        return round(min(max(new_i, 0.05), 1.0), 3)

    def _trigger_abstraction_for_slot(self, slot_id: str):
        if self.state != StorageState.NORMAL:
            return
        self.state = StorageState.ABSTRACTING
        unabstracted = [e for e in self._entries.values()
                        if e.source_slot_id == slot_id and not e.abstracted]
        if len(unabstracted) >= 5 and self._publish_abstraction_trigger:
            self._publish_abstraction_trigger(AbstractionTrigger(
                slot_id=slot_id,
                entries=unabstracted,
                reason="累计触发"
            ))
        self.state = StorageState.NORMAL

    def _trigger_timed_abstraction(self):
        if self.state != StorageState.NORMAL:
            return
        self.state = StorageState.ABSTRACTING
        for slot_id in set(e.source_slot_id for e in self._entries.values()):
            unabstracted = [e for e in self._entries.values()
                            if e.source_slot_id == slot_id and not e.abstracted]
            if len(unabstracted) >= 5 and self._publish_abstraction_trigger:
                self._publish_abstraction_trigger(AbstractionTrigger(
                    slot_id=slot_id,
                    entries=unabstracted,
                    reason="定时触发"
                ))
        self.state = StorageState.NORMAL

    def _handle_abstraction_complete(self, confirm: Dict[str, Any]):
        entry_ids = confirm.get("entry_ids", [])
        rule_id = confirm.get("rule_id", "")
        for eid in entry_ids:
            if eid in self._entries:
                self._entries[eid].abstracted = True
                self._entries[eid].related_rule_id = rule_id

    def _trigger_forget(self, reason: str):
        if self._publish_forget_trigger and self._entries:
            self._publish_forget_trigger("ag-mem-40", list(self._entries.keys()))

    def _calculate_usage_pct(self) -> float:
        return min(self._entry_count / self.MAX_ENTRIES, 1.0) if self.MAX_ENTRIES > 0 else 0.0

    def _publish_status(self):
        abstracted = sum(1 for e in self._entries.values() if e.abstracted)
        if self._publish_status_report:
            self._publish_status_report(L4StatusReport(
                state=self.state,
                total_entries=self._entry_count,
                usage_pct=self._calculate_usage_pct(),
                abstracted_count=abstracted,
                recent_90d_writes=self._recent_90d_writes
            ))

    def get_state(self) -> StorageState:
        return self.state

    def get_entry_count(self) -> int:
        return self._entry_count

    def emergency_shutdown(self):
        self.state = StorageState.SYSTEM_PAUSED
        print(f"[{self.module_id}] 紧急熔断, 当前条目数={self._entry_count}")

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
    print("  Agent-mlnf-mem L4长期层存储单元 (ag-mem-26) 演示")
    print("=" * 70)

    storage = L4LongTermStorage()

    print_separator("STEP 1: 接收L3晋升条目（含去个性化处理）")
    storage.set_promotion_list_query(lambda: L4PromotionList(
        entries=[
            ExperienceEntry(entry_id="L3-TOOL-001", source_slot_id="ag-mem-16",
                            experience_data={"tool": "weather_api", "user_id": "U001"},
                            i_value=0.75, s_value=0.6, v_value=0.5, c_value=0.4, result_label="成功"),
        ],
        source_slot_id="ag-mem-16"
    ))
    result = storage.run_storage_cycle()
    if result:
        print(f"  接收: {result.received_count}, 写入: {result.success_count}, 去个性化: {result.anonymized_count}")

    print_separator("STEP 2: 拒绝CAUTION条目")
    storage.set_promotion_list_query(lambda: L4PromotionList(
        entries=[
            ExperienceEntry(entry_id="L3-TOOL-CAUTION", source_slot_id="ag-mem-16",
                            caution_label="CAUTION", i_value=0.6),
        ]
    ))
    result = storage.run_storage_cycle()
    if result:
        print(f"  接收: {result.received_count}, 写入: {result.success_count} (CAUTION条目被拒绝)")

    print("\n✅ L4长期层存储单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-26 L4长期层存储单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_storage():
            return L4LongTermStorage()

        # TC-M26-01: 正常写入并去个性化
        print("\n[TC-M26-01] 正常写入并去个性化")
        try:
            s = setup_storage()
            s.set_promotion_list_query(lambda: L4PromotionList(entries=[
                ExperienceEntry(entry_id="T01", source_slot_id="ag-mem-16",
                                experience_data={"user_id": "U001", "session_id": "S001", "tool": "test"},
                                i_value=0.75, s_value=0.6, v_value=0.5, c_value=0.4)
            ]))
            result = s.run_storage_cycle()
            assert result is not None
            assert result.success_count == 1
            entry = s._entries["T01"]
            assert "user_id" not in entry.experience_data
            assert entry.v_value == 0.0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M26-02: 拒绝CAUTION条目
        print("\n[TC-M26-02] 拒绝CAUTION条目")
        try:
            s = setup_storage()
            s.set_promotion_list_query(lambda: L4PromotionList(entries=[
                ExperienceEntry(entry_id="T02", caution_label="CAUTION", i_value=0.6)
            ]))
            result = s.run_storage_cycle()
            assert result.success_count == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M26-03: 查询
        print("\n[TC-M26-03] 查询")
        try:
            s = setup_storage()
            s._entries["Q1"] = ExperienceEntry(entry_id="Q1", source_slot_id="ag-mem-16",
                                                experience_data={"tool": "weather"}, i_value=0.8)
            s._entry_count = 1
            s.set_query_request_query(lambda: L4QueryRequest(source_slot_id="ag-mem-16", query_conditions={"keywords": ["weather"]}))
            s.run_storage_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M26-04: 触发抽象提炼
        print("\n[TC-M26-04] 触发抽象提炼（累计20条）")
        try:
            s = setup_storage()
            s._abstract_counter["ag-mem-16"] = 19
            s.set_promotion_list_query(lambda: L4PromotionList(entries=[
                ExperienceEntry(entry_id="T04", source_slot_id="ag-mem-16", i_value=0.8)
            ]))
            s.run_storage_cycle()
            assert s._abstract_counter["ag-mem-16"] == 0  # 触发后重置
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M26-05: 定时抽象提炼
        print("\n[TC-M26-05] 定时抽象提炼（72小时）")
        try:
            s = setup_storage()
            s._entries["E1"] = ExperienceEntry(entry_id="E1", source_slot_id="ag-mem-16", i_value=0.8, abstracted=False)
            s._entries["E2"] = ExperienceEntry(entry_id="E2", source_slot_id="ag-mem-16", i_value=0.7, abstracted=False)
            s._entries["E3"] = ExperienceEntry(entry_id="E3", source_slot_id="ag-mem-16", i_value=0.6, abstracted=False)
            s._entries["E4"] = ExperienceEntry(entry_id="E4", source_slot_id="ag-mem-16", i_value=0.5, abstracted=False)
            s._entries["E5"] = ExperienceEntry(entry_id="E5", source_slot_id="ag-mem-16", i_value=0.4, abstracted=False)
            s._entry_count = 5
            s._last_abstract_time = 0
            s.run_storage_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M26-06: 紧急熔断
        print("\n[TC-M26-06] 紧急熔断")
        try:
            s = setup_storage()
            s.emergency_shutdown()
            assert s.state == StorageState.SYSTEM_PAUSED
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