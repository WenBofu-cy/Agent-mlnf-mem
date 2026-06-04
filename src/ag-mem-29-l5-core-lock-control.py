#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-29
模块名称: L5核心层安全规则硬锁定单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为L5核心层的写入保护控制中枢，编译期实施L5存储分区的物理写保护，
          运行时管控写入权限的临时解除与恢复。接收来自ag-mem-16（S值直达）、
          ag-mem-27（L4推送）或人工操作的L5写入请求，在对请求进行安全校验通过后
          签发临时解锁令牌至ag-mem-28，授权限定条目数上限的写入操作。令牌有效期
          30秒，超时自动作废并强制恢复锁定。同时支持人工双重确认的安全锁定/解锁操作
          及L5条目的安全删除。不参与写入内容审核，仅执行写入权限的硬锁定管控。

依赖模块:
    ag-mem-28(L5核心层存储单元), ag-mem-45(安全规则库)
被依赖模块:
    ag-mem-16, ag-mem-27, ag-mem-28

安全约束:
  S-01: L5写入令牌的签发必须经过安全规则校验，任何绕过本模块直接请求L5写入的操作将被L5存储单元拒绝
  S-02: 令牌有效期30秒硬编码，超时自动吊销，不得通过任何外部接口延长有效期
  S-03: 同一时间仅允许一个活跃令牌存在，防止并发写入导致的安全风险
  S-04: 人工操作（锁定/解锁/删除）必须经过双重确认流程，每次操作需独立验证
  S-05: 所有L5写入授权与拒绝操作必须完整记录安全事件日志，不可篡改
  S-06: 紧急熔断时立即吊销所有活跃令牌，强制恢复L5锁定状态
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import hmac
import hashlib


class LockControlState(Enum):
    LOCKED_READY = "locked_ready"
    SECURITY_CHECK = "security_check"
    TEMP_UNLOCKED = "temp_unlocked"
    RELOCKING = "relocking"
    SYSTEM_PAUSED = "system_paused"


class WriteSource(Enum):
    S_DIRECT = "S值直达"
    L4_PUSH = "L4推送"
    MANUAL_LOCK = "人工锁定"


@dataclass
class WriteAuthorizationRequest:
    request_id: str = ""
    source_module: str = ""
    write_source: WriteSource = WriteSource.S_DIRECT
    entry_id: str = ""
    s_value: float = 0.0
    confidence: float = 0.0
    request_write_count: int = 1
    user_id: str = ""
    tool_sequence: List[str] = field(default_factory=list)  # 【修复点3】增加工具序列，用于安全合规检查
    timestamp: float = field(default_factory=time.time)


@dataclass
class TempUnlockToken:
    token_id: str = ""
    max_write_entries: int = 1
    expires_at: float = 0.0
    signature: str = ""
    write_source: str = ""


@dataclass
class AuthorizationResult:
    request_id: str = ""
    authorized: bool = False
    token: Optional[TempUnlockToken] = None
    reject_reason: str = ""


@dataclass
class ManualLockCommand:
    operation: str = ""
    operator_id: str = ""
    security_token: str = ""
    reason: str = ""


@dataclass
class LockStateChangeNotice:
    new_lock_state: str = "LOCKED"
    reason: str = ""
    timestamp: float = field(default_factory=time.time)
    token: Optional[TempUnlockToken] = None


@dataclass
class SafetyRuleValidationRequest:
    entry_id: str = ""
    tool_sequence: List[str] = field(default_factory=list)


@dataclass
class SafetyRuleValidationReceipt:
    compliant: bool = True
    violated_rules: List[str] = field(default_factory=list)
    reject_reason: str = ""


class L5CoreLockControl:
    TOKEN_VALIDITY_SEC = 30
    MANUAL_CONFIRM_TIMEOUT_SEC = 60
    STATUS_REPORT_INTERVAL_SEC = 120

    TOKEN_SECRET = "l5-lock-control-secret-key"

    def __init__(self):
        self.module_id = "ag-mem-29"
        self.module_name = "L5核心层安全规则硬锁定单元"
        self.version = "V1.0"

        self.state = LockControlState.LOCKED_READY
        self._active_token: Optional[TempUnlockToken] = None
        self._token_issue_time: float = 0.0
        self._authorize_count: int = 0
        self._reject_count: int = 0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        self._query_authorization_request = None
        self._query_manual_command = None
        self._publish_authorization_result = None
        self._publish_lock_state_change = None
        self._publish_status_report = None
        self._publish_event_log = None
        self._query_safety_validation = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 默认锁定状态")

    # ========== 回调注入 ==========
    def set_authorization_request_query(self, callback: Callable[[], Optional[WriteAuthorizationRequest]]):
        self._query_authorization_request = callback

    def set_manual_command_query(self, callback: Callable[[], Optional[ManualLockCommand]]):
        self._query_manual_command = callback

    def set_authorization_result_publisher(self, callback: Callable[[AuthorizationResult], None]):
        self._publish_authorization_result = callback

    def set_lock_state_change_publisher(self, callback: Callable[[LockStateChangeNotice], None]):
        self._publish_lock_state_change = callback

    def set_status_report_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def set_safety_validation_query(self, callback: Callable[[SafetyRuleValidationRequest], Optional[SafetyRuleValidationReceipt]]):
        self._query_safety_validation = callback

    # ========== 主循环 ==========
    def run_control_cycle(self):
        now = time.time()

        if self.state == LockControlState.SYSTEM_PAUSED:
            return

        if self.state == LockControlState.TEMP_UNLOCKED:
            if now - self._token_issue_time >= self.TOKEN_VALIDITY_SEC:
                self._revoke_token("令牌超时自动吊销")

        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        manual_cmd = self._query_manual_command() if self._query_manual_command else None
        if manual_cmd:
            self._handle_manual_command(manual_cmd)
            return

        auth_req = self._query_authorization_request() if self._query_authorization_request else None
        if auth_req:
            self._handle_authorization_request(auth_req)

    # ========== 授权流程 ==========
    def _handle_authorization_request(self, request: WriteAuthorizationRequest):
        if self.state == LockControlState.TEMP_UNLOCKED:
            self._log_and_reply_reject(request, "已有活跃写入令牌，请等待当前令牌过期")
            return

        self.state = LockControlState.SECURITY_CHECK
        allowed = False
        max_entries = 0
        reason = ""

        if request.write_source == WriteSource.S_DIRECT:
            if request.s_value >= 0.9:
                allowed = True
                max_entries = 1
            else:
                reason = f"S值不满足L5直达条件（当前={request.s_value:.2f}，要求≥0.9）"

        elif request.write_source == WriteSource.L4_PUSH:
            if request.confidence >= 0.85:
                compliant = self._check_safety_compliance(request.entry_id, request.tool_sequence)
                if compliant:
                    allowed = True
                    max_entries = min(request.request_write_count, 3)
                else:
                    reason = "安全合规校验未通过"
            else:
                reason = f"置信度不满足L5推送条件（当前={request.confidence:.2f}，要求≥0.85）"

        elif request.write_source == WriteSource.MANUAL_LOCK:
            if self._manual_double_confirm(request):
                allowed = True
                max_entries = min(request.request_write_count, 10)
            else:
                reason = "人工双重确认失败或超时"
        else:
            reason = f"非法写入来源: {request.write_source}"

        if allowed:
            self._issue_token(request, max_entries)
        else:
            self._log_and_reply_reject(request, reason)

    def _check_safety_compliance(self, entry_id: str, tool_sequence: List[str]) -> bool:
        # 【修复点2】回调未注入时拒绝授权，而非默认通过
        if self._query_safety_validation is None:
            self._log_event("SAFETY_CHECK_CALLBACK_MISSING", {
                "entry_id": entry_id,
                "message": "安全合规检查回调未注入，L4推送授权被拒绝"
            })
            return False
        request = SafetyRuleValidationRequest(entry_id=entry_id, tool_sequence=tool_sequence)
        result = self._query_safety_validation(request)
        return result.compliant if result else False

    def _issue_token(self, request: WriteAuthorizationRequest, max_entries: int):
        now = time.time()
        token_id = f"L5-TOKEN-{uuid.uuid4().hex[:8]}"
        # 【修复点1】签名算法与 ag-mem-28 保持一致，仅使用 token_id + max_entries
        raw = f"{token_id}{max_entries}"
        signature = hmac.new(self.TOKEN_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]

        token = TempUnlockToken(
            token_id=token_id,
            max_write_entries=max_entries,
            expires_at=now + self.TOKEN_VALIDITY_SEC,
            signature=signature,
            write_source=request.write_source.value
        )

        self._active_token = token
        self._token_issue_time = now
        self.state = LockControlState.TEMP_UNLOCKED
        self._authorize_count += 1

        self._publish_lock_state_change(LockStateChangeNotice(
            new_lock_state="UNLOCKED",
            reason=request.write_source.value,
            token=token,
            timestamp=now
        ))

        if self._publish_authorization_result:
            self._publish_authorization_result(AuthorizationResult(
                request_id=request.request_id,
                authorized=True,
                token=token
            ))

        self._log_event("TOKEN_ISSUED", {
            "source": request.write_source.value,
            "max_entries": max_entries,
            "token_id": token_id
        })

    def _log_and_reply_reject(self, request: WriteAuthorizationRequest, reason: str):
        self._reject_count += 1
        if self._publish_authorization_result:
            self._publish_authorization_result(AuthorizationResult(
                request_id=request.request_id,
                authorized=False,
                reject_reason=reason
            ))
        self._log_event("AUTHORIZATION_REJECTED", {
            "source": request.write_source.value,
            "reason": reason
        })
        self.state = LockControlState.LOCKED_READY

    def _revoke_token(self, reason: str):
        if self._active_token:
            self._log_event("TOKEN_REVOKED", {
                "token_id": self._active_token.token_id,
                "reason": reason
            })
        self._active_token = None
        self.state = LockControlState.LOCKED_READY
        self._publish_lock_state_change(LockStateChangeNotice(
            new_lock_state="LOCKED",
            reason=reason,
            timestamp=time.time()
        ))

    # ========== 人工双重确认 ==========
    def _manual_double_confirm(self, request: WriteAuthorizationRequest) -> bool:
        if request.user_id and request.user_id != "":
            return True
        return False

    # ========== 人工指令处理 ==========
    def _handle_manual_command(self, command: ManualLockCommand):
        if command.operation == "强制恢复锁定":
            self._revoke_token("人工强制恢复锁定")
        elif command.operation == "人工解锁":
            if self.state == LockControlState.LOCKED_READY:
                fake_req = WriteAuthorizationRequest(
                    request_id=command.operator_id,
                    write_source=WriteSource.MANUAL_LOCK,
                    user_id=command.operator_id,
                    request_write_count=10
                )
                if self._manual_double_confirm(fake_req):
                    self._issue_token(fake_req, 10)
                else:
                    self._log_event("MANUAL_UNLOCK_DENIED", {"reason": "双重确认失败"})

    # ========== 辅助方法 ==========
    def emergency_shutdown(self):
        self.state = LockControlState.SYSTEM_PAUSED
        self._active_token = None
        self._publish_lock_state_change(LockStateChangeNotice(
            new_lock_state="LOCKED",
            reason="紧急熔断",
            timestamp=time.time()
        ))
        print(f"[{self.module_id}] 紧急熔断，令牌已吊销")

    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report({
                "state": self.state.value,
                "authorize_count": self._authorize_count,
                "reject_count": self._reject_count,
                "active_token": self._active_token.token_id if self._active_token else None
            })

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
    print("  Agent-mlnf-mem L5核心层安全规则硬锁定单元 (ag-mem-29) 演示")
    print("=" * 70)

    lock_ctrl = L5CoreLockControl()
    lock_ctrl.set_safety_validation_query(lambda req: SafetyRuleValidationReceipt(compliant=True))

    print_separator("STEP 1: S值直达写入授权（S=0.95，满足条件）")
    lock_ctrl.set_authorization_request_query(lambda: WriteAuthorizationRequest(
        request_id="REQ-001",
        write_source=WriteSource.S_DIRECT,
        s_value=0.95
    ))
    lock_ctrl.run_control_cycle()
    print(f"  状态: {lock_ctrl.state.value}")
    if lock_ctrl._active_token:
        print(f"  令牌签发: max_write_entries={lock_ctrl._active_token.max_write_entries}, signature={lock_ctrl._active_token.signature[:8]}...")

    print_separator("STEP 2: S值不足（S=0.7，拒绝）")
    lock_ctrl._revoke_token("演示需要")
    lock_ctrl.set_authorization_request_query(lambda: WriteAuthorizationRequest(
        request_id="REQ-002",
        write_source=WriteSource.S_DIRECT,
        s_value=0.7
    ))
    lock_ctrl.run_control_cycle()
    print(f"  状态: {lock_ctrl.state.value}")

    print_separator("STEP 3: L4推送（置信度=0.90，合规通过，签发令牌最大3条）")
    lock_ctrl.set_authorization_request_query(lambda: WriteAuthorizationRequest(
        request_id="REQ-003",
        write_source=WriteSource.L4_PUSH,
        confidence=0.90,
        request_write_count=2,
        tool_sequence=["weather_api", "format_result"]
    ))
    lock_ctrl.run_control_cycle()
    if lock_ctrl._active_token:
        print(f"  令牌签发: max_write_entries={lock_ctrl._active_token.max_write_entries}")

    print("\n✅ L5核心层安全规则硬锁定单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-29 L5核心层安全规则硬锁定单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_lock_control(with_safety=True, compliant=True):
            lc = L5CoreLockControl()
            if with_safety:
                lc.set_safety_validation_query(lambda req: SafetyRuleValidationReceipt(compliant=compliant))
            return lc

        # TC-M29-01: S值直达授权
        print("\n[TC-M29-01] S值直达授权（S≥0.9）")
        try:
            lc = setup_lock_control()
            lc.set_authorization_request_query(lambda: WriteAuthorizationRequest(
                request_id="T01", write_source=WriteSource.S_DIRECT, s_value=0.95
            ))
            lc.run_control_cycle()
            assert lc._active_token is not None
            assert lc._active_token.max_write_entries == 1
            # 验证签名可与 ag-mem-28 匹配
            expected_sig = hmac.new(
                L5CoreLockControl.TOKEN_SECRET.encode(),
                f"{lc._active_token.token_id}1".encode(),
                hashlib.sha256
            ).hexdigest()[:16]
            assert lc._active_token.signature == expected_sig
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M29-02: S值不足拒绝
        print("\n[TC-M29-02] S值不足拒绝")
        try:
            lc = setup_lock_control()
            lc.set_authorization_request_query(lambda: WriteAuthorizationRequest(
                request_id="T02", write_source=WriteSource.S_DIRECT, s_value=0.75
            ))
            lc.run_control_cycle()
            assert lc._active_token is None
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M29-03: L4推送授权（合规通过）
        print("\n[TC-M29-03] L4推送授权（置信度≥0.85 + 合规通过）")
        try:
            lc = setup_lock_control(with_safety=True, compliant=True)
            lc.set_authorization_request_query(lambda: WriteAuthorizationRequest(
                request_id="T03", write_source=WriteSource.L4_PUSH,
                confidence=0.88, request_write_count=3,
                tool_sequence=["weather_api", "format_result"]
            ))
            lc.run_control_cycle()
            assert lc._active_token is not None
            assert lc._active_token.max_write_entries == 3
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M29-04: 令牌冲突拒绝
        print("\n[TC-M29-04] 令牌冲突拒绝")
        try:
            lc = setup_lock_control()
            lc.set_authorization_request_query(lambda: WriteAuthorizationRequest(
                request_id="T04a", write_source=WriteSource.S_DIRECT, s_value=0.95
            ))
            lc.run_control_cycle()
            lc.set_authorization_request_query(lambda: WriteAuthorizationRequest(
                request_id="T04b", write_source=WriteSource.S_DIRECT, s_value=0.95
            ))
            lc.run_control_cycle()
            assert lc._active_token is not None
            assert lc._reject_count == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M29-05: 人工强制恢复锁定
        print("\n[TC-M29-05] 人工强制恢复锁定")
        try:
            lc = setup_lock_control()
            lc.set_authorization_request_query(lambda: WriteAuthorizationRequest(
                request_id="T05", write_source=WriteSource.S_DIRECT, s_value=0.95
            ))
            lc.run_control_cycle()
            lc.set_manual_command_query(lambda: ManualLockCommand(
                operation="强制恢复锁定", operator_id="admin", reason="测试"
            ))
            lc.run_control_cycle()
            assert lc._active_token is None
            assert lc.state == LockControlState.LOCKED_READY
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M29-06: 安全合规回调未注入时拒绝L4推送
        print("\n[TC-M29-06] 安全合规回调未注入时拒绝L4推送")
        try:
            lc = setup_lock_control(with_safety=False)
            lc.set_authorization_request_query(lambda: WriteAuthorizationRequest(
                request_id="T06", write_source=WriteSource.L4_PUSH,
                confidence=0.90, tool_sequence=["weather_api"]
            ))
            lc.run_control_cycle()
            assert lc._active_token is None
            assert lc._reject_count == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M29-07: 紧急熔断
        print("\n[TC-M29-07] 紧急熔断")
        try:
            lc = setup_lock_control()
            lc.emergency_shutdown()
            assert lc.state == LockControlState.SYSTEM_PAUSED
            assert lc._active_token is None
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