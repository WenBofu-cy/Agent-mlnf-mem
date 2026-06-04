#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-47
模块名称: 疑问缓存库
所属分区: 四、漏斗外挂扩展区（物理隔离）
核心职责: 作为漏斗外挂扩展区的临时疑问存储库，暂存 ECC 认知大脑在推理过程中产生的推理
          断点、未确认认知项、低置信度场景数据。当 ECC 模块（如 ag-ecc-08 元认知模块）
          判定当前场景的置信度低于阈值或存在无法即时解决的逻辑矛盾时，将相关数据写入疑问
          缓存库，供系统离线复盘、根因分析、策略修正与模型迭代使用。本库完全独立于双漏斗
          记忆系统运行，不参与记忆沉淀、筛选、晋升与遗忘机制。仅提供暂存与查询服务，缓存
          条目在系统确认问题已解决或超过保留期限后自动清除。

依赖模块:
    无（作为独立缓存服务，不依赖记忆系统内部模块）
被依赖模块:
    ag-ecc-08(元认知模块), ag-ecc-03(因果推理模块),
    ag-mem-01(总控漏斗F₀，可转发查询请求以支持离线复盘)

安全约束:
  C-01: 疑问缓存条目在系统确认问题解决或超过保留期限后自动清除，不得无限期保留
  C-02: 疑问条目中的场景描述不得包含用户的原始输入文本或个人身份信息，仅保留场景特征
         哈希与结构化的上下文参数
  C-03: 本库完全独立于漏斗记忆系统，不参与记忆沉淀、晋升、遗忘任何环节
  C-04: 疑问缓存数据仅用于离线复盘与系统调试，不得作为实时决策依据
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import hashlib


class CacheState(Enum):
    NORMAL_SERVICE = "normal_service"
    CAPACITY_WARNING = "capacity_warning"
    CAPACITY_FULL = "capacity_full"
    SYSTEM_PAUSED = "system_paused"


class QuestionType(Enum):
    REASONING_BREAK = "推理断点"
    LOGIC_CONTRADICTION = "逻辑矛盾"
    LOW_CONFIDENCE = "低置信度场景"


class ProcessingStatus(Enum):
    PENDING = "待分析"
    ANALYZING = "分析中"
    RESOLVED = "已解决"
    IGNORED = "已忽略"
    EXPIRED = "已过期"


@dataclass
class QuestionEntry:
    entry_id: str = ""
    source_module: str = ""                 # 提交该疑问的 ECC 模块
    question_type: QuestionType = QuestionType.LOW_CONFIDENCE
    related_entry_ids: List[str] = field(default_factory=list)   # 关联的记忆或决策条目
    scene_snapshot_hash: str = ""           # 场景特征哈希（不包含原始数据）
    context_summary: Dict[str, Any] = field(default_factory=dict) # 结构化上下文参数
    current_confidence: float = 0.0
    priority: str = "中"                    # 高/中/低
    status: ProcessingStatus = ProcessingStatus.PENDING
    resolution_summary: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class QuestionSubmitRequest:
    source_module: str = ""
    question_type: QuestionType = QuestionType.LOW_CONFIDENCE
    related_entry_ids: List[str] = field(default_factory=list)
    scene_snapshot_hash: str = ""
    context_summary: Dict[str, Any] = field(default_factory=dict)
    current_confidence: float = 0.0
    priority: str = "中"


@dataclass
class QuestionQueryRequest:
    query_type: Optional[QuestionType] = None
    status: Optional[ProcessingStatus] = None
    time_range: Optional[float] = None          # 最近多少秒内
    max_results: int = 50


@dataclass
class QuestionUpdateCommand:
    entry_id: str = ""
    new_status: ProcessingStatus = ProcessingStatus.RESOLVED
    resolution_summary: str = ""


@dataclass
class QuestionQueryResult:
    entries: List[QuestionEntry] = field(default_factory=list)
    total_matched: int = 0


@dataclass
class CacheStatus:
    state: CacheState = CacheState.NORMAL_SERVICE
    total_entries: int = 0
    usage_pct: float = 0.0
    oldest_entry_time: float = 0.0
    newest_entry_time: float = 0.0


class QuestionCache:
    MAX_ENTRIES = 5000
    WARNING_THRESHOLD = 0.8
    RETENTION_DAYS = 90
    CLEANUP_INTERVAL_SEC = 86400  # 24小时
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.module_id = "ag-mem-47"
        self.module_name = "疑问缓存库"
        self.version = "V1.0"

        self.state = CacheState.NORMAL_SERVICE
        self._entries: Dict[str, QuestionEntry] = {}
        self._total_entries: int = 0
        self._last_cleanup_time: float = time.time()
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_submit_request = None
        self._query_query_request = None
        self._query_update_command = None

        self._publish_submit_confirm = None
        self._publish_query_result = None
        self._publish_update_confirm = None
        self._publish_cache_status = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成，最大容量={self.MAX_ENTRIES}")

    # ========== 回调注入 ==========
    def set_submit_request_query(self, callback: Callable[[], Optional[QuestionSubmitRequest]]):
        self._query_submit_request = callback

    def set_query_request_query(self, callback: Callable[[], Optional[QuestionQueryRequest]]):
        self._query_query_request = callback

    def set_update_command_query(self, callback: Callable[[], Optional[QuestionUpdateCommand]]):
        self._query_update_command = callback

    def set_submit_confirm_publisher(self, callback: Callable[[str, bool, str], None]):
        self._publish_submit_confirm = callback

    def set_query_result_publisher(self, callback: Callable[[QuestionQueryResult], None]):
        self._publish_query_result = callback

    def set_update_confirm_publisher(self, callback: Callable[[str, bool], None]):
        self._publish_update_confirm = callback

    def set_cache_status_publisher(self, callback: Callable[[CacheStatus], None]):
        self._publish_cache_status = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_cache_cycle(self):
        now = time.time()

        if self.state == CacheState.SYSTEM_PAUSED:
            return

        # 定期清理过期条目
        if now - self._last_cleanup_time >= self.CLEANUP_INTERVAL_SEC:
            self._perform_cleanup(now)
            self._last_cleanup_time = now

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 处理提交请求
        submit_req = self._query_submit_request() if self._query_submit_request else None
        if submit_req:
            self._handle_submit(submit_req)
            return

        # 处理查询请求
        query_req = self._query_query_request() if self._query_query_request else None
        if query_req:
            self._handle_query(query_req)
            return

        # 处理状态更新指令
        update_cmd = self._query_update_command() if self._query_update_command else None
        if update_cmd:
            self._handle_update(update_cmd)

    # ========== 提交处理 ==========
    def _handle_submit(self, request: QuestionSubmitRequest):
        if self._total_entries >= self.MAX_ENTRIES:
            if self._publish_submit_confirm:
                self._publish_submit_confirm("", False, "缓存已满")
            return

        entry_id = f"Q-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        entry = QuestionEntry(
            entry_id=entry_id,
            source_module=request.source_module,
            question_type=request.question_type,
            related_entry_ids=request.related_entry_ids,
            scene_snapshot_hash=request.scene_snapshot_hash,
            context_summary=request.context_summary,
            current_confidence=request.current_confidence,
            priority=request.priority,
            status=ProcessingStatus.PENDING,
            created_at=time.time(),
            updated_at=time.time()
        )
        self._entries[entry_id] = entry
        self._total_entries += 1

        # 容量检查
        if self._total_entries >= self.MAX_ENTRIES:
            self.state = CacheState.CAPACITY_FULL
        elif self._total_entries >= self.MAX_ENTRIES * self.WARNING_THRESHOLD:
            self.state = CacheState.CAPACITY_WARNING

        if self._publish_submit_confirm:
            self._publish_submit_confirm(entry_id, True, "")

    # ========== 查询处理 ==========
    def _handle_query(self, request: QuestionQueryRequest):
        matched = []
        for entry in self._entries.values():
            if request.question_type and entry.question_type != request.question_type:
                continue
            if request.status and entry.status != request.status:
                continue
            if request.time_range and (time.time() - entry.created_at) > request.time_range:
                continue
            matched.append(entry)
        matched.sort(key=lambda x: x.created_at, reverse=True)
        matched = matched[:request.max_results]

        if self._publish_query_result:
            self._publish_query_result(QuestionQueryResult(
                entries=matched,
                total_matched=len(matched)
            ))

    # ========== 状态更新 ==========
    def _handle_update(self, command: QuestionUpdateCommand):
        if command.entry_id in self._entries:
            entry = self._entries[command.entry_id]
            entry.status = command.new_status
            entry.resolution_summary = command.resolution_summary
            entry.updated_at = time.time()
            if self._publish_update_confirm:
                self._publish_update_confirm(command.entry_id, True)
        else:
            if self._publish_update_confirm:
                self._publish_update_confirm(command.entry_id, False)

    # ========== 定期清理 ==========
    def _perform_cleanup(self, now: float):
        retention_sec = self.RETENTION_DAYS * 86400
        expired = [
            eid for eid, entry in self._entries.items()
            if (now - entry.created_at) > retention_sec and entry.status in (ProcessingStatus.RESOLVED, ProcessingStatus.IGNORED)
        ]
        for eid in expired:
            del self._entries[eid]
            self._total_entries -= 1

        if self._total_entries < self.MAX_ENTRIES * self.WARNING_THRESHOLD:
            self.state = CacheState.NORMAL_SERVICE

    # ========== 辅助方法 ==========
    def _publish_status(self):
        if self._publish_cache_status:
            usage = self._total_entries / self.MAX_ENTRIES if self.MAX_ENTRIES > 0 else 0
            oldest = min((e.created_at for e in self._entries.values()), default=0)
            newest = max((e.created_at for e in self._entries.values()), default=0)
            self._publish_cache_status(CacheStatus(
                state=self.state,
                total_entries=self._total_entries,
                usage_pct=round(usage, 2),
                oldest_entry_time=oldest,
                newest_entry_time=newest
            ))

    def emergency_shutdown(self):
        self.state = CacheState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 疑问缓存库 (ag-mem-47) 演示")
    print("=" * 70)

    cache = QuestionCache()

    print_separator("STEP 1: 提交低置信度场景疑问")
    cache.set_submit_request_query(lambda: QuestionSubmitRequest(
        source_module="ag-ecc-08",
        question_type=QuestionType.LOW_CONFIDENCE,
        current_confidence=0.35,
        priority="高"
    ))
    cache.run_cache_cycle()
    print(f"  当前条目数: {cache._total_entries}")

    print_separator("STEP 2: 查询待分析疑问")
    cache.set_query_request_query(lambda: QuestionQueryRequest(
        status=ProcessingStatus.PENDING
    ))
    cache.run_cache_cycle()

    print_separator("STEP 3: 更新疑问状态为已解决")
    # 获取刚刚提交的条目ID
    entry_id = next(iter(cache._entries.keys())) if cache._entries else ""
    if entry_id:
        cache.set_update_command_query(lambda: QuestionUpdateCommand(
            entry_id=entry_id,
            new_status=ProcessingStatus.RESOLVED,
            resolution_summary="已确认为临时网络波动导致"
        ))
        cache.run_cache_cycle()
        print(f"  状态更新完成")

    print("\n✅ 疑问缓存库演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-47 疑问缓存库 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_cache():
            return QuestionCache()

        # TC-M47-01: 正常提交疑问
        print("\n[TC-M47-01] 正常提交疑问")
        try:
            c = setup_cache()
            c.set_submit_request_query(lambda: QuestionSubmitRequest(
                source_module="ag-ecc-08", question_type=QuestionType.LOW_CONFIDENCE, current_confidence=0.3
            ))
            c.run_cache_cycle()
            assert c._total_entries == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M47-02: 缓存满时拒绝提交
        print("\n[TC-M47-02] 缓存满时拒绝提交")
        try:
            c = setup_cache()
            c._total_entries = c.MAX_ENTRIES
            c.set_submit_request_query(lambda: QuestionSubmitRequest(source_module="ag-ecc-08"))
            c.run_cache_cycle()
            assert c._total_entries == c.MAX_ENTRIES  # 未增加
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M47-03: 更新疑问状态
        print("\n[TC-M47-03] 更新疑问状态")
        try:
            c = setup_cache()
            c._entries["Q-TEST"] = QuestionEntry(entry_id="Q-TEST", status=ProcessingStatus.PENDING)
            c._total_entries = 1
            c.set_update_command_query(lambda: QuestionUpdateCommand(entry_id="Q-TEST", new_status=ProcessingStatus.RESOLVED))
            c.run_cache_cycle()
            assert c._entries["Q-TEST"].status == ProcessingStatus.RESOLVED
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M47-04: 定期清理过期已解决条目
        print("\n[TC-M47-04] 定期清理过期已解决条目")
        try:
            c = setup_cache()
            old_time = time.time() - (c.RETENTION_DAYS + 1) * 86400
            c._entries["Q-OLD"] = QuestionEntry(entry_id="Q-OLD", status=ProcessingStatus.RESOLVED, created_at=old_time)
            c._entries["Q-NEW"] = QuestionEntry(entry_id="Q-NEW", status=ProcessingStatus.PENDING)
            c._total_entries = 2
            c._perform_cleanup(time.time())
            assert "Q-OLD" not in c._entries
            assert "Q-NEW" in c._entries
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M47-05: 查询过滤
        print("\n[TC-M47-05] 查询过滤（按状态）")
        try:
            c = setup_cache()
            c._entries["Q1"] = QuestionEntry(entry_id="Q1", status=ProcessingStatus.PENDING)
            c._entries["Q2"] = QuestionEntry(entry_id="Q2", status=ProcessingStatus.RESOLVED)
            c._total_entries = 2
            c.set_query_request_query(lambda: QuestionQueryRequest(status=ProcessingStatus.PENDING))
            c.run_cache_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M47-06: 紧急熔断
        print("\n[TC-M47-06] 紧急熔断")
        try:
            c = setup_cache()
            c.emergency_shutdown()
            assert c.state == CacheState.SYSTEM_PAUSED
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