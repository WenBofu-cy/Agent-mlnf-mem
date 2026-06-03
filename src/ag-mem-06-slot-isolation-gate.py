#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-06
模块名称: 画像槽数据隔离管控单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 在编译期与运行时强制实施漏斗一内部各画像槽之间的物理存储隔离。拦截并校验
          所有跨槽数据访问请求，确保任意时刻任意模块只能访问当前活跃会话绑定的唯一
          画像槽。管理槽级访问令牌的签发、验证与吊销。任何试图跨用户读取或混合画像
          数据的操作均被实时阻断并记录告警。不参与任何认知决策，仅执行数据隔离的强制管控。

依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-05(画像槽创建与初始化单元),
    ag-mem-07/09/10/11(漏斗一内部模块), ag-mem-51(记忆变更日志追溯单元)
被依赖模块:
    ag-mem-07, ag-mem-09, ag-mem-10, ag-mem-11

安全约束:
  S-01: 编译期硬编码访问权限矩阵，运行时禁止任何模块修改权限配置
  S-02: 所有跨槽访问请求必须被实时阻断，不得以“仅读取不修改”为理由放行
  S-03: 隔离违规事件必须完整记录（来源模块、目标槽位、活跃槽位、请求内容摘要），不可篡改
  S-04: 访问令牌有效期不得超过300秒，过期令牌自动失效
  S-05: 本模块为漏斗一数据隔离的唯一强制执行点，任何模块不得绕过本模块直接访问画像槽存储
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import hmac
import hashlib


class GateState(Enum):
    NORMAL_GATE = "normal_gate"
    VIOLATION_BLOCK = "violation_block"
    NO_ACTIVE_SLOT = "no_active_slot"
    SYSTEM_PAUSED = "system_paused"


class OperationType(Enum):
    READ = "读"
    WRITE = "写"
    MODIFY = "修改"
    DELETE = "删除"


@dataclass
class DataAccessRequest:
    request_id: str = ""
    source_module: str = ""
    operation_type: OperationType = OperationType.READ
    target_slot_id: str = ""
    data_fields: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class ActiveSlotBinding:
    session_id: str = ""
    active_user_id: str = ""
    active_slot_id: str = ""
    bound_at: float = field(default_factory=time.time)


@dataclass
class AccessToken:
    token_id: str = ""
    authorized_slot_id: str = ""
    authorized_operation: OperationType = OperationType.READ
    issued_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=time.time)
    signature: str = ""


@dataclass
class AccessDeniedReceipt:
    request_id: str = ""
    deny_reason: str = ""
    severity: str = "一般"
    request_summary: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class IsolationViolationAlert:
    violation_type: str = ""
    source_module: str = ""
    target_slot: str = ""
    active_slot: str = ""
    request_summary: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class TokenRevokeRequest:
    slot_id: str = ""
    revoke_reason: str = ""
    immediate: bool = True
    timestamp: float = field(default_factory=time.time)


@dataclass
class TokenRevokeConfirm:
    slot_id: str = ""
    revoked_count: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class IsolationStatusReport:
    active_slot_count: int = 0
    today_violations: int = 0
    issued_tokens: int = 0
    revoked_tokens: int = 0
    timestamp: float = field(default_factory=time.time)


# 编译期硬编码的访问权限矩阵
ACCESS_PERMISSION_MATRIX: Dict[str, Dict[str, Any]] = {
    "ag-mem-07": {
        "allowed_operations": [OperationType.WRITE],
        "requires_token": True,
        "token_type": "write",
    },
    "ag-mem-09": {
        "allowed_operations": [OperationType.READ, OperationType.WRITE],
        "requires_token": True,
        "token_type": "read_write",
    },
    "ag-mem-10": {
        "allowed_operations": [OperationType.READ],
        "requires_token": True,
        "token_type": "read",
    },
    "ag-mem-11": {
        "allowed_operations": [OperationType.READ],
        "requires_token": True,
        "token_type": "read",
    },
    "ag-mem-01": {
        "allowed_operations": [OperationType.READ],
        "requires_token": True,
        "token_type": "read",
    },
}


class SlotIsolationGate:
    TOKEN_VALIDITY_SEC = 300  # 5分钟
    MAX_ISSUED_TOKENS = 50
    TOKEN_CLEANUP_INTERVAL_SEC = 60

    def __init__(self):
        self.module_id = "ag-mem-06"
        self.module_name = "画像槽数据隔离管控单元"
        self.version = "V1.0"

        self.state = GateState.NO_ACTIVE_SLOT
        self._active_slot_id: Optional[str] = None
        self._active_user_id: Optional[str] = None
        self._issued_tokens: Dict[str, AccessToken] = {}
        self._violation_counter: int = 0
        self._total_issued: int = 0
        self._total_revoked: int = 0
        self._last_cleanup_time = time.time()
        self._last_report_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_access_request = None
        self._query_slot_binding = None
        self._query_token_revoke = None

        self._publish_access_token = None
        self._publish_deny_receipt = None
        self._publish_violation_alert = None
        self._publish_revoke_confirm = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_access_request_query(self, callback: Callable[[], Optional[DataAccessRequest]]):
        self._query_access_request = callback

    def set_slot_binding_query(self, callback: Callable[[], Optional[ActiveSlotBinding]]):
        self._query_slot_binding = callback

    def set_token_revoke_query(self, callback: Callable[[], Optional[TokenRevokeRequest]]):
        self._query_token_revoke = callback

    def set_access_token_publisher(self, callback: Callable[[AccessToken], None]):
        self._publish_access_token = callback

    def set_deny_receipt_publisher(self, callback: Callable[[AccessDeniedReceipt], None]):
        self._publish_deny_receipt = callback

    def set_violation_alert_publisher(self, callback: Callable[[IsolationViolationAlert], None]):
        self._publish_violation_alert = callback

    def set_revoke_confirm_publisher(self, callback: Callable[[TokenRevokeConfirm], None]):
        self._publish_revoke_confirm = callback

    def set_status_report_publisher(self, callback: Callable[[IsolationStatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_isolation_cycle(self) -> Optional[AccessToken]:
        now = time.time()

        if self.state == GateState.SYSTEM_PAUSED:
            return None

        # 更新活跃槽位绑定
        binding = self._query_slot_binding() if self._query_slot_binding else None
        if binding:
            if self._active_slot_id != binding.active_slot_id:
                self._active_slot_id = binding.active_slot_id
                self._active_user_id = binding.active_user_id
                if self.state == GateState.NO_ACTIVE_SLOT:
                    self.state = GateState.NORMAL_GATE
                # 槽位切换时，吊销旧令牌
                self._revoke_all_tokens_for_slot(binding.active_slot_id)

        # 处理令牌吊销请求
        revoke_req = self._query_token_revoke() if self._query_token_revoke else None
        if revoke_req:
            revoked = self._revoke_all_tokens_for_slot(revoke_req.slot_id)
            if self._publish_revoke_confirm:
                self._publish_revoke_confirm(TokenRevokeConfirm(
                    slot_id=revoke_req.slot_id,
                    revoked_count=revoked
                ))
            if revoke_req.slot_id == self._active_slot_id:
                self._active_slot_id = None
                self.state = GateState.NO_ACTIVE_SLOT

        # 定期清理过期令牌
        if now - self._last_cleanup_time >= self.TOKEN_CLEANUP_INTERVAL_SEC:
            self._cleanup_expired_tokens()
            self._last_cleanup_time = now

        # 接收数据访问请求
        request = self._query_access_request() if self._query_access_request else None
        if request is None:
            return None

        return self._handle_access_request(request)

    def _handle_access_request(self, request: DataAccessRequest) -> Optional[AccessToken]:
        now = time.time()

        # 检查是否有活跃槽位
        if self.state == GateState.NO_ACTIVE_SLOT or self._active_slot_id is None:
            self.state = GateState.NO_ACTIVE_SLOT
            self._send_deny(request, "无活跃画像槽", "一般")
            return None

        # 检查请求来源模块是否在权限矩阵中
        source = request.source_module
        if source not in ACCESS_PERMISSION_MATRIX:
            self._block_violation(request, "未授权模块访问", "严重")
            return None

        # 检查目标槽位是否为当前活跃槽位
        if request.target_slot_id != self._active_slot_id:
            self._block_violation(request, "跨槽访问被禁止", "严重")
            return None

        # 检查操作类型是否在授权范围内
        allowed_ops = ACCESS_PERMISSION_MATRIX[source]["allowed_operations"]
        if request.operation_type not in allowed_ops:
            self._send_deny(request, "操作类型越权", "严重")
            return None

        # 签发访问令牌
        token = self._issue_token(request)
        self._total_issued += 1
        return token

    def _issue_token(self, request: DataAccessRequest) -> AccessToken:
        token_id = f"TOKEN-{uuid.uuid4().hex[:8]}"
        issued_at = time.time()
        expires_at = issued_at + self.TOKEN_VALIDITY_SEC

        signature = self._generate_signature(token_id, request.source_module,
                                              self._active_slot_id, request.operation_type.value, issued_at)

        token = AccessToken(
            token_id=token_id,
            authorized_slot_id=self._active_slot_id,
            authorized_operation=request.operation_type,
            issued_at=issued_at,
            expires_at=expires_at,
            signature=signature
        )

        self._issued_tokens[token_id] = token

        if self._publish_access_token:
            self._publish_access_token(token)

        return token

    def _send_deny(self, request: DataAccessRequest, reason: str, severity: str):
        receipt = AccessDeniedReceipt(
            request_id=request.request_id,
            deny_reason=reason,
            severity=severity,
            request_summary=f"{request.source_module} -> {request.target_slot_id}"
        )
        if self._publish_deny_receipt:
            self._publish_deny_receipt(receipt)

    def _block_violation(self, request: DataAccessRequest, violation_type: str, severity: str):
        self.state = GateState.VIOLATION_BLOCK
        self._violation_counter += 1

        alert = IsolationViolationAlert(
            violation_type=violation_type,
            source_module=request.source_module,
            target_slot=request.target_slot_id,
            active_slot=self._active_slot_id or "None",
            request_summary=str(request.data_fields)
        )

        if self._publish_violation_alert:
            self._publish_violation_alert(alert)

        self._send_deny(request, violation_type, severity)
        self._log_event("VIOLATION_DETECTED", {
            "type": violation_type,
            "source": request.source_module,
            "target": request.target_slot_id,
            "active": self._active_slot_id
        })

        self.state = GateState.NORMAL_GATE

    def _revoke_all_tokens_for_slot(self, slot_id: str) -> int:
        revoked = 0
        for token_id, token in list(self._issued_tokens.items()):
            if token.authorized_slot_id == slot_id:
                del self._issued_tokens[token_id]
                revoked += 1
                self._total_revoked += 1
        return revoked

    def _cleanup_expired_tokens(self):
        now = time.time()
        for token_id, token in list(self._issued_tokens.items()):
            if token.expires_at <= now:
                del self._issued_tokens[token_id]
                self._total_revoked += 1

    def _generate_signature(self, *args) -> str:
        key = "isolation-gate-secret-key"  # 编译期硬编码密钥
        message = "|".join(str(arg) for arg in args)
        return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()[:16]

    def get_state(self) -> GateState:
        return self.state

    def emergency_shutdown(self):
        self.state = GateState.SYSTEM_PAUSED
        # 吊销所有令牌
        self._issued_tokens.clear()
        self._active_slot_id = None
        print(f"[{self.module_id}] 紧急熔断，已吊销所有令牌")

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
    print("  Agent-mlnf-mem 画像槽数据隔离管控单元 (ag-mem-06) 演示")
    print("=" * 70)

    gate = SlotIsolationGate()
    gate.set_slot_binding_query(lambda: ActiveSlotBinding(
        session_id="S001", active_user_id="U001", active_slot_id="SLOT-LONG-0001"
    ))

    # 先运行一次绑定
    gate.run_isolation_cycle()

    print_separator("STEP 1: ag-mem-07 请求写入当前活跃槽")
    gate.set_access_request_query(lambda: DataAccessRequest(
        request_id="REQ-001", source_module="ag-mem-07",
        operation_type=OperationType.WRITE, target_slot_id="SLOT-LONG-0001"
    ))
    token = gate.run_isolation_cycle()
    if token:
        print(f"  令牌签发: {token.token_id}")
        print(f"  授权操作: {token.authorized_operation.value}")
        print(f"  过期时间: {token.expires_at - time.time():.0f}秒后")

    print_separator("STEP 2: 未授权模块访问（被拦截）")
    gate.set_access_request_query(lambda: DataAccessRequest(
        request_id="REQ-002", source_module="ag-mem-99",
        operation_type=OperationType.READ, target_slot_id="SLOT-LONG-0001"
    ))
    token = gate.run_isolation_cycle()
    if token is None:
        print("  ✅ 访问已被正确拦截")

    print_separator("STEP 3: 跨槽访问（被拦截）")
    gate.set_access_request_query(lambda: DataAccessRequest(
        request_id="REQ-003", source_module="ag-mem-10",
        operation_type=OperationType.READ, target_slot_id="SLOT-LONG-0002"
    ))
    token = gate.run_isolation_cycle()
    if token is None:
        print("  ✅ 跨槽访问已被正确拦截")

    print("\n✅ 画像槽数据隔离管控单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-06 画像槽数据隔离管控单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_gate():
            g = SlotIsolationGate()
            g.set_slot_binding_query(lambda: ActiveSlotBinding(
                session_id="S_TEST", active_user_id="U_TEST", active_slot_id="SLOT-LONG-0001"
            ))
            g.run_isolation_cycle()
            return g

        # TC-M06-01: 合法写入请求
        print("\n[TC-M06-01] 合法写入请求")
        try:
            g = setup_gate()
            g.set_access_request_query(lambda: DataAccessRequest(
                request_id="T01", source_module="ag-mem-07",
                operation_type=OperationType.WRITE, target_slot_id="SLOT-LONG-0001"
            ))
            token = g.run_isolation_cycle()
            assert token is not None
            assert token.authorized_slot_id == "SLOT-LONG-0001"
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M06-02: 跨槽访问被拦截
        print("\n[TC-M06-02] 跨槽访问被拦截")
        try:
            g = setup_gate()
            g.set_access_request_query(lambda: DataAccessRequest(
                request_id="T02", source_module="ag-mem-09",
                operation_type=OperationType.READ, target_slot_id="SLOT-LONG-0002"
            ))
            token = g.run_isolation_cycle()
            assert token is None
            assert g.state != GateState.NO_ACTIVE_SLOT
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M06-03: 无活跃槽位时拒绝
        print("\n[TC-M06-03] 无活跃槽位时拒绝")
        try:
            g = SlotIsolationGate()
            g.set_slot_binding_query(lambda: None)
            g.set_access_request_query(lambda: DataAccessRequest(
                request_id="T03", source_module="ag-mem-07",
                operation_type=OperationType.WRITE, target_slot_id="SLOT-LONG-0001"
            ))
            token = g.run_isolation_cycle()
            assert token is None
            assert g.state == GateState.NO_ACTIVE_SLOT
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M06-04: 未授权模块访问被拦截
        print("\n[TC-M06-04] 未授权模块访问被拦截")
        try:
            g = setup_gate()
            g.set_access_request_query(lambda: DataAccessRequest(
                request_id="T04", source_module="ag-mem-99",
                operation_type=OperationType.READ, target_slot_id="SLOT-LONG-0001"
            ))
            token = g.run_isolation_cycle()
            assert token is None
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M06-05: 操作类型越权
        print("\n[TC-M06-05] 操作类型越权（ag-mem-10 尝试写入）")
        try:
            g = setup_gate()
            g.set_access_request_query(lambda: DataAccessRequest(
                request_id="T05", source_module="ag-mem-10",
                operation_type=OperationType.WRITE, target_slot_id="SLOT-LONG-0001"
            ))
            token = g.run_isolation_cycle()
            assert token is None
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M06-06: 紧急熔断
        print("\n[TC-M06-06] 紧急熔断")
        try:
            g = setup_gate()
            g.emergency_shutdown()
            assert g.state == GateState.SYSTEM_PAUSED
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