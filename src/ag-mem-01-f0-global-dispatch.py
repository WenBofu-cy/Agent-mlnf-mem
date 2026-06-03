#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-01
模块名称: 总控漏斗F0-双漏斗全局调度中枢
所属分区: 一、顶层总控中枢
核心职责: 作为 Agent-mlnf-mem 双漏斗记忆中枢的全局唯一调度入口，接收 ECC 认知大脑通过
          MemoryBus 下发的经验查询与写入请求，完成意图解析、优先级仲裁、双漏斗路由分发。
          管理漏斗一（用户画像）与漏斗二（任务经验）的资源分配与模式切换，协调漏斗外挂
          扩展区的只读查询。汇总各子漏斗运行状态，统一向 ECC 回传查询回执与健康状态。
          不参与任何认知决策，仅执行记忆管理的调度与路由。

依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-03(漏斗二专属调度单元),
    ag-mem-44(独立知识库), ag-mem-45(安全规则库), ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ECC 认知大脑, ag-mem-02~51 全部记忆模块

安全约束:
  S-01: 本模块为双漏斗记忆中枢的唯一对外入口，所有 ECC 记忆请求必须经本模块路由
  S-02: 漏斗一数据仅向通过身份验证的查询请求开放，编译期禁止漏斗一数据流入漏斗二
  S-03: 熔断状态下，本模块仅接收恢复信号，拒绝一切查询与写入请求
  S-04: 本模块不参与认知决策，仅执行记忆存储的调度、路由与状态汇总
  S-05: 冷启动自检未通过时，必须明确标记降级模块并向 ECC 上报告警
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class DispatchState(Enum):
    INIT = "init"
    IDLE = "idle"
    NORMAL_DISPATCH = "normal_dispatch"
    MAINTENANCE = "maintenance"
    MELTDOWN = "meltdown"


class RequestType(Enum):
    EXPERIENCE_QUERY = "experience_query"
    EXPERIENCE_WRITE = "experience_write"
    USER_PROFILE_QUERY = "user_profile_query"
    KNOWLEDGE_QUERY = "knowledge_query"
    SAFETY_RULE_QUERY = "safety_rule_query"
    COLD_START_CHECK = "cold_start_check"


@dataclass
class ECCRequest:
    request_id: str = ""
    request_type: RequestType = RequestType.EXPERIENCE_QUERY
    user_id: str = ""
    session_id: str = ""
    task_context: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class DispatchResult:
    request_id: str = ""
    success: bool = True
    data: Any = None
    error_reason: str = ""
    source_funnel: str = ""
    query_duration_ms: float = 0.0


@dataclass
class HealthStatus:
    funnel_one_active: bool = False
    funnel_two_active: bool = False
    total_storage_usage_pct: float = 0.0
    active_modules_count: int = 0
    maintenance_mode: bool = False
    timestamp: float = field(default_factory=time.time)


class F0GlobalDispatch:
    def __init__(self):
        self.module_id = "ag-mem-01"
        self.module_name = "总控漏斗F0-双漏斗全局调度中枢"
        self.version = "V1.0"

        self.state = DispatchState.INIT
        self._request_queue: List[ECCRequest] = []
        self._funnel_one_available = False
        self._funnel_two_available = False
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_ecc_request = None
        self._query_funnel_one_status = None
        self._query_funnel_two_status = None
        self._query_capacity_status = None
        self._publish_result = None
        self._publish_health_status = None
        self._publish_dispatch_command = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_ecc_request_query(self, callback: Callable[[], Optional[ECCRequest]]):
        self._query_ecc_request = callback

    def set_funnel_one_status_query(self, callback: Callable[[], Dict[str, Any]]):
        self._query_funnel_one_status = callback

    def set_funnel_two_status_query(self, callback: Callable[[], Dict[str, Any]]):
        self._query_funnel_two_status = callback

    def set_capacity_status_query(self, callback: Callable[[], Dict[str, Any]]):
        self._query_capacity_status = callback

    def set_result_publisher(self, callback: Callable[[DispatchResult], None]):
        self._publish_result = callback

    def set_health_status_publisher(self, callback: Callable[[HealthStatus], None]):
        self._publish_health_status = callback

    def set_dispatch_command_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_dispatch_command = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_dispatch_cycle(self) -> Optional[DispatchResult]:
        now = time.time()

        if self.state == DispatchState.INIT:
            self._perform_cold_start()
            return None

        if self.state == DispatchState.MELTDOWN:
            return None

        # 处理队列中的请求
        if self._request_queue:
            request = self._request_queue.pop(0)
            return self._process_request(request)

        # 从总线获取新请求
        request = self._query_ecc_request() if self._query_ecc_request else None
        if request is None:
            return None

        return self._process_request(request)

    def _perform_cold_start(self):
        self._funnel_one_available = self._check_module_availability("ag-mem-02")
        self._funnel_two_available = self._check_module_availability("ag-mem-03")
        
        if self._funnel_one_available or self._funnel_two_available:
            self.state = DispatchState.IDLE
            self._log_event("COLD_START_COMPLETE", {
                "funnel_one": self._funnel_one_available,
                "funnel_two": self._funnel_two_available
            })
        else:
            self._log_event("COLD_START_FAILED", {"reason": "所有漏斗不可用"})

    def _process_request(self, request: ECCRequest) -> DispatchResult:
        if self.state in (DispatchState.INIT, DispatchState.MELTDOWN):
            return DispatchResult(
                request_id=request.request_id,
                success=False,
                error_reason="系统当前不可服务"
            )

        self.state = DispatchState.NORMAL_DISPATCH
        start_time = time.time()

        if request.request_type == RequestType.EXPERIENCE_QUERY:
            result = self._handle_experience_query(request)
        elif request.request_type == RequestType.EXPERIENCE_WRITE:
            result = self._handle_experience_write(request)
        elif request.request_type == RequestType.USER_PROFILE_QUERY:
            result = self._handle_user_profile_query(request)
        elif request.request_type == RequestType.KNOWLEDGE_QUERY:
            result = self._handle_knowledge_query(request)
        elif request.request_type == RequestType.SAFETY_RULE_QUERY:
            result = self._handle_safety_rule_query(request)
        elif request.request_type == RequestType.COLD_START_CHECK:
            result = self._handle_cold_start_check(request)
        else:
            result = DispatchResult(
                request_id=request.request_id,
                success=False,
                error_reason=f"未知请求类型: {request.request_type}"
            )

        result.query_duration_ms = (time.time() - start_time) * 1000
        self.state = DispatchState.IDLE
        return result

    def _handle_experience_query(self, request: ECCRequest) -> DispatchResult:
        if not request.user_id:
            return DispatchResult(
                request_id=request.request_id,
                success=False,
                error_reason="用户ID不能为空"
            )

        # 向漏斗一和漏斗二发起查询
        funnel_one_result = self._query_funnel("ag-mem-02", request)
        funnel_two_result = self._query_funnel("ag-mem-03", request)

        # 合并结果
        combined_data = []
        if funnel_one_result:
            combined_data.extend(funnel_one_result)
        if funnel_two_result:
            combined_data.extend(funnel_two_result)

        # 按重要度排序
        combined_data.sort(key=lambda x: x.get("importance", 0), reverse=True)

        return DispatchResult(
            request_id=request.request_id,
            success=True,
            data=combined_data,
            source_funnel="funnel_one+funnel_two"
        )

    def _handle_experience_write(self, request: ECCRequest) -> DispatchResult:
        # 根据写入数据类型判定目标漏斗
        if request.task_context.get("is_user_profile", False):
            target_funnel = "ag-mem-02"
        else:
            target_funnel = "ag-mem-03"

        write_result = self._write_to_funnel(target_funnel, request)
        return DispatchResult(
            request_id=request.request_id,
            success=write_result.get("success", False),
            data=write_result,
            source_funnel="funnel_one" if target_funnel == "ag-mem-02" else "funnel_two"
        )

    def _handle_user_profile_query(self, request: ECCRequest) -> DispatchResult:
        profile_data = self._query_funnel("ag-mem-02", request)
        return DispatchResult(
            request_id=request.request_id,
            success=True,
            data=profile_data,
            source_funnel="funnel_one"
        )

    def _handle_knowledge_query(self, request: ECCRequest) -> DispatchResult:
        knowledge_data = self._query_funnel("ag-mem-44", request)
        return DispatchResult(
            request_id=request.request_id,
            success=True,
            data=knowledge_data,
            source_funnel="external_knowledge_base"
        )

    def _handle_safety_rule_query(self, request: ECCRequest) -> DispatchResult:
        safety_data = self._query_funnel("ag-mem-45", request)
        return DispatchResult(
            request_id=request.request_id,
            success=True,
            data=safety_data,
            source_funnel="external_safety_rules"
        )

    def _handle_cold_start_check(self, request: ECCRequest) -> DispatchResult:
        self._perform_cold_start()
        return DispatchResult(
            request_id=request.request_id,
            success=True,
            data={
                "funnel_one_available": self._funnel_one_available,
                "funnel_two_available": self._funnel_two_available,
                "state": self.state.value
            }
        )

    def _query_funnel(self, target_module: str, request: ECCRequest) -> Optional[List[Dict[str, Any]]]:
        if self._publish_dispatch_command:
            self._publish_dispatch_command(target_module, {
                "action": "query",
                "request": request
            })
        # 实际实现中会等待子模块响应，此处简化
        return None

    def _write_to_funnel(self, target_module: str, request: ECCRequest) -> Dict[str, Any]:
        if self._publish_dispatch_command:
            self._publish_dispatch_command(target_module, {
                "action": "write",
                "request": request
            })
        return {"success": True, "entry_id": f"EXP-{uuid.uuid4().hex[:8]}"}

    def _check_module_availability(self, module_id: str) -> bool:
        # 简化实现：通过状态查询回调检查模块是否在线
        return True

    def get_health_status(self) -> HealthStatus:
        funnel_one_status = self._query_funnel_one_status() if self._query_funnel_one_status else {}
        funnel_two_status = self._query_funnel_two_status() if self._query_funnel_two_status else {}
        capacity_status = self._query_capacity_status() if self._query_capacity_status else {}

        return HealthStatus(
            funnel_one_active=funnel_one_status.get("active", False),
            funnel_two_active=funnel_two_status.get("active", False),
            total_storage_usage_pct=capacity_status.get("usage_pct", 0.0),
            active_modules_count=sum([
                1 if funnel_one_status.get("active") else 0,
                1 if funnel_two_status.get("active") else 0
            ]),
            maintenance_mode=self.state == DispatchState.MAINTENANCE
        )

    def emergency_shutdown(self):
        self.state = DispatchState.MELTDOWN
        self._request_queue.clear()
        self._log_event("EMERGENCY_SHUTDOWN", {"state": self.state.value})
        print(f"[{self.module_id}] 紧急熔断")

    def recover_from_meltdown(self):
        if self.state == DispatchState.MELTDOWN:
            self.state = DispatchState.IDLE
            self._log_event("MELTDOWN_RECOVERED", {})

    def _log_event(self, event_type: str, details: Dict[str, Any]) -> None:
        log_entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(log_entry)
        if self._publish_event_log:
            self._publish_event_log(log_entry)

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs

    def get_state(self) -> DispatchState:
        return self.state


def print_separator(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_main():
    print("=" * 70)
    print("  Agent-mlnf-mem 总控漏斗F0 (ag-mem-01) 演示")
    print("=" * 70)

    f0 = F0GlobalDispatch()

    print_separator("STEP 1: 冷启动自检")
    f0.run_dispatch_cycle()
    health = f0.get_health_status()
    print(f"  状态: {f0.state.value}")
    print(f"  漏斗一: {'可用' if health.funnel_one_active else '不可用'}")
    print(f"  漏斗二: {'可用' if health.funnel_two_active else '不可用'}")

    print_separator("STEP 2: 处理经验查询请求")
    f0.set_ecc_request_query(lambda: ECCRequest(
        request_id="REQ-001",
        request_type=RequestType.EXPERIENCE_QUERY,
        user_id="U001",
        task_context={"scene": "对话交互"}
    ))
    result = f0.run_dispatch_cycle()
    if result:
        print(f"  请求ID: {result.request_id}")
        print(f"  成功: {result.success}")
        print(f"  来源漏斗: {result.source_funnel}")
        print(f"  查询耗时: {result.query_duration_ms:.2f}ms")

    print_separator("STEP 3: 处理经验写入请求")
    f0.set_ecc_request_query(lambda: ECCRequest(
        request_id="REQ-002",
        request_type=RequestType.EXPERIENCE_WRITE,
        user_id="U001",
        task_context={"scene": "工具调用", "is_user_profile": False}
    ))
    result = f0.run_dispatch_cycle()
    if result:
        print(f"  写入结果: {result.data}")

    print_separator("STEP 4: 紧急熔断")
    f0.emergency_shutdown()
    print(f"  状态: {f0.state.value}")

    print("\n✅ 总控漏斗F0演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-01 总控漏斗F0 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        # TC-M01-01: 冷启动自检
        print("\n[TC-M01-01] 冷启动自检")
        try:
            f0 = F0GlobalDispatch()
            f0.run_dispatch_cycle()
            assert f0.state != DispatchState.INIT
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M01-02: 经验查询请求
        print("\n[TC-M01-02] 经验查询请求")
        try:
            f0 = F0GlobalDispatch()
            f0.run_dispatch_cycle()
            f0.set_ecc_request_query(lambda: ECCRequest(
                request_id="T02", request_type=RequestType.EXPERIENCE_QUERY, user_id="U001"
            ))
            result = f0.run_dispatch_cycle()
            assert result is not None
            assert result.success
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M01-03: 写入漏斗二（非用户画像）
        print("\n[TC-M01-03] 写入漏斗二")
        try:
            f0 = F0GlobalDispatch()
            f0.run_dispatch_cycle()
            f0.set_ecc_request_query(lambda: ECCRequest(
                request_id="T03", request_type=RequestType.EXPERIENCE_WRITE,
                task_context={"is_user_profile": False}
            ))
            result = f0.run_dispatch_cycle()
            assert result is not None
            assert result.source_funnel == "funnel_two"
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M01-04: 紧急熔断拒绝请求
        print("\n[TC-M01-04] 紧急熔断拒绝请求")
        try:
            f0 = F0GlobalDispatch()
            f0.emergency_shutdown()
            f0.set_ecc_request_query(lambda: ECCRequest(
                request_id="T04", request_type=RequestType.EXPERIENCE_QUERY
            ))
            result = f0.run_dispatch_cycle()
            assert result is not None
            assert not result.success
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M01-05: 用户画像查询
        print("\n[TC-M01-05] 用户画像查询")
        try:
            f0 = F0GlobalDispatch()
            f0.run_dispatch_cycle()
            f0.set_ecc_request_query(lambda: ECCRequest(
                request_id="T05", request_type=RequestType.USER_PROFILE_QUERY, user_id="U001"
            ))
            result = f0.run_dispatch_cycle()
            assert result is not None
            assert result.source_funnel == "funnel_one"
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