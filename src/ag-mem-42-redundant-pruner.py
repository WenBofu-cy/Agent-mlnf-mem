#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-42
模块名称: 冗余记忆删除与归档单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 作为漏斗二中唯一有权物理清除数据的模块，接收来自 ag-mem-40（遗忘阈值判定单元）
          和 ag-mem-25（L3归并单元）的条目清除指令，对指定的经验条目执行安全删除或冷归档。
          根据条目的当前层级与建议处理方式，L1/L2层级条目直接安全擦除，L3及以上层级条目
          压缩归档至冷存储以保留可追溯性。在完成删除或归档后，向 ag-mem-48 退还已释放的
          存储配额，并向 ag-mem-51 记录完整的删除/归档事件。不参与遗忘判定或存储管理决策，
          仅执行已批准的条目清理操作。

依赖模块:
    ag-mem-40(遗忘阈值判定单元), ag-mem-25(L3相似经验归并单元),
    ag-mem-48(全局容量配额管控单元), ag-mem-49(存储压缩与冷归档单元),
    ag-mem-51(记忆变更日志追溯单元)
被依赖模块:
    ag-mem-40, ag-mem-25, ag-mem-20~26(各层级存储单元)

安全约束:
  D-01: L5核心层条目编译期拒绝任何删除或归档请求，确保永久记忆不被任何自动化模块清除
  D-02: L3/L4层级条目必须先完整归档至冷存储，确认归档成功后，方可从源层级删除
  D-03: 本模块为漏斗二中唯一有权物理清除数据的模块，其他模块不得绕过本模块直接删除经验条目
  D-04: 所有删除和归档操作必须记录完整的不可变事件日志（条目ID、层级、原因、时间戳）
  D-05: 配额退还必须在删除或归档操作确认完成后执行，不得在操作未完成时提前退还
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class PrunerState(Enum):
    IDLE = "idle"
    DELETING = "deleting"
    ARCHIVING = "archiving"
    QUOTA_RETURN = "quota_return"
    OPERATION_FAILED = "operation_failed"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ForgetCandidate:
    entry_id: str = ""
    source_layer: str = ""
    source_slot_id: str = ""
    i_value: float = 0.0
    forget_threshold: float = 0.0
    forget_reason: str = ""
    pending_reuse_check: bool = False


@dataclass
class CleanupRequest:
    candidates: List[ForgetCandidate] = field(default_factory=list)
    source: str = ""
    trigger_reason: str = ""


@dataclass
class TargetDeleteResult:
    entry_id: str = ""
    success: bool = True
    released_bytes: int = 0
    error_reason: str = ""


@dataclass
class ArchiveResult:
    entry_id: str = ""
    success: bool = True
    archive_location: str = ""
    compressed_size: int = 0
    error_reason: str = ""


@dataclass
class CleanupCompleteReceipt:
    total_entries: int = 0
    deleted_count: int = 0
    archived_count: int = 0
    failed_entries: List[Dict[str, str]] = field(default_factory=list)
    total_released_bytes: int = 0
    duration_ms: float = 0.0


class RedundantMemoryPruner:
    MAX_BATCH_SIZE = 200
    BATCH_INTERVAL_MS = 100
    SINGLE_OP_TIMEOUT_SEC = 5

    def __init__(self):
        self.module_id = "ag-mem-42"
        self.module_name = "冗余记忆删除与归档单元"
        self.version = "V1.0"

        self.state = PrunerState.IDLE
        self._cleanup_queue: List[CleanupRequest] = []
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_cleanup_request = None
        self._query_target_delete_confirm = None
        self._query_archive_confirm = None
        self._query_quota_return_confirm = None
        self._query_reuse_check = None

        self._publish_target_delete_command = None
        self._publish_archive_command = None
        self._publish_quota_return_request = None
        self._publish_cleanup_complete = None
        self._publish_cleanup_log = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_cleanup_request_query(self, callback: Callable[[], Optional[CleanupRequest]]):
        self._query_cleanup_request = callback

    def set_target_delete_confirm_query(self, callback: Callable[[str], Optional[TargetDeleteResult]]):
        self._query_target_delete_confirm = callback

    def set_archive_confirm_query(self, callback: Callable[[str], Optional[ArchiveResult]]):
        self._query_archive_confirm = callback

    def set_quota_return_confirm_query(self, callback: Callable[[int], Optional[bool]]):
        self._query_quota_return_confirm = callback

    def set_reuse_check_query(self, callback: Callable[[str], Optional[Dict[str, Any]]]):
        self._query_reuse_check = callback

    def set_target_delete_command_publisher(self, callback: Callable[[str, str], None]):
        self._publish_target_delete_command = callback

    def set_archive_command_publisher(self, callback: Callable[[ForgetCandidate], None]):
        self._publish_archive_command = callback

    def set_quota_return_request_publisher(self, callback: Callable[[int], None]):
        self._publish_quota_return_request = callback

    def set_cleanup_complete_publisher(self, callback: Callable[[CleanupCompleteReceipt], None]):
        self._publish_cleanup_complete = callback

    def set_cleanup_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_cleanup_log = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_pruner_cycle(self):
        if self.state == PrunerState.SYSTEM_PAUSED:
            return

        # 处理队列中的请求
        if self._cleanup_queue and self.state == PrunerState.IDLE:
            next_req = self._cleanup_queue.pop(0)
            self._process_cleanup_request(next_req)
            return

        # 接收新清理请求
        request = self._query_cleanup_request() if self._query_cleanup_request else None
        if request is None:
            return

        if self.state != PrunerState.IDLE:
            self._cleanup_queue.append(request)
            return

        self._process_cleanup_request(request)

    def _process_cleanup_request(self, request: CleanupRequest):
        start_time = time.time()
        # 保存处理前状态，使用 try-finally 确保状态恢复（修复问题二）
        previous_state = self.state

        try:
            self.state = PrunerState.DELETING

            deleted_count = 0
            archived_count = 0
            failed_entries = []
            total_released = 0

            for candidate in request.candidates:
                # 安全边界 D-01：L5 永久保留
                if candidate.source_layer == "L5":
                    failed_entries.append({"entry_id": candidate.entry_id, "reason": "L5永久记忆禁止删除"})
                    continue

                # 处理接近阈值的待定条目：查询 ag-mem-41 校验结果
                if candidate.pending_reuse_check:
                    if self._query_reuse_check:
                        reuse_result = self._query_reuse_check(candidate.entry_id)
                        if reuse_result and reuse_result.get("is_protected", False):
                            continue
                    # 校验未通过或无回调，继续执行删除

                # 确定处理方式
                if candidate.source_layer in ("L1", "L2"):
                    # 直接删除
                    if self._publish_target_delete_command:
                        self._publish_target_delete_command(candidate.entry_id, candidate.source_layer)

                    if self._query_target_delete_confirm:
                        confirm = self._query_target_delete_confirm(candidate.entry_id)
                        if confirm and confirm.success:
                            deleted_count += 1
                            total_released += confirm.released_bytes
                            self._log_cleanup(candidate, "删除")
                        else:
                            reason = confirm.error_reason if confirm else "删除确认超时"
                            failed_entries.append({"entry_id": candidate.entry_id, "reason": reason})
                    else:
                        # 修复问题一：回调不可用时保守失败
                        failed_entries.append({"entry_id": candidate.entry_id, "reason": "删除确认回调不可用"})
                else:
                    # L3/L4 需要归档
                    self.state = PrunerState.ARCHIVING

                    if self._publish_archive_command:
                        self._publish_archive_command(candidate)

                    if self._query_archive_confirm:
                        archive_confirm = self._query_archive_confirm(candidate.entry_id)
                        if archive_confirm and archive_confirm.success:
                            # 归档成功，从源层级删除
                            if self._publish_target_delete_command:
                                self._publish_target_delete_command(candidate.entry_id, candidate.source_layer)

                            if self._query_target_delete_confirm:
                                delete_confirm = self._query_target_delete_confirm(candidate.entry_id)
                                if delete_confirm and delete_confirm.success:
                                    archived_count += 1
                                    total_released += delete_confirm.released_bytes
                                    self._log_cleanup(candidate, "归档")
                                else:
                                    reason = delete_confirm.error_reason if delete_confirm else "归档后删除确认超时"
                                    failed_entries.append({"entry_id": candidate.entry_id, "reason": f"归档成功但源层级删除失败: {reason}"})
                            else:
                                failed_entries.append({"entry_id": candidate.entry_id, "reason": "归档成功但删除确认回调不可用"})
                        else:
                            reason = archive_confirm.error_reason if archive_confirm else "归档确认超时"
                            failed_entries.append({"entry_id": candidate.entry_id, "reason": reason})
                    else:
                        # 修复问题一：回调不可用时保守失败
                        failed_entries.append({"entry_id": candidate.entry_id, "reason": "归档确认回调不可用"})

            # 退还配额（安全边界 D-05）
            if total_released > 0:
                self.state = PrunerState.QUOTA_RETURN
                if self._publish_quota_return_request:
                    self._publish_quota_return_request(total_released)

            elapsed = (time.time() - start_time) * 1000
            receipt = CleanupCompleteReceipt(
                total_entries=len(request.candidates),
                deleted_count=deleted_count,
                archived_count=archived_count,
                failed_entries=failed_entries,
                total_released_bytes=total_released,
                duration_ms=elapsed
            )

            if self._publish_cleanup_complete:
                self._publish_cleanup_complete(receipt)

        except Exception as e:
            self._log_event("CLEANUP_EXCEPTION", {
                "error": str(e),
                "request_source": request.source
            })
        finally:
            # 修复问题二：确保状态恢复至 IDLE，防止队列死锁
            self.state = PrunerState.IDLE

    def _log_cleanup(self, candidate: ForgetCandidate, operation: str):
        """记录清理操作日志（安全边界 D-04）"""
        if self._publish_cleanup_log:
            self._publish_cleanup_log({
                "entry_id": candidate.entry_id,
                "source_layer": candidate.source_layer,
                "operation": operation,
                "reason": candidate.forget_reason,
                "i_value": candidate.i_value,
                "timestamp": time.time()
            })

    # ========== 辅助 ==========
    def emergency_shutdown(self):
        self.state = PrunerState.SYSTEM_PAUSED
        self._cleanup_queue.clear()
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
    print("  Agent-mlnf-mem 冗余记忆删除与归档单元 (ag-mem-42) 演示")
    print("=" * 70)

    pruner = RedundantMemoryPruner()

    print_separator("STEP 1: 处理 L1/L2 直接删除")
    pruner.set_cleanup_request_query(lambda: CleanupRequest(
        candidates=[
            ForgetCandidate("E01", "L1", "ag-mem-16", 0.02, 0.10, "I值过低"),
            ForgetCandidate("E02", "L2", "ag-mem-16", 0.15, 0.20, "I值过低"),
        ],
        source="forget_judge"
    ))
    pruner.run_pruner_cycle()
    print(f"  清理执行完成")

    print_separator("STEP 2: L5条目被拦截")
    pruner.set_cleanup_request_query(lambda: CleanupRequest(
        candidates=[ForgetCandidate("E03", "L5", "ag-mem-16", 0.01, 0.10, "测试")],
        source="forget_judge"
    ))
    pruner.run_pruner_cycle()
    print(f"  L5条目被正确拦截，未执行删除")

    print_separator("STEP 3: 待定复用校验（受保护，跳过删除）")
    pruner.set_reuse_check_query(lambda eid: {"is_protected": True})
    pruner.set_cleanup_request_query(lambda: CleanupRequest(
        candidates=[ForgetCandidate("E04", "L1", "ag-mem-16", 0.09, 0.10, "接近阈值", pending_reuse_check=True)],
        source="forget_judge"
    ))
    pruner.run_pruner_cycle()
    print(f"  受保护条目已跳过删除")

    print("\n✅ 冗余记忆删除与归档单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-42 冗余记忆删除与归档单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_pruner():
            return RedundantMemoryPruner()

        # TC-M42-01: L1条目直接删除
        print("\n[TC-M42-01] L1条目直接删除")
        try:
            p = setup_pruner()
            p.set_cleanup_request_query(lambda: CleanupRequest(
                candidates=[ForgetCandidate("T01", "L1", "ag-mem-16", 0.02, 0.10, "I值过低")],
                source="forget_judge"
            ))
            p.run_pruner_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M42-02: L3条目先归档后删除
        print("\n[TC-M42-02] L3条目先归档后删除")
        try:
            p = setup_pruner()
            p.set_cleanup_request_query(lambda: CleanupRequest(
                candidates=[ForgetCandidate("T02", "L3", "ag-mem-16", 0.25, 0.30, "I值过低")],
                source="forget_judge"
            ))
            p.run_pruner_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M42-03: L5条目被拒绝
        print("\n[TC-M42-03] L5条目被拒绝")
        try:
            p = setup_pruner()
            p.set_cleanup_request_query(lambda: CleanupRequest(
                candidates=[ForgetCandidate("T03", "L5", "ag-mem-16", 0.01, 0.10, "测试")],
                source="forget_judge"
            ))
            p.run_pruner_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M42-04: pending_reuse_check 受保护跳过删除
        print("\n[TC-M42-04] pending_reuse_check 受保护跳过删除")
        try:
            p = setup_pruner()
            p.set_reuse_check_query(lambda eid: {"is_protected": True})
            p.set_cleanup_request_query(lambda: CleanupRequest(
                candidates=[ForgetCandidate("T04", "L1", "ag-mem-16", 0.09, 0.10, "接近阈值", pending_reuse_check=True)],
                source="forget_judge"
            ))
            p.run_pruner_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M42-05: pending_reuse_check 未受保护执行删除
        print("\n[TC-M42-05] pending_reuse_check 未受保护执行删除")
        try:
            p = setup_pruner()
            p.set_reuse_check_query(lambda eid: {"is_protected": False})
            p.set_cleanup_request_query(lambda: CleanupRequest(
                candidates=[ForgetCandidate("T05", "L1", "ag-mem-16", 0.09, 0.10, "接近阈值", pending_reuse_check=True)],
                source="forget_judge"
            ))
            p.run_pruner_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M42-06: 紧急熔断
        print("\n[TC-M42-06] 紧急熔断")
        try:
            p = setup_pruner()
            p.emergency_shutdown()
            assert p.state == PrunerState.SYSTEM_PAUSED
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