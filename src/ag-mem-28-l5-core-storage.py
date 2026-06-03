#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-28
模块名称: L5核心层存储单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为漏斗二五层记忆存储架构的第五层（最高层），专门存储终身不可遗忘的安全底线
          经验、不可抗力事件及经人工锁定或高安全显著性（S≥0.9）直达写入的关键策略。
          L5是整个记忆中枢的"永久记忆区"，占漏斗二总容量的0.5%，默认情况下物理锁定，
          写入权限由ag-mem-29管控，访问权限由ag-mem-30管控。所有L5层条目默认不可删除、
          不可修改，仅提供只读查询服务。不参与晋升判定或遗忘策略，仅执行高安全经验的接收、
          存储与只读检索。

依赖模块:
    ag-mem-16(工具调用槽，S值直达L5), ag-mem-27(L4抽象提炼单元，高置信度规则推送),
    ag-mem-29(L5核心层安全规则硬锁定单元), ag-mem-30(L5核心层防篡改与只读管控单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-29, ag-mem-30, ag-mem-15~19(各场景分槽)

安全约束:
  S-01: L5层默认处于物理写保护锁定状态，任何写入操作必须持有ag-mem-29签发的有效临时解锁令牌
  S-02: L5层条目永久保留，不受任何遗忘策略约束，不得通过ag-mem-40或ag-mem-42自动删除
  S-03: L5层条目仅可人工删除（需ag-mem-29双重确认），任何自动化模块无权修改或删除L5数据
  S-04: L5层查询必须通过ag-mem-30的令牌验证，禁止无令牌访问
  S-05: 临时解锁令牌有效期30秒，超时自动作废，L5自动恢复锁定状态
  S-06: L5写入来源仅限"S值直达"、"L4推送"、"人工锁定"三种，其他来源一律拒绝
  S-07: S值直达写入必须满足S ≥ 0.9且结果标签为成功，失败经验不得通过S值直达进入L5
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import hmac
import hashlib


class StorageState(Enum):
    LOCKED_NORMAL = "locked_normal"
    TEMP_UNLOCKED = "temp_unlocked"
    CAPACITY_FULL = "capacity_full"
    LOCK_FAULT = "lock_fault"
    SYSTEM_PAUSED = "system_paused"


class WriteSource(Enum):
    S_DIRECT = "S值直达"
    L4_PUSH = "L4推送"
    MANUAL_LOCK = "人工锁定"


@dataclass
class ExperienceEntry:
    entry_id: str = ""
    source_slot_id: str = ""
    write_source: Optional[WriteSource] = None
    experience_data: Dict[str, Any] = field(default_factory=dict)
    i_value: float = 0.0
    s_value: float = 0.0
    v_value: float = 0.0
    c_value: float = 0.0
    result_label: str = "成功"
    readonly: bool = True
    editable: bool = False
    deletable: bool = False
    locked_at: float = field(default_factory=time.time)
    timestamp: float = field(default_factory=time.time)


@dataclass
class L5WriteRequest:
    request_id: str = ""
    entry: ExperienceEntry = field(default_factory=ExperienceEntry)
    write_source: WriteSource = WriteSource.S_DIRECT
    security_token: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class TempUnlockToken:
    token_id: str = ""
    max_write_entries: int = 1
    expires_at: float = 0.0
    signature: str = ""


@dataclass
class L5QueryRequest:
    request_id: str = ""
    query_conditions: Dict[str, Any] = field(default_factory=dict)
    query_token: str = ""
    max_results: int = 20


@dataclass
class QueryTokenValidationReceipt:
    token_valid: bool = False
    authorized_slot_id: str = ""
    authorized_operation: str = ""


@dataclass
class L5WriteConfirm:
    entry_id: str = ""
    success: bool = True
    l5_usage_pct: float = 0.0
    lock_status_restored: bool = False
    error_reason: str = ""


@dataclass
class L5WriteRejectNotice:
    request_id: str = ""
    entry_id: str = ""
    reject_reason: str = ""
    l5_state: str = ""


@dataclass
class L5QueryResult:
    matched_entries: List[ExperienceEntry] = field(default_factory=list)
    total_count: int = 0
    query_duration_ms: float = 0.0


@dataclass
class L5StatusReport:
    state: StorageState = StorageState.LOCKED_NORMAL
    total_entries: int = 0
    usage_pct: float = 0.0
    lock_status: str = "锁定"
    last_write_time: float = 0.0


class L5CoreStorage:
    # 容量配置
    L5_CAPACITY_RATIO = 0.005  # 占漏斗二总容量的0.5%
    MAX_ENTRIES = 200
    MAX_ENTRY_SIZE_BYTES = 30 * 1024
    WRITE_TIMEOUT_MS = 500
    CAPACITY_CRITICAL_THRESHOLD = 0.95
    TEMP_UNLOCK_MAX_DURATION_SEC = 30
    STATUS_REPORT_INTERVAL_SEC = 120

    # 安全令牌密钥
    TOKEN_SECRET = "l5-core-storage-secret-key"

    def __init__(self):
        self.module_id = "ag-mem-28"
        self.module_name = "L5核心层存储单元"
        self.version = "V1.0"

        self.state = StorageState.LOCKED_NORMAL
        self._entries: Dict[str, ExperienceEntry] = {}
        self._entry_count: int = 0
        self._temp_unlock_token: Optional[TempUnlockToken] = None
        self._token_write_remaining: int = 0  # 当前令牌剩余可写入条目数
        self._unlock_start_time: float = 0.0
        self._last_write_time: float = 0.0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_write_request = None
        self._query_temp_unlock_token = None
        self._query_query_request = None
        self._query_token_validation = None
        self._query_capacity_info = None
        self._query_lock_state_change = None

        self._publish_write_confirm = None
        self._publish_write_reject = None
        self._publish_query_result = None
        self._publish_status_report = None
        self._publish_token_verify_request = None
        self._publish_event_log = None
        self._publish_auto_lock_notice = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 默认锁定状态, 最大条目={self.MAX_ENTRIES}")

    # ========== 回调注入 ==========
    def set_write_request_query(self, callback: Callable[[], Optional[L5WriteRequest]]):
        self._query_write_request = callback

    def set_temp_unlock_token_query(self, callback: Callable[[], Optional[TempUnlockToken]]):
        self._query_temp_unlock_token = callback

    def set_query_request_query(self, callback: Callable[[], Optional[L5QueryRequest]]):
        self._query_query_request = callback

    def set_token_validation_query(self, callback: Callable[[str], Optional[QueryTokenValidationReceipt]]):
        self._query_token_validation = callback

    def set_capacity_info_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_capacity_info = callback

    def set_lock_state_change_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_lock_state_change = callback

    def set_write_confirm_publisher(self, callback: Callable[[L5WriteConfirm], None]):
        self._publish_write_confirm = callback

    def set_write_reject_publisher(self, callback: Callable[[L5WriteRejectNotice], None]):
        self._publish_write_reject = callback

    def set_query_result_publisher(self, callback: Callable[[L5QueryResult], None]):
        self._publish_query_result = callback

    def set_status_report_publisher(self, callback: Callable[[L5StatusReport], None]):
        self._publish_status_report = callback

    def set_token_verify_request_publisher(self, callback: Callable[[str, str], None]):
        self._publish_token_verify_request = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def set_auto_lock_notice_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_auto_lock_notice = callback

    # ========== 主循环 ==========
    def run_storage_cycle(self):
        now = time.time()

        if self.state == StorageState.SYSTEM_PAUSED:
            return

        # 检查临时解锁超时
        if self.state == StorageState.TEMP_UNLOCKED:
            if now - self._unlock_start_time >= self.TEMP_UNLOCK_MAX_DURATION_SEC:
                self._restore_lock("超时自动恢复")

        # 处理锁定状态变更通知
        lock_change = self._query_lock_state_change() if self._query_lock_state_change else None
        if lock_change:
            new_state = lock_change.get("new_lock_state")
            if new_state == "LOCKED":
                self.state = StorageState.LOCKED_NORMAL
                self._temp_unlock_token = None
                self._token_write_remaining = 0
            elif new_state == "UNLOCKED" and lock_change.get("reason") == "人工授权":
                token_data = lock_change.get("token")
                if token_data:
                    self._temp_unlock_token = TempUnlockToken(
                        token_id=token_data.get("token_id", ""),
                        max_write_entries=token_data.get("max_write_entries", 1),
                        expires_at=token_data.get("expires_at", now + self.TEMP_UNLOCK_MAX_DURATION_SEC),
                        signature=token_data.get("signature", "")
                    )
                    self._token_write_remaining = self._temp_unlock_token.max_write_entries
                    self._unlock_start_time = now
                    self.state = StorageState.TEMP_UNLOCKED

        # 更新临时解锁令牌
        token = self._query_temp_unlock_token() if self._query_temp_unlock_token else None
        if token and self.state != StorageState.TEMP_UNLOCKED:
            self._temp_unlock_token = token
            self._token_write_remaining = token.max_write_entries
            self._unlock_start_time = now
            self.state = StorageState.TEMP_UNLOCKED

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 处理写入请求
        write_request = self._query_write_request() if self._query_write_request else None
        if write_request:
            self._handle_write(write_request)
            return

        # 处理查询请求
        query_request = self._query_query_request() if self._query_query_request else None
        if query_request:
            self._handle_query(query_request)

    # ========== 写入处理 ==========
    def _handle_write(self, request: L5WriteRequest):
        # 校验L5是否可写入
        if self.state != StorageState.TEMP_UNLOCKED:
            self._reject_write(request, "L5处于锁定状态，写入需临时解锁令牌")
            return

        # 检查令牌写入配额
        if self._token_write_remaining <= 0:
            self._restore_lock("写入配额耗尽")
            self._reject_write(request, "令牌写入配额已耗尽")
            return

        # 校验安全令牌
        if not self._validate_token(request.security_token):
            self._reject_write(request, "安全令牌校验失败")
            return

        # 校验写入来源合法性
        if request.write_source not in WriteSource:
            self._reject_write(request, "非法写入来源")
            return

        # S值直达特殊校验
        if request.write_source == WriteSource.S_DIRECT:
            if request.entry.s_value < 0.9:
                self._reject_write(request, f"S值不满足L5直达条件（当前={request.entry.s_value:.2f}，要求≥0.9）")
                return
            if request.entry.result_label != "成功":
                self._reject_write(request, "失败经验不得通过S值直达进入L5")
                return

        # L4推送特殊校验
        if request.write_source == WriteSource.L4_PUSH:
            if request.entry.i_value < 0.85:
                self._reject_write(request, f"置信度不满足L5推送条件（当前={request.entry.i_value:.2f}，要求≥0.85）")
                return

        # 容量检查
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CAPACITY_CRITICAL_THRESHOLD or self._entry_count >= self.MAX_ENTRIES:
            self.state = StorageState.CAPACITY_FULL
            self._reject_write(request, "L5容量已满或达到最大条目数上限")
            return

        # 校验条目大小
        if len(str(request.entry.experience_data)) > self.MAX_ENTRY_SIZE_BYTES:
            self._reject_write(request, f"条目大小超过L5上限（{self.MAX_ENTRY_SIZE_BYTES}字节）")
            return

        # 写入L5
        entry = request.entry
        entry_id = f"L5-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        entry.entry_id = entry_id
        entry.write_source = request.write_source
        entry.readonly = True
        entry.editable = False
        entry.deletable = False
        entry.locked_at = time.time()

        self._entries[entry_id] = entry
        self._entry_count += 1
        self._last_write_time = time.time()

        # 消耗写入配额
        self._token_write_remaining -= 1
        if self._token_write_remaining <= 0:
            self._restore_lock("写入配额耗尽")
            lock_restored = True
        else:
            lock_restored = False

        confirm = L5WriteConfirm(
            entry_id=entry_id,
            success=True,
            l5_usage_pct=round(self._calculate_usage_pct(), 3),
            lock_status_restored=lock_restored
        )

        if self._publish_write_confirm:
            self._publish_write_confirm(confirm)

        self._log_event("L5_WRITE", {
            "entry_id": entry_id,
            "source": request.write_source.value,
            "s_value": entry.s_value,
            "remaining_writes": self._token_write_remaining
        })

    def _reject_write(self, request: L5WriteRequest, reason: str):
        if self._publish_write_reject:
            self._publish_write_reject(L5WriteRejectNotice(
                request_id=request.request_id,
                entry_id=request.entry.entry_id,
                reject_reason=reason,
                l5_state=self.state.value
            ))

    # ========== 查询处理 ==========
    def _handle_query(self, request: L5QueryRequest):
        start_time = time.time()

        # 验证查询令牌
        if self._publish_token_verify_request:
            self._publish_token_verify_request("ag-mem-30", request.query_token)

        validation = None
        if self._query_token_validation:
            validation = self._query_token_validation(request.query_token)

        if validation is None or not validation.token_valid:
            return  # 令牌无效，不返回任何数据

        # 检索匹配条目
        matched = []
        for entry in self._entries.values():
            keywords = request.query_conditions.get("keywords", [])
            if keywords:
                if not any(kw in str(entry.experience_data) for kw in keywords):
                    continue
            # 标记只读属性
            entry.readonly = True
            entry.editable = False
            entry.deletable = False
            matched.append(entry)

        matched.sort(key=lambda x: x.i_value, reverse=True)
        matched = matched[:request.max_results]

        elapsed = (time.time() - start_time) * 1000
        result = L5QueryResult(
            matched_entries=matched,
            total_count=len(matched),
            query_duration_ms=elapsed
        )

        if self._publish_query_result:
            self._publish_query_result(result)

    # ========== 锁定管理 ==========
    def _restore_lock(self, reason: str):
        self.state = StorageState.LOCKED_NORMAL
        self._temp_unlock_token = None
        self._token_write_remaining = 0
        self._log_event("LOCK_RESTORED", {"reason": reason})

        if self._publish_auto_lock_notice:
            self._publish_auto_lock_notice({
                "reason": reason,
                "timestamp": time.time()
            })

    def _validate_token(self, token_str: str) -> bool:
        if not self._temp_unlock_token:
            return False
        if time.time() > self._temp_unlock_token.expires_at:
            return False
        # 验证签名
        expected_sig = hmac.new(
            self.TOKEN_SECRET.encode(),
            f"{self._temp_unlock_token.token_id}{self._temp_unlock_token.max_write_entries}".encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        return token_str == expected_sig

    # ========== 辅助方法 ==========
    def _calculate_usage_pct(self) -> float:
        if self.MAX_ENTRIES == 0:
            return 0.0
        return min(self._entry_count / self.MAX_ENTRIES, 1.0)

    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report(L5StatusReport(
                state=self.state,
                total_entries=self._entry_count,
                usage_pct=self._calculate_usage_pct(),
                lock_status="锁定" if self.state == StorageState.LOCKED_NORMAL else "临时解锁",
                last_write_time=self._last_write_time
            ))

    def get_state(self) -> StorageState:
        return self.state

    def get_entry_count(self) -> int:
        return self._entry_count

    def emergency_shutdown(self):
        self.state = StorageState.SYSTEM_PAUSED
        # 立即恢复锁定
        self._temp_unlock_token = None
        self._token_write_remaining = 0
        print(f"[{self.module_id}] 紧急熔断，已恢复锁定")

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
    print("  Agent-mlnf-mem L5核心层存储单元 (ag-mem-28) 演示")
    print("=" * 70)

    storage = L5CoreStorage()

    print_separator("STEP 1: 锁定状态写入被拒")
    storage.set_write_request_query(lambda: L5WriteRequest(
        request_id="REQ-001",
        entry=ExperienceEntry(s_value=0.95, result_label="成功"),
        write_source=WriteSource.S_DIRECT,
        security_token="invalid"
    ))
    storage.run_storage_cycle()
    print(f"  状态: {storage.state.value}, 条目数: {storage.get_entry_count()}")

    print_separator("STEP 2: 临时解锁并写入（配额1）")
    token_id = "TOKEN-001"
    token_sig = hmac.new(
        L5CoreStorage.TOKEN_SECRET.encode(),
        f"{token_id}1".encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    storage.set_temp_unlock_token_query(lambda: TempUnlockToken(
        token_id=token_id, max_write_entries=1,
        expires_at=time.time() + 30, signature=token_sig
    ))
    storage.run_storage_cycle()
    print(f"  状态: {storage.state.value}")

    storage.set_write_request_query(lambda: L5WriteRequest(
        request_id="REQ-002",
        entry=ExperienceEntry(s_value=0.95, i_value=0.9, result_label="成功"),
        write_source=WriteSource.S_DIRECT,
        security_token=token_sig
    ))
    storage.run_storage_cycle()
    print(f"  写入成功，条目数: {storage.get_entry_count()}, 锁定状态: {storage.state.value}")

    print_separator("STEP 3: 配额耗尽后再次写入被拒")
    storage.set_write_request_query(lambda: L5WriteRequest(
        request_id="REQ-003",
        entry=ExperienceEntry(s_value=0.95, result_label="成功"),
        write_source=WriteSource.S_DIRECT,
        security_token=token_sig
    ))
    storage.run_storage_cycle()
    print(f"  状态: {storage.state.value}, 条目数: {storage.get_entry_count()} (应拒绝)")

    print("\n✅ L5核心层存储单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-28 L5核心层存储单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def make_token(max_entries=1):
            token_id = f"TOKEN-{uuid.uuid4().hex[:8]}"
            sig = hmac.new(
                L5CoreStorage.TOKEN_SECRET.encode(),
                f"{token_id}{max_entries}".encode(),
                hashlib.sha256
            ).hexdigest()[:16]
            return TempUnlockToken(
                token_id=token_id, max_write_entries=max_entries,
                expires_at=time.time() + 30, signature=sig
            ), sig

        def setup_storage(unlocked=False, max_entries=1):
            s = L5CoreStorage()
            if unlocked:
                token, sig = make_token(max_entries)
                s.set_temp_unlock_token_query(lambda: token)
                s.run_storage_cycle()
                s._test_token_sig = sig
            s.set_token_validation_query(lambda token: QueryTokenValidationReceipt(
                token_valid=True, authorized_slot_id="test", authorized_operation="只读查询"
            ))
            return s

        # TC-M28-01: 锁定状态写入拒绝
        print("\n[TC-M28-01] 锁定状态写入拒绝")
        try:
            s = setup_storage(unlocked=False)
            s.set_write_request_query(lambda: L5WriteRequest(
                entry=ExperienceEntry(s_value=0.95, result_label="成功"),
                write_source=WriteSource.S_DIRECT, security_token=""
            ))
            s.run_storage_cycle()
            assert s.get_entry_count() == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M28-02: 正常写入
        print("\n[TC-M28-02] 正常写入")
        try:
            s = setup_storage(unlocked=True)
            s.set_write_request_query(lambda: L5WriteRequest(
                entry=ExperienceEntry(s_value=0.95, i_value=0.9, result_label="成功"),
                write_source=WriteSource.S_DIRECT, security_token=s._test_token_sig
            ))
            s.run_storage_cycle()
            assert s.get_entry_count() == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M28-03: S值不足拒绝
        print("\n[TC-M28-03] S值不足拒绝")
        try:
            s = setup_storage(unlocked=True)
            s.set_write_request_query(lambda: L5WriteRequest(
                entry=ExperienceEntry(s_value=0.7, result_label="成功"),
                write_source=WriteSource.S_DIRECT, security_token=s._test_token_sig
            ))
            s.run_storage_cycle()
            assert s.get_entry_count() == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M28-04: 失败经验拒绝
        print("\n[TC-M28-04] 失败经验S值直达拒绝")
        try:
            s = setup_storage(unlocked=True)
            s.set_write_request_query(lambda: L5WriteRequest(
                entry=ExperienceEntry(s_value=0.95, result_label="失败"),
                write_source=WriteSource.S_DIRECT, security_token=s._test_token_sig
            ))
            s.run_storage_cycle()
            assert s.get_entry_count() == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M28-05: 写入配额耗尽后拒绝
        print("\n[TC-M28-05] 写入配额耗尽后拒绝")
        try:
            s = setup_storage(unlocked=True, max_entries=1)
            # 第一次写入成功
            s.set_write_request_query(lambda: L5WriteRequest(
                entry=ExperienceEntry(s_value=0.95, i_value=0.9, result_label="成功"),
                write_source=WriteSource.S_DIRECT, security_token=s._test_token_sig
            ))
            s.run_storage_cycle()
            # 第二次应拒绝
            s.set_write_request_query(lambda: L5WriteRequest(
                entry=ExperienceEntry(s_value=0.95, i_value=0.9, result_label="成功"),
                write_source=WriteSource.S_DIRECT, security_token=s._test_token_sig
            ))
            s.run_storage_cycle()
            assert s.get_entry_count() == 1
            assert s.state == StorageState.LOCKED_NORMAL  # 自动锁定
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M28-06: 紧急熔断
        print("\n[TC-M28-06] 紧急熔断")
        try:
            s = setup_storage(unlocked=True)
            s.emergency_shutdown()
            assert s.state == StorageState.SYSTEM_PAUSED
            assert s._temp_unlock_token is None
            assert s._token_write_remaining == 0
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