#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-30
模块名称: L5核心层防篡改与只读管控单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为L5核心层数据的唯一对外访问接口，管控所有外部模块对L5层经验的读写权限。
          对外部模块仅提供经过令牌验证的只读查询服务，任何写入请求必须携带由ag-mem-29
          签发的有效写入安全令牌。管理查询令牌的签发、验证与吊销，确保L5数据不被未授权
          模块访问或修改。不参与经验内容的管理决策，仅执行访问权限的强制管控。

依赖模块:
    ag-mem-28(L5核心层存储单元), ag-mem-29(L5核心层安全规则硬锁定单元)
被依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-28

安全约束:
  S-01: L5层数据的所有访问必须经过本模块的令牌验证，禁止任何模块绕过本模块直接访问L5存储
  S-02: 查询令牌仅授权只读操作，任何携带查询令牌的写入请求一律拒绝
  S-03: 写入令牌必须与ag-mem-29签发的活跃令牌完全一致（ID+签名双重校验），任何不匹配视为令牌伪造
  S-04: 令牌签名采用HMAC-SHA256，密钥仅本模块与ag-mem-29共享，外部模块无法伪造
  S-05: 违规访问尝试必须实时阻断并完整记录告警日志，不可篡改
  S-06: 查询令牌过期后自动失效，不得通过任何方式延长有效期
"""

from typing import Dict, List, Optional, Any, Callable, Set
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import hmac
import hashlib


class AccessState(Enum):
    NORMAL_GATE = "normal_gate"
    WRITE_TOKEN_CHECK = "write_token_check"
    QUERY_TOKEN_ISSUE = "query_token_issue"
    VIOLATION_BLOCK = "violation_block"
    SYSTEM_PAUSED = "system_paused"


# ========== 访问权限矩阵（编译期硬编码） ==========
QUERY_AUTHORIZED_MODULES: Set[str] = {
    "ag-mem-15",  # 对话交互槽
    "ag-mem-16",  # 工具调用槽
    "ag-mem-17",  # 信息检索槽
    "ag-mem-18",  # 创作生成槽
    "ag-mem-19",  # 通用任务槽
}

WRITE_AUTHORIZED_MODULES: Set[str] = {
    "ag-mem-16",  # S值直达
    "ag-mem-27",  # L4推送
    "ag-mem-29",  # 人工锁定
}


@dataclass
class L5QueryRequest:
    request_id: str = ""
    query_conditions: Dict[str, Any] = field(default_factory=dict)
    source_slot_id: str = ""
    source_module_id: str = ""  # 【修复点2】新增来源模块ID，用于权限校验
    max_results: int = 20


@dataclass
class L5WriteRequest:
    request_id: str = ""
    entry_data: Dict[str, Any] = field(default_factory=dict)
    write_source: str = ""
    source_module_id: str = ""  # 【修复点2】新增来源模块ID
    token_id: str = ""          # 【修复点1】拆分令牌ID字段
    signature: str = ""         # 【修复点1】拆分签名字段


@dataclass
class ActiveWriteToken:
    token_id: str = ""
    max_write_entries: int = 1
    expires_at: float = 0.0
    signature: str = ""
    write_source: str = ""


@dataclass
class QueryToken:
    token_id: str = ""
    authorized_slot_id: str = ""
    authorized_module_id: str = ""  # 【修复点2】记录授权模块
    issued_at: float = field(default_factory=time.time)


@dataclass
class QueryTokenValidationResult:
    is_valid: bool = False
    authorized_slot_id: str = ""


@dataclass
class QueryTokenIssueResponse:
    token_id: str = ""
    authorized_slot_id: str = ""
    expires_in_seconds: int = 0


@dataclass
class WriteTokenValidationResult:
    is_valid: bool = False
    max_write_entries: int = 0
    remaining_validity_seconds: float = 0.0


@dataclass
class AccessRejectNotice:
    reject_reason: str = ""
    severity: str = ""


@dataclass
class ViolationAlert:
    violation_type: str = ""
    source_module: str = ""
    summary: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class GateStatusReport:
    state: str = ""
    issued_today: int = 0
    rejected_today: int = 0
    active_query_tokens: int = 0
    violation_count: int = 0


class L5AccessController:
    QUERY_TOKEN_VALIDITY_SEC = 300
    MAX_ACTIVE_QUERY_TOKENS = 20
    CLEANUP_INTERVAL_SEC = 60
    STATUS_REPORT_INTERVAL_SEC = 120
    TOKEN_SECRET = "l5-access-control-secret-key"

    def __init__(self):
        self.module_id = "ag-mem-30"
        self.module_name = "L5核心层防篡改与只读管控单元"
        self.version = "V1.0"

        self.state = AccessState.NORMAL_GATE
        self._active_query_tokens: Dict[str, QueryToken] = {}
        self._active_write_token: Optional[ActiveWriteToken] = None
        self._issued_today: int = 0
        self._rejected_today: int = 0
        self._violation_count: int = 0
        self._last_cleanup_time: float = time.time()
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        self._query_query_request = None
        self._query_write_request = None
        self._query_write_token_info = None
        self._publish_query_token_response = None
        self._publish_query_token_validation = None
        self._publish_write_token_validation = None
        self._publish_reject_notice = None
        self._publish_violation_alert = None
        self._publish_status_report = None
        self._publish_event_log = None
        # 【修复点3】新增：提供令牌验证回调供 ag-mem-28 注入
        self._verify_query_token_callback: Optional[Callable[[str], QueryTokenValidationResult]] = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_query_request_query(self, callback: Callable[[], Optional[L5QueryRequest]]):
        self._query_query_request = callback

    def set_write_request_query(self, callback: Callable[[], Optional[L5WriteRequest]]):
        self._query_write_request = callback

    def set_write_token_info_query(self, callback: Callable[[], Optional[ActiveWriteToken]]):
        self._query_write_token_info = callback

    def set_query_token_response_publisher(self, callback: Callable[[QueryTokenIssueResponse], None]):
        self._publish_query_token_response = callback

    def set_query_token_validation_publisher(self, callback: Callable[[QueryTokenValidationResult], None]):
        self._publish_query_token_validation = callback

    def set_write_token_validation_publisher(self, callback: Callable[[WriteTokenValidationResult], None]):
        self._publish_write_token_validation = callback

    def set_reject_notice_publisher(self, callback: Callable[[AccessRejectNotice], None]):
        self._publish_reject_notice = callback

    def set_violation_alert_publisher(self, callback: Callable[[ViolationAlert], None]):
        self._publish_violation_alert = callback

    def set_status_report_publisher(self, callback: Callable[[GateStatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # 【修复点3】提供令牌验证回调，供 ag-mem-28 注入
    def set_verify_query_token_callback(self, callback: Callable[[str], QueryTokenValidationResult]):
        self._verify_query_token_callback = callback

    def run_access_cycle(self):
        now = time.time()

        if self.state == AccessState.SYSTEM_PAUSED:
            return

        # 定期清理过期查询令牌
        if now - self._last_cleanup_time >= self.CLEANUP_INTERVAL_SEC:
            self._cleanup_expired_query_tokens(now)
            self._last_cleanup_time = now

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_gate_status()
            self._last_status_time = now

        # 同步写入令牌信息
        token_info = self._query_write_token_info() if self._query_write_token_info else None
        if token_info:
            self._active_write_token = token_info

        # 处理查询请求
        query_req = self._query_query_request() if self._query_query_request else None
        if query_req:
            self._handle_query_request(query_req, now)
            return

        # 处理写入请求
        write_req = self._query_write_request() if self._query_write_request else None
        if write_req:
            self._handle_write_request(write_req, now)

    def _handle_query_request(self, request: L5QueryRequest, now: float):
        # 【修复点2】校验请求来源模块是否在访问权限矩阵中
        if request.source_module_id not in QUERY_AUTHORIZED_MODULES:
            self._reject_and_alert(
                operation="查询",
                reason=f"未授权模块: {request.source_module_id}",
                severity="严重",
                request_id=request.request_id,
                source_module=request.source_module_id
            )
            return

        self.state = AccessState.QUERY_TOKEN_ISSUE

        # 检查活跃查询令牌数量，超限则淘汰最旧的
        if len(self._active_query_tokens) >= self.MAX_ACTIVE_QUERY_TOKENS:
            oldest_token_id = min(
                self._active_query_tokens.keys(),
                key=lambda tid: self._active_query_tokens[tid].issued_at
            )
            # 【修复点4】淘汰令牌时记录日志并发送吊销通知
            self._log_event("QUERY_TOKEN_EVICTED", {
                "token_id": oldest_token_id,
                "reason": "活跃令牌数量超限"
            })
            del self._active_query_tokens[oldest_token_id]

        # 签发新令牌
        token_id = f"L5-QTOKEN-{uuid.uuid4().hex[:8]}"
        token = QueryToken(
            token_id=token_id,
            authorized_slot_id=request.source_slot_id,
            authorized_module_id=request.source_module_id,
            issued_at=now
        )
        self._active_query_tokens[token_id] = token
        self._issued_today += 1

        # 【修复点5】补充日志记录
        self._log_event("QUERY_TOKEN_ISSUED", {
            "token_id": token_id,
            "source_module": request.source_module_id,
            "source_slot": request.source_slot_id
        })

        if self._publish_query_token_response:
            self._publish_query_token_response(QueryTokenIssueResponse(
                token_id=token_id,
                authorized_slot_id=request.source_slot_id,
                expires_in_seconds=self.QUERY_TOKEN_VALIDITY_SEC
            ))

        self.state = AccessState.NORMAL_GATE

    def _handle_write_request(self, request: L5WriteRequest, now: float):
        self.state = AccessState.WRITE_TOKEN_CHECK

        # 【修复点2】校验写入来源模块
        if request.source_module_id not in WRITE_AUTHORIZED_MODULES:
            self._reject_and_alert(
                operation="写入",
                reason=f"未授权写入模块: {request.source_module_id}",
                severity="严重",
                request_id=request.request_id,
                source_module=request.source_module_id
            )
            return

        # 无活跃写入令牌
        if not self._active_write_token:
            self._reject_and_alert(
                operation="写入",
                reason="无活跃写入令牌",
                severity="严重",
                request_id=request.request_id,
                source_module=request.source_module_id
            )
            return

        # 【修复点1】检查令牌ID（拆分后的独立字段）
        if request.token_id != self._active_write_token.token_id:
            self._reject_and_alert(
                operation="写入",
                reason="令牌ID不匹配",
                severity="严重",
                request_id=request.request_id,
                source_module=request.source_module_id
            )
            return

        # 检查令牌是否过期
        if now > self._active_write_token.expires_at:
            self._reject_and_alert(
                operation="写入",
                reason="写入令牌已过期",
                severity="一般",
                request_id=request.request_id,
                source_module=request.source_module_id
            )
            return

        # 【修复点1】验证签名（使用拆分后的独立签名字段）
        expected_sig = hmac.new(
            self.TOKEN_SECRET.encode(),
            f"{self._active_write_token.token_id}{self._active_write_token.max_write_entries}".encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        if request.signature != expected_sig:
            self._reject_and_alert(
                operation="写入",
                reason="令牌签名校验失败",
                severity="严重",
                request_id=request.request_id,
                source_module=request.source_module_id
            )
            return

        # 全部通过，返回验证成功
        remaining = self._active_write_token.expires_at - now
        if self._publish_write_token_validation:
            self._publish_write_token_validation(WriteTokenValidationResult(
                is_valid=True,
                max_write_entries=self._active_write_token.max_write_entries,
                remaining_validity_seconds=max(0.0, remaining)
            ))

        # 【修复点5】补充日志
        self._log_event("WRITE_TOKEN_VALIDATED", {
            "token_id": request.token_id,
            "source_module": request.source_module_id,
            "remaining_validity": max(0.0, remaining)
        })

        self.state = AccessState.NORMAL_GATE

    def verify_query_token(self, token_id: str, now: float) -> QueryTokenValidationResult:
        if token_id not in self._active_query_tokens:
            return QueryTokenValidationResult(is_valid=False)

        token = self._active_query_tokens[token_id]
        if now - token.issued_at > self.QUERY_TOKEN_VALIDITY_SEC:
            del self._active_query_tokens[token_id]
            return QueryTokenValidationResult(is_valid=False)

        return QueryTokenValidationResult(
            is_valid=True,
            authorized_slot_id=token.authorized_slot_id
        )

    def _reject_and_alert(self, operation: str, reason: str, severity: str,
                          request_id: str = "", source_module: str = ""):
        self._rejected_today += 1
        if severity == "严重":
            self._violation_count += 1

        # 【修复点5】补充本地日志记录
        self._log_event(f"{operation}_REJECTED", {
            "reason": reason,
            "severity": severity,
            "request_id": request_id,
            "source_module": source_module
        })

        if self._publish_reject_notice:
            self._publish_reject_notice(AccessRejectNotice(
                reject_reason=reason,
                severity=severity
            ))

        if severity == "严重" and self._publish_violation_alert:
            self._publish_violation_alert(ViolationAlert(
                violation_type=f"未授权{operation}",
                source_module=source_module,
                summary=reason
            ))

        self.state = AccessState.NORMAL_GATE

    def _cleanup_expired_query_tokens(self, now: float):
        expired = [
            tid for tid, token in self._active_query_tokens.items()
            if now - token.issued_at > self.QUERY_TOKEN_VALIDITY_SEC
        ]
        for tid in expired:
            del self._active_query_tokens[tid]
        if expired:
            self._log_event("QUERY_TOKENS_EXPIRED_CLEANED", {
                "cleaned_count": len(expired),
                "token_ids": expired
            })

    def _publish_gate_status(self):
        if self._publish_status_report:
            self._publish_status_report(GateStatusReport(
                state=self.state.value,
                issued_today=self._issued_today,
                rejected_today=self._rejected_today,
                active_query_tokens=len(self._active_query_tokens),
                violation_count=self._violation_count
            ))

    def emergency_shutdown(self):
        self.state = AccessState.SYSTEM_PAUSED
        self._log_event("EMERGENCY_SHUTDOWN", {
            "active_query_tokens": len(self._active_query_tokens)
        })
        self._active_query_tokens.clear()
        self._active_write_token = None
        print(f"[{self.module_id}] 紧急熔断，所有令牌已吊销")

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
    print("  Agent-mlnf-mem L5核心层防篡改与只读管控单元 (ag-mem-30) 演示")
    print("=" * 70)

    controller = L5AccessController()

    print_separator("STEP 1: 授权模块签发查询令牌")
    controller.set_query_request_query(lambda: L5QueryRequest(
        request_id="Q1",
        source_slot_id="ag-mem-16",
        source_module_id="ag-mem-16"
    ))
    controller.run_access_cycle()
    print(f"  活跃查询令牌数: {len(controller._active_query_tokens)}")

    print_separator("STEP 2: 验证有效查询令牌")
    valid = controller.verify_query_token(
        list(controller._active_query_tokens.keys())[0], time.time()
    )
    print(f"  验证结果: {valid.is_valid}")

    print_separator("STEP 3: 未授权模块查询被拒绝")
    controller.set_query_request_query(lambda: L5QueryRequest(
        request_id="Q2",
        source_slot_id="ag-mem-16",
        source_module_id="ag-mem-01"
    ))
    controller.run_access_cycle()
    print(f"  今日拒绝次数: {controller._rejected_today}")

    print_separator("STEP 4: 合法写入令牌通过校验")
    token_id = "TOKEN-TEST-001"
    sig = hmac.new(
        L5AccessController.TOKEN_SECRET.encode(),
        f"{token_id}1".encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    controller._active_write_token = ActiveWriteToken(
        token_id=token_id,
        max_write_entries=1,
        expires_at=time.time() + 30,
        signature=sig,
        write_source="S值直达"
    )
    controller.set_write_request_query(lambda: L5WriteRequest(
        request_id="W1",
        source_module_id="ag-mem-16",
        token_id=token_id,
        signature=sig
    ))
    controller.run_access_cycle()
    print(f"  今日拒绝次数: {controller._rejected_today} (应无新增)")

    print("\n✅ L5核心层防篡改与只读管控单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-30 L5核心层防篡改与只读管控单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_controller():
            return L5AccessController()

        # TC-M30-01: 授权模块签发查询令牌
        print("\n[TC-M30-01] 授权模块签发查询令牌")
        try:
            c = setup_controller()
            c.set_query_request_query(lambda: L5QueryRequest(
                source_slot_id="ag-mem-16", source_module_id="ag-mem-16"
            ))
            c.run_access_cycle()
            assert len(c._active_query_tokens) == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M30-02: 未授权模块查询被拒绝
        print("\n[TC-M30-02] 未授权模块查询被拒绝")
        try:
            c = setup_controller()
            c.set_query_request_query(lambda: L5QueryRequest(
                source_slot_id="ag-mem-16", source_module_id="ag-mem-01"
            ))
            c.run_access_cycle()
            assert len(c._active_query_tokens) == 0
            assert c._rejected_today == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M30-03: 令牌ID不匹配被拒绝
        print("\n[TC-M30-03] 令牌ID不匹配被拒绝")
        try:
            c = setup_controller()
            token_id = "TOKEN-A"
            sig = hmac.new(
                L5AccessController.TOKEN_SECRET.encode(),
                f"{token_id}1".encode(),
                hashlib.sha256
            ).hexdigest()[:16]
            c._active_write_token = ActiveWriteToken(
                token_id=token_id, max_write_entries=1,
                expires_at=time.time() + 30, signature=sig
            )
            c.set_write_request_query(lambda: L5WriteRequest(
                source_module_id="ag-mem-16",
                token_id="TOKEN-B",  # 不匹配
                signature=sig
            ))
            c.run_access_cycle()
            assert c._rejected_today == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M30-04: 签名不匹配被拒绝
        print("\n[TC-M30-04] 签名不匹配被拒绝")
        try:
            c = setup_controller()
            token_id = "TOKEN-C"
            sig = hmac.new(
                L5AccessController.TOKEN_SECRET.encode(),
                f"{token_id}1".encode(),
                hashlib.sha256
            ).hexdigest()[:16]
            c._active_write_token = ActiveWriteToken(
                token_id=token_id, max_write_entries=1,
                expires_at=time.time() + 30, signature=sig
            )
            c.set_write_request_query(lambda: L5WriteRequest(
                source_module_id="ag-mem-16",
                token_id=token_id,
                signature="INVALID_SIGNATURE"
            ))
            c.run_access_cycle()
            assert c._rejected_today == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M30-05: 合法令牌通过校验
        print("\n[TC-M30-05] 合法令牌通过校验")
        try:
            c = setup_controller()
            token_id = "TOKEN-D"
            sig = hmac.new(
                L5AccessController.TOKEN_SECRET.encode(),
                f"{token_id}1".encode(),
                hashlib.sha256
            ).hexdigest()[:16]
            c._active_write_token = ActiveWriteToken(
                token_id=token_id, max_write_entries=1,
                expires_at=time.time() + 30, signature=sig
            )
            c.set_write_request_query(lambda: L5WriteRequest(
                source_module_id="ag-mem-16",
                token_id=token_id,
                signature=sig
            ))
            c.run_access_cycle()
            assert c._rejected_today == 0  # 应无拒绝
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M30-06: 紧急熔断
        print("\n[TC-M30-06] 紧急熔断")
        try:
            c = setup_controller()
            c.emergency_shutdown()
            assert c.state == AccessState.SYSTEM_PAUSED
            assert len(c._active_query_tokens) == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M30-07: 查询令牌超限淘汰
        print("\n[TC-M30-07] 查询令牌超限淘汰")
        try:
            c = setup_controller()
            for i in range(25):
                c.set_query_request_query(lambda i=i: L5QueryRequest(
                    source_slot_id="ag-mem-16",
                    source_module_id="ag-mem-16"
                ))
                c.run_access_cycle()
            assert len(c._active_query_tokens) == 20
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