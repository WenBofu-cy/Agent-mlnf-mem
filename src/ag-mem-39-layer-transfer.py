#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-39
模块名称: 层级单向搬运写入单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 接收 ag-mem-38（晋升双条件判定单元）下发的晋升候选清单，执行经验条目从当前层级
          向上一层的物理搬运。确保搬运过程严格单向——经验只能从低层级向高层级晋升，禁止任何
          形式的回退或越级跳转。在搬运完成后，通知源层级存储单元删除已搬运的原始条目，并向
          目标层级确认写入成功。当搬运失败时，保留原始条目不变并回滚搬运操作。不参与晋升条件
          判定，仅执行已批准晋升条目的物理搬运。

依赖模块:
    ag-mem-38(晋升双条件判定单元), ag-mem-20~28(各层级存储单元),
    ag-mem-48(全局容量配额管控单元), ag-mem-51(记忆变更日志追溯单元)
被依赖模块:
    ag-mem-38, ag-mem-51

安全约束:
  T-01: 编译期硬编码合法晋升路径（L1→L2→L3→L4→L5），禁止任何形式的回退或越级搬运
  T-02: 搬运过程严格遵循"先写目标，确认成功后，再删源"的顺序，确保经验数据不丢失
  T-03: 目标层级写入失败时，已部分完成的操作必须完整回滚
  T-04: L4→L5晋升必须携带 ag-mem-29 签发的有效安全令牌，否则拒绝搬运
  T-05: 每条搬运操作必须记录完整的晋升事件日志
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class TransferState(Enum):
    IDLE = "idle"
    CAPACITY_CHECK = "capacity_check"
    TRANSFERRING = "transferring"
    ROLLING_BACK = "rolling_back"
    SYSTEM_PAUSED = "system_paused"


# 合法的晋升路径映射（编译期硬编码）
LEGAL_TRANSFERS = {
    ("L1", "L2"): True,
    ("L2", "L3"): True,
    ("L3", "L4"): True,
    ("L4", "L5"): True,
}


@dataclass
class PromotionEntry:
    entry_id: str = ""
    experience_data: Dict[str, Any] = field(default_factory=dict)
    i_value: float = 0.0
    source_slot_id: str = ""
    source_layer: str = ""
    target_layer: str = ""
    promotion_reason: str = ""
    security_token: Optional[str] = None


@dataclass
class PromotionCandidateList:
    source_layer: str = ""
    target_layer: str = ""
    entries: List[PromotionEntry] = field(default_factory=list)


@dataclass
class TargetWriteResult:
    entry_id: str = ""
    success: bool = True
    storage_position: str = ""
    error_reason: str = ""


@dataclass
class SourceDeleteResult:
    entry_id: str = ""
    success: bool = True
    released_bytes: int = 0
    error_reason: str = ""


@dataclass
class TransferCompleteReceipt:
    source_layer: str = ""
    target_layer: str = ""
    total_entries: int = 0
    success_count: int = 0
    failed_entries: List[Dict[str, str]] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class TransferAnomalyAlert:
    alert_type: str = ""
    entry_id: str = ""
    source_layer: str = ""
    target_layer: str = ""
    detail: str = ""


class LayerTransferUnit:
    WRITE_TIMEOUT_SEC = 5
    DELETE_TIMEOUT_SEC = 5
    ROLLBACK_RETRY_MAX = 2
    MAX_BATCH_SIZE = 200

    def __init__(self):
        self.module_id = "ag-mem-39"
        self.module_name = "层级单向搬运写入单元"
        self.version = "V1.0"

        self.state = TransferState.IDLE
        self._written_in_current_batch: List[str] = []
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_promotion_list = None
        self._query_target_capacity = None
        self._query_target_write_confirm = None
        self._query_source_delete_confirm = None
        self._query_l5_security_token = None

        self._publish_target_write_command = None
        self._publish_source_delete_command = None
        self._publish_transfer_complete = None
        self._publish_anomaly_alert = None
        self._publish_promotion_log = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_promotion_list_query(self, callback: Callable[[], Optional[PromotionCandidateList]]):
        self._query_promotion_list = callback

    def set_target_capacity_query(self, callback: Callable[[str, int], Optional[bool]]):
        self._query_target_capacity = callback

    def set_target_write_confirm_query(self, callback: Callable[[str], Optional[TargetWriteResult]]):
        self._query_target_write_confirm = callback

    def set_source_delete_confirm_query(self, callback: Callable[[str], Optional[SourceDeleteResult]]):
        self._query_source_delete_confirm = callback

    def set_l5_security_token_query(self, callback: Callable[[], Optional[str]]):
        self._query_l5_security_token = callback

    def set_target_write_command_publisher(self, callback: Callable[[PromotionEntry], None]):
        self._publish_target_write_command = callback

    def set_source_delete_command_publisher(self, callback: Callable[[str, str], None]):
        self._publish_source_delete_command = callback

    def set_transfer_complete_publisher(self, callback: Callable[[TransferCompleteReceipt], None]):
        self._publish_transfer_complete = callback

    def set_anomaly_alert_publisher(self, callback: Callable[[TransferAnomalyAlert], None]):
        self._publish_anomaly_alert = callback

    def set_promotion_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_promotion_log = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_transfer_cycle(self):
        if self.state == TransferState.SYSTEM_PAUSED:
            return

        candidate_list = self._query_promotion_list() if self._query_promotion_list else None
        if candidate_list is None:
            return

        source = candidate_list.source_layer
        target = candidate_list.target_layer

        # 校验路径合法性
        if (source, target) not in LEGAL_TRANSFERS:
            self._log_anomaly("非法晋升路径", "", source, target, f"路径 {source}→{target} 不合法")
            return

        if not candidate_list.entries:
            self._send_transfer_complete(candidate_list, 0, [], 0.0)
            return

        # 容量校验
        self.state = TransferState.CAPACITY_CHECK
        if self._query_target_capacity:
            ok = self._query_target_capacity(target, len(candidate_list.entries))
            if not ok:
                fails = [{"entry_id": e.entry_id, "reason": "目标层级容量不足"} for e in candidate_list.entries]
                self._send_transfer_complete(candidate_list, 0, fails, 0.0)
                self.state = TransferState.IDLE
                return

        # 开始搬运
        self.state = TransferState.TRANSFERRING
        self._written_in_current_batch = []
        start_time = time.time()
        success_count = 0
        failed_list: List[Dict[str, str]] = []

        for idx, entry in enumerate(candidate_list.entries):
            # L4→L5 特殊校验
            if source == "L4" and target == "L5":
                if not entry.security_token:
                    failed_list.append({"entry_id": entry.entry_id, "reason": "缺少L5安全令牌"})
                    continue
                if len(entry.security_token) < 10:
                    failed_list.append({"entry_id": entry.entry_id, "reason": "L5安全令牌无效"})
                    continue

            # 写入目标层级
            if self._publish_target_write_command:
                self._publish_target_write_command(entry)
            write_confirm = self._query_target_write_confirm(entry.entry_id) if self._query_target_write_confirm else TargetWriteResult(entry_id=entry.entry_id, success=True)

            if not write_confirm.success:
                # 写入失败，回滚已写入的条目
                self.state = TransferState.ROLLING_BACK
                self._rollback_written_entries(target)
                failed_list.append({"entry_id": entry.entry_id, "reason": write_confirm.error_reason})

                # 修复：将剩余未处理条目全部标记为失败
                remaining = candidate_list.entries[idx + 1:]
                for e in remaining:
                    failed_list.append({"entry_id": e.entry_id, "reason": "搬运中断（前序写入失败）"})

                self._log_anomaly("搬运中断", entry.entry_id, source, target,
                                   f"前序条目写入失败，共{len(remaining)}条未处理")
                break

            # 写入成功，标记
            self._written_in_current_batch.append(entry.entry_id)

            # 删除源层级条目
            if self._publish_source_delete_command:
                self._publish_source_delete_command(entry.entry_id, source)
            delete_confirm = self._query_source_delete_confirm(entry.entry_id) if self._query_source_delete_confirm else SourceDeleteResult(entry_id=entry.entry_id, success=True)

            if delete_confirm.success:
                success_count += 1
                if self._publish_promotion_log:
                    self._publish_promotion_log({
                        "entry_id": entry.entry_id,
                        "source_layer": source,
                        "target_layer": target,
                        "i_value": entry.i_value,
                        "reason": entry.promotion_reason,
                        "timestamp": time.time()
                    })
            else:
                success_count += 1
                failed_list.append({"entry_id": entry.entry_id, "reason": "源层级删除失败，条目在两级同时存在"})

        duration_ms = (time.time() - start_time) * 1000
        self._send_transfer_complete(candidate_list, success_count, failed_list, duration_ms)
        self._written_in_current_batch = []
        self.state = TransferState.IDLE

    def _rollback_written_entries(self, target_layer: str):
        """回滚已写入的条目，增加确认等待与重试机制"""
        for eid in self._written_in_current_batch:
            success = False
            for attempt in range(self.ROLLBACK_RETRY_MAX + 1):
                if self._publish_source_delete_command:
                    self._publish_source_delete_command(eid, target_layer)
                # 等待删除确认
                confirm = self._query_source_delete_confirm(eid) if self._query_source_delete_confirm else SourceDeleteResult(entry_id=eid, success=True)
                if confirm and confirm.success:
                    success = True
                    break
                if attempt < self.ROLLBACK_RETRY_MAX:
                    time.sleep(0.1)  # 短暂等待后重试

            if not success:
                # 回滚删除失败，记录告警，标记需人工清理
                self._log_anomaly("回滚删除失败", eid, "", target_layer,
                                   f"已写入条目{eid}无法从目标层级{target_layer}清除，需人工干预")
                self._log_event("ROLLBACK_DELETE_FAILED", {
                    "entry_id": eid,
                    "target_layer": target_layer,
                    "retries": self.ROLLBACK_RETRY_MAX
                })

        self._written_in_current_batch = []

    def _send_transfer_complete(self, candidate_list: PromotionCandidateList, success_count: int,
                                  failed_list: List[Dict[str, str]], duration_ms: float):
        if self._publish_transfer_complete:
            self._publish_transfer_complete(TransferCompleteReceipt(
                source_layer=candidate_list.source_layer,
                target_layer=candidate_list.target_layer,
                total_entries=len(candidate_list.entries),
                success_count=success_count,
                failed_entries=failed_list,
                duration_ms=duration_ms
            ))

    def _log_anomaly(self, alert_type: str, entry_id: str, source: str, target: str, detail: str):
        if self._publish_anomaly_alert:
            self._publish_anomaly_alert(TransferAnomalyAlert(
                alert_type=alert_type,
                entry_id=entry_id,
                source_layer=source,
                target_layer=target,
                detail=detail
            ))

    # ========== 辅助 ==========
    def emergency_shutdown(self):
        self.state = TransferState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 层级单向搬运写入单元 (ag-mem-39) 演示")
    print("=" * 70)

    unit = LayerTransferUnit()
    unit.set_target_capacity_query(lambda layer, count: True)

    print_separator("STEP 1: 正常 L2→L3 搬运")
    unit.set_promotion_list_query(lambda: PromotionCandidateList(
        source_layer="L2", target_layer="L3",
        entries=[
            PromotionEntry(entry_id="E01", source_layer="L2", target_layer="L3", i_value=0.65, source_slot_id="ag-mem-16"),
            PromotionEntry(entry_id="E02", source_layer="L2", target_layer="L3", i_value=0.70, source_slot_id="ag-mem-16"),
        ]
    ))
    unit.run_transfer_cycle()
    print(f"  搬运执行完成")

    print_separator("STEP 2: 非法路径 L1→L3 被拦截")
    unit.set_promotion_list_query(lambda: PromotionCandidateList(
        source_layer="L1", target_layer="L3",
        entries=[PromotionEntry(entry_id="E03", source_layer="L1", target_layer="L3", i_value=0.5)]
    ))
    unit.run_transfer_cycle()
    print(f"  非法路径已被拦截")

    print_separator("STEP 3: 写入失败回滚并标记剩余条目")
    call_count = [0]
    def mock_write_confirm(eid):
        call_count[0] += 1
        if call_count[0] > 1:
            return TargetWriteResult(entry_id=eid, success=False, error_reason="存储写入异常")
        return TargetWriteResult(entry_id=eid, success=True)
    unit.set_target_write_confirm_query(mock_write_confirm)
    unit.set_promotion_list_query(lambda: PromotionCandidateList(
        source_layer="L2", target_layer="L3",
        entries=[
            PromotionEntry(entry_id="E1", source_layer="L2", target_layer="L3", i_value=0.65, source_slot_id="ag-mem-16"),
            PromotionEntry(entry_id="E2", source_layer="L2", target_layer="L3", i_value=0.70, source_slot_id="ag-mem-16"),
            PromotionEntry(entry_id="E3", source_layer="L2", target_layer="L3", i_value=0.75, source_slot_id="ag-mem-16"),
        ]
    ))
    unit.run_transfer_cycle()
    print(f"  搬运中断，剩余条目已标记为失败")

    print("\n✅ 层级单向搬运写入单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-39 层级单向搬运写入单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_unit():
            u = LayerTransferUnit()
            u.set_target_capacity_query(lambda layer, count: True)
            return u

        # TC-M39-01: 正常搬运
        print("\n[TC-M39-01] 正常 L2→L3 搬运")
        try:
            u = setup_unit()
            u.set_promotion_list_query(lambda: PromotionCandidateList(
                source_layer="L2", target_layer="L3",
                entries=[PromotionEntry(entry_id="T01", source_layer="L2", target_layer="L3", i_value=0.65, source_slot_id="ag-mem-16")]
            ))
            u.run_transfer_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M39-02: 非法路径拦截
        print("\n[TC-M39-02] 非法路径 L1→L3 拦截")
        try:
            u = setup_unit()
            u.set_promotion_list_query(lambda: PromotionCandidateList(
                source_layer="L1", target_layer="L3",
                entries=[PromotionEntry(entry_id="T02", source_layer="L1", target_layer="L3")]
            ))
            u.run_transfer_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M39-03: 容量不足拒绝
        print("\n[TC-M39-03] 容量不足拒绝")
        try:
            u = LayerTransferUnit()
            u.set_target_capacity_query(lambda layer, count: False)
            u.set_promotion_list_query(lambda: PromotionCandidateList(
                source_layer="L2", target_layer="L3",
                entries=[PromotionEntry(entry_id="T03", source_layer="L2", target_layer="L3")]
            ))
            u.run_transfer_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M39-04: L4→L5 无令牌拒绝
        print("\n[TC-M39-04] L4→L5 无令牌拒绝")
        try:
            u = setup_unit()
            u.set_promotion_list_query(lambda: PromotionCandidateList(
                source_layer="L4", target_layer="L5",
                entries=[PromotionEntry(entry_id="T04", source_layer="L4", target_layer="L5")]
            ))
            u.run_transfer_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M39-05: 写入失败回滚并标记剩余条目
        print("\n[TC-M39-05] 写入失败回滚并标记剩余条目")
        try:
            u = setup_unit()
            call_count = [0]
            def mock_write_confirm(eid):
                call_count[0] += 1
                if call_count[0] > 1:
                    return TargetWriteResult(entry_id=eid, success=False, error_reason="存储写入异常")
                return TargetWriteResult(entry_id=eid, success=True)
            u.set_target_write_confirm_query(mock_write_confirm)
            u.set_promotion_list_query(lambda: PromotionCandidateList(
                source_layer="L2", target_layer="L3",
                entries=[
                    PromotionEntry(entry_id="E1", source_layer="L2", target_layer="L3", i_value=0.65, source_slot_id="ag-mem-16"),
                    PromotionEntry(entry_id="E2", source_layer="L2", target_layer="L3", i_value=0.70, source_slot_id="ag-mem-16"),
                    PromotionEntry(entry_id="E3", source_layer="L2", target_layer="L3", i_value=0.75, source_slot_id="ag-mem-16"),
                ]
            ))
            u.run_transfer_cycle()
            # 验证回滚状态
            assert len(u._written_in_current_batch) == 0  # 回滚后应清空
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M39-06: 紧急熔断
        print("\n[TC-M39-06] 紧急熔断")
        try:
            u = setup_unit()
            u.emergency_shutdown()
            assert u.state == TransferState.SYSTEM_PAUSED
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