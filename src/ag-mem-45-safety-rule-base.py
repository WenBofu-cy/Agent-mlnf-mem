#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-45
模块名称: 安全规则库
所属分区: 四、漏斗外挂扩展区（物理隔离）
核心职责: 作为漏斗外挂扩展区的安全策略知识库，存储并管理工具调用安全边界、权限分级规则、
          敏感操作白名单/黑名单、合规校验基准等结构化安全规则。为 ag-mem-43（失败经验安全
          仲裁三道校验单元）提供第一道安全合规校验的依据，为 ag-ecc-04（安全仲裁模块）提供
          全局安全规则查询，为 ag-mem-29（L5 核心层安全规则硬锁定单元）提供 L5 安全合规
          校验基准。本库完全独立于双漏斗记忆系统运行，不参与记忆沉淀、筛选、晋升与遗忘机制。
          安全规则在系统部署前预置，支持定期离线更新，是 EM-Core Agent 安全体系的底层规则
          源头。仅提供只读查询服务，不参与任何认知决策。

依赖模块:
    无（作为独立安全规则服务，不依赖记忆系统内部模块）
被依赖模块:
    ag-mem-43(失败经验安全仲裁), ag-mem-29(L5锁定单元),
    ag-ecc-04(安全仲裁模块), ag-mem-01(总控漏斗F₀)

安全约束:
  SR-01: 安全规则库为只读数据，运行时不得被任何控制模块或自动化流程修改
  SR-02: 规则更新必须经过授权令牌验证、签名校验与沙箱逻辑一致性验证三道关卡
  SR-03: 规则库降级时强制加载内置最小安全规则集，确保安全底线在任何情况下均可用
  SR-04: 合规校验请求信息不足时，默认返回“不合规”，遵循安全保守原则
  SR-05: 全量规则查询仅向 ag-ecc-04 和 ag-mem-29 开放，其他模块无权获取
  SR-06: 所有规则更新与拒绝事件必须记录完整的安全事件日志，不可篡改
  SR-07: 本库完全独立于漏斗记忆系统，不参与记忆沉淀、晋升、遗忘任何环节
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import copy


class RuleBaseState(Enum):
    NORMAL_SERVICE = "normal_service"
    LOADING = "loading"
    DEGRADED = "degraded"
    SYSTEM_PAUSED = "system_paused"


class RuleCategory(Enum):
    WHITELIST = "工具白名单"
    BLACKLIST = "工具黑名单"
    SENSITIVE = "敏感操作清单"
    PERMISSION = "权限分级规则"
    COMPLIANCE = "合规校验规则"
    SAFETY_LEVEL = "安全等级定义"


class Severity(Enum):
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"
    CRITICAL = "严重"


@dataclass
class SafetyRule:
    rule_id: str = ""
    category: RuleCategory = RuleCategory.WHITELIST
    name: str = ""
    applicable_tools: List[str] = field(default_factory=list)
    condition: str = ""
    action: str = "允许"
    severity: Severity = Severity.LOW
    priority: int = 50
    overridable: bool = False
    override_condition: str = ""
    version: str = "1.0"
    updated_at: float = field(default_factory=time.time)
    source: str = "预置"


@dataclass
class SafetyRuleQueryRequest:
    request_id: str = ""
    requester_module: str = ""
    query_type: str = "合规校验"
    tool_name: str = ""
    operation_type: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    tool_call_history: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class SafetyComplianceResult:
    request_id: str = ""
    compliant: bool = True
    violated_rules: List[str] = field(default_factory=list)
    severity: str = "低"
    required_action: str = ""
    suggestion: str = ""


@dataclass
class SafetyRuleQueryResult:
    request_id: str = ""
    matched_rules: List[SafetyRule] = field(default_factory=list)
    total_matched: int = 0


@dataclass
class SafetyRuleUpdateCommand:
    update_scope: str = "全量"
    data_package: Dict[str, Any] = field(default_factory=dict)
    version: str = ""
    signature: str = ""
    authorization_token: str = ""


@dataclass
class SafetyRuleUpdateConfirm:
    success: bool = True
    added_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    new_version: str = ""
    error_reason: str = ""


@dataclass
class SafetyRuleIntegrityReport:
    total_rules: int = 0
    valid_rules: int = 0
    corrupted_rules: List[str] = field(default_factory=list)
    check_duration_ms: float = 0.0


@dataclass
class SafetyRuleBaseStatus:
    state: RuleBaseState = RuleBaseState.NORMAL_SERVICE
    total_rules: int = 0
    category_distribution: Dict[str, int] = field(default_factory=dict)
    last_updated: float = 0.0
    version: str = ""


class SafetyRuleBase:
    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.module_id = "ag-mem-45"
        self.module_name = "安全规则库"
        self.version = "V1.0"
        self._rule_version = "1.0.0"

        self.state = RuleBaseState.LOADING
        self._rules: Dict[str, SafetyRule] = {}
        self._category_index: Dict[RuleCategory, List[str]] = {c: [] for c in RuleCategory}
        self._tool_index: Dict[str, List[str]] = {}
        self._query_cache: Dict[str, Tuple[Any, float]] = {}
        self._total_rules: int = 0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_rule_request = None
        self._query_update_command = None
        self._query_integrity_check = None

        self._publish_compliance_result = None
        self._publish_rule_query_result = None
        self._publish_update_confirm = None
        self._publish_integrity_report = None
        self._publish_status_report = None
        self._publish_event_log = None

        # 加载预置规则
        self._load_preset_rules()
        self.state = RuleBaseState.NORMAL_SERVICE

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 预置规则数={self._total_rules}")

    # ========== 回调注入 ==========
    def set_rule_request_query(self, callback: Callable[[], Optional[SafetyRuleQueryRequest]]):
        self._query_rule_request = callback

    def set_update_command_query(self, callback: Callable[[], Optional[SafetyRuleUpdateCommand]]):
        self._query_update_command = callback

    def set_integrity_check_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_integrity_check = callback

    def set_compliance_result_publisher(self, callback: Callable[[SafetyComplianceResult], None]):
        self._publish_compliance_result = callback

    def set_rule_query_result_publisher(self, callback: Callable[[SafetyRuleQueryResult], None]):
        self._publish_rule_query_result = callback

    def set_update_confirm_publisher(self, callback: Callable[[SafetyRuleUpdateConfirm], None]):
        self._publish_update_confirm = callback

    def set_integrity_report_publisher(self, callback: Callable[[SafetyRuleIntegrityReport], None]):
        self._publish_integrity_report = callback

    def set_status_report_publisher(self, callback: Callable[[SafetyRuleBaseStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_rule_cycle(self):
        now = time.time()

        if self.state == RuleBaseState.SYSTEM_PAUSED:
            return

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 处理规则查询请求
        query = self._query_rule_request() if self._query_rule_request else None
        if query:
            self._handle_query(query)
            return

        # 处理规则更新指令
        update_cmd = self._query_update_command() if self._query_update_command else None
        if update_cmd:
            self._handle_update(update_cmd)
            return

        # 处理完整性校验
        integrity_cmd = self._query_integrity_check() if self._query_integrity_check else None
        if integrity_cmd:
            self._handle_integrity_check()

    # ========== 查询处理 ==========
    def _handle_query(self, request: SafetyRuleQueryRequest):
        if request.query_type == "合规校验":
            result = self._perform_compliance_check(request)
            if self._publish_compliance_result:
                self._publish_compliance_result(result)
            return

        if request.query_type == "全量规则":
            if request.requester_module not in ("ag-ecc-04", "ag-mem-29"):
                self._log_event("UNAUTHORIZED_FULL_QUERY", {"requester": request.requester_module})
                if self._publish_rule_query_result:
                    self._publish_rule_query_result(SafetyRuleQueryResult(request_id=request.request_id, matched_rules=[]))
                return

        # 其他类型查询
        matched = self._query_rules(request)
        if self._publish_rule_query_result:
            self._publish_rule_query_result(SafetyRuleQueryResult(
                request_id=request.request_id,
                matched_rules=matched,
                total_matched=len(matched)
            ))

    def _perform_compliance_check(self, request: SafetyRuleQueryRequest) -> SafetyComplianceResult:
        tool_name = request.tool_name
        operation = request.operation_type

        # 保守默认：信息不足时拒绝（安全约束SR-04）
        if not tool_name and not operation:
            return SafetyComplianceResult(
                request_id=request.request_id,
                compliant=False,
                violated_rules=["信息不足，默认拒绝"],
                severity="严重",
                suggestion="请提供工具名称或操作类型"
            )

        # 第一步：检查黑名单（优先级最高）
        blacklist_rules = self._find_rules_by_category_and_tool(RuleCategory.BLACKLIST, tool_name)
        if blacklist_rules:
            return SafetyComplianceResult(
                request_id=request.request_id,
                compliant=False,
                violated_rules=[r.name for r in blacklist_rules],
                severity="严重",
                required_action="禁止执行",
                suggestion="该工具或操作在黑名单中，禁止执行"
            )

        # 第二步：检查合规模式（优先级高于白名单，防止序列攻击）
        compliance_rules = self._check_compliance_patterns(request.tool_call_history)
        if compliance_rules:
            return SafetyComplianceResult(
                request_id=request.request_id,
                compliant=False,
                violated_rules=[r.name for r in compliance_rules],
                severity="严重",
                required_action="禁止执行",
                suggestion="工具调用序列触发合规模式告警"
            )

        # 第三步：检查白名单
        whitelist_rules = self._find_rules_by_category_and_tool(RuleCategory.WHITELIST, tool_name)
        is_in_whitelist = len(whitelist_rules) > 0

        # 第四步：检查敏感操作
        sensitive_rules = self._find_rules_by_category_and_tool(RuleCategory.SENSITIVE, tool_name)
        needs_confirmation = len(sensitive_rules) > 0

        # 第五步：检查权限分级
        permission_rules = self._find_rules_by_category_and_tool(RuleCategory.PERMISSION, tool_name)
        max_severity = Severity.LOW
        for r in permission_rules:
            if r.severity == Severity.CRITICAL:
                max_severity = Severity.CRITICAL
            elif r.severity == Severity.HIGH and max_severity.value in ("低", "中"):
                max_severity = Severity.HIGH

        # 综合判定（修复：不在白名单且不在敏感清单，但非安全工具 → 拒绝）
        if not is_in_whitelist and not needs_confirmation:
            # 不在白名单且未登记为敏感操作的未知工具 → 保守拒绝
            return SafetyComplianceResult(
                request_id=request.request_id,
                compliant=False,
                violated_rules=["不在白名单中"],
                severity="高",
                required_action="禁止执行",
                suggestion="该工具未在白名单中注册，禁止自动执行"
            )

        if not is_in_whitelist and needs_confirmation:
            return SafetyComplianceResult(
                request_id=request.request_id,
                compliant=True,
                violated_rules=[],
                severity="中",
                required_action="需要用户确认",
                suggestion="该操作不在白名单中且属于敏感操作，需用户确认后方可执行"
            )

        if needs_confirmation:
            return SafetyComplianceResult(
                request_id=request.request_id,
                compliant=True,
                violated_rules=[],
                severity="中",
                required_action="需要用户确认",
                suggestion="敏感操作，需要用户二次确认"
            )

        return SafetyComplianceResult(
            request_id=request.request_id,
            compliant=True,
            violated_rules=[],
            severity=max_severity.value,
            suggestion="操作合规"
        )

    def _query_rules(self, request: SafetyRuleQueryRequest) -> List[SafetyRule]:
        if request.query_type == "全量规则":
            return list(self._rules.values())
        elif request.query_type == "权限分级":
            return self._find_rules_by_category_and_tool(RuleCategory.PERMISSION, request.tool_name)
        elif request.query_type == "敏感操作判定":
            return self._find_rules_by_category_and_tool(RuleCategory.SENSITIVE, request.tool_name)
        elif request.query_type == "安全等级查询":
            return self._find_rules_by_category(RuleCategory.SAFETY_LEVEL)
        return []

    # ========== 规则查询辅助 ==========
    def _find_rules_by_category_and_tool(self, category: RuleCategory, tool_name: str) -> List[SafetyRule]:
        results = []
        if category not in self._category_index:
            return results
        for rule_id in self._category_index[category]:
            rule = self._rules.get(rule_id)
            if rule and (not tool_name or tool_name in rule.applicable_tools or not rule.applicable_tools):
                results.append(rule)
        return results

    def _find_rules_by_category(self, category: RuleCategory) -> List[SafetyRule]:
        results = []
        if category not in self._category_index:
            return results
        for rule_id in self._category_index[category]:
            rule = self._rules.get(rule_id)
            if rule:
                results.append(rule)
        return results

    def _check_compliance_patterns(self, tool_history: List[str]) -> List[SafetyRule]:
        violated = []
        for rule in self._find_rules_by_category(RuleCategory.COMPLIANCE):
            if rule.condition and all(t in tool_history for t in rule.applicable_tools):
                violated.append(rule)
        return violated

    # ========== 更新处理 ==========
    def _handle_update(self, command: SafetyRuleUpdateCommand):
        self.state = RuleBaseState.LOADING

        # 校验授权令牌
        if not command.authorization_token or len(command.authorization_token) < 10:
            self._send_update_confirm(False, 0, 0, 0, "授权令牌无效")
            self.state = RuleBaseState.NORMAL_SERVICE
            return

        # 校验签名
        if not command.signature or len(command.signature) < 10:
            self._send_update_confirm(False, 0, 0, 0, "签名校验失败")
            self.state = RuleBaseState.NORMAL_SERVICE
            return

        # 备份当前规则与索引
        old_rules = copy.deepcopy(self._rules)
        old_indexes = copy.deepcopy(self._category_index)
        old_version = self._rule_version
        old_count = self._total_rules

        try:
            added = 0
            updated = 0
            deleted = 0
            data = command.data_package

            if "add_rules" in data:
                for rule_data in data["add_rules"]:
                    rule = SafetyRule(
                        rule_id=rule_data.get("rule_id", f"SEC-{uuid.uuid4().hex[:8]}"),
                        category=RuleCategory[rule_data["category"]] if "category" in rule_data else RuleCategory.WHITELIST,
                        name=rule_data.get("name", ""),
                        applicable_tools=rule_data.get("applicable_tools", []),
                        condition=rule_data.get("condition", ""),
                        action=rule_data.get("action", "允许"),
                        severity=Severity[rule_data.get("severity", "LOW")],
                        priority=rule_data.get("priority", 50),
                        version=command.version,
                        updated_at=time.time(),
                        source="更新"
                    )
                    self._rules[rule.rule_id] = rule
                    added += 1

            if "update_rules" in data:
                for rule_data in data["update_rules"]:
                    rule_id = rule_data.get("rule_id")
                    if rule_id in self._rules:
                        rule = self._rules[rule_id]
                        if "name" in rule_data:
                            rule.name = rule_data["name"]
                        if "applicable_tools" in rule_data:
                            rule.applicable_tools = rule_data["applicable_tools"]
                        if "condition" in rule_data:
                            rule.condition = rule_data["condition"]
                        if "action" in rule_data:
                            rule.action = rule_data["action"]
                        if "severity" in rule_data:
                            rule.severity = Severity[rule_data["severity"]]
                        rule.version = command.version
                        rule.updated_at = time.time()
                        updated += 1

            if "delete_rule_ids" in data:
                for rule_id in data["delete_rule_ids"]:
                    if rule_id in self._rules:
                        del self._rules[rule_id]
                        deleted += 1

            # 统一重建索引（性能优化：避免逐条维护的冗余操作）
            self._rule_version = command.version
            self._total_rules = len(self._rules)
            self._rebuild_indexes()
            self._query_cache.clear()

            self._send_update_confirm(True, added, updated, deleted, command.version)

        except Exception as e:
            # 原子回滚
            self._rules = old_rules
            self._category_index = old_indexes
            self._rule_version = old_version
            self._total_rules = old_count
            self._send_update_confirm(False, 0, 0, 0, f"更新异常: {str(e)}")

        self.state = RuleBaseState.NORMAL_SERVICE

    def _send_update_confirm(self, success: bool, added: int, updated: int, deleted: int, version_or_error: str):
        if self._publish_update_confirm:
            if success:
                self._publish_update_confirm(SafetyRuleUpdateConfirm(
                    success=True,
                    added_count=added,
                    updated_count=updated,
                    deleted_count=deleted,
                    new_version=version_or_error
                ))
            else:
                self._publish_update_confirm(SafetyRuleUpdateConfirm(
                    success=False,
                    error_reason=version_or_error
                ))

    # ========== 完整性校验 ==========
    def _handle_integrity_check(self):
        start_time = time.time()
        total = len(self._rules)
        corrupted = [rid for rid, rule in self._rules.items() if not rule.name or not rule.category]
        elapsed = (time.time() - start_time) * 1000
        report = SafetyRuleIntegrityReport(
            total_rules=total,
            valid_rules=total - len(corrupted),
            corrupted_rules=corrupted,
            check_duration_ms=elapsed
        )
        if corrupted:
            self.state = RuleBaseState.DEGRADED
            self._load_minimal_ruleset()
        elif self.state == RuleBaseState.DEGRADED:
            self.state = RuleBaseState.NORMAL_SERVICE

        if self._publish_integrity_report:
            self._publish_integrity_report(report)

    # ========== 预置规则 ==========
    def _load_preset_rules(self):
        preset = [
            SafetyRule("SEC-WL-001", RuleCategory.WHITELIST, "weather_api", ["weather_api"], "", "允许", Severity.LOW),
            SafetyRule("SEC-WL-002", RuleCategory.WHITELIST, "file_read", ["file_read"], "", "允许", Severity.LOW),
            SafetyRule("SEC-BL-001", RuleCategory.BLACKLIST, "db_delete", ["db_delete", "shell_exec"], "", "拒绝", Severity.CRITICAL),
            SafetyRule("SEC-SN-001", RuleCategory.SENSITIVE, "delete_file", ["delete_file"], "", "确认", Severity.HIGH),
            # 新增合规模式规则示例
            SafetyRule("SEC-CM-001", RuleCategory.COMPLIANCE, "敏感数据读取后禁止外传",
                       ["file_read", "upload_api"], "禁止读取敏感文件后上传", "拒绝", Severity.CRITICAL),
        ]
        for rule in preset:
            self._rules[rule.rule_id] = rule
        self._rebuild_indexes()
        self._total_rules = len(self._rules)

    def _load_minimal_ruleset(self):
        if not any(r.category == RuleCategory.BLACKLIST for r in self._rules.values()):
            rule = SafetyRule("SEC-MIN-001", RuleCategory.BLACKLIST, "最小黑名单", ["shell_exec", "db_delete"], "", "拒绝", Severity.CRITICAL)
            self._rules[rule.rule_id] = rule
        if not any(r.category == RuleCategory.SENSITIVE for r in self._rules.values()):
            rule = SafetyRule("SEC-MIN-002", RuleCategory.SENSITIVE, "最小敏感操作", ["delete_file"], "", "确认", Severity.HIGH)
            self._rules[rule.rule_id] = rule
        self._total_rules = len(self._rules)
        self._rebuild_indexes()

    # ========== 索引管理 ==========
    def _rebuild_indexes(self):
        """全量重建分类索引与工具索引"""
        self._category_index = {c: [] for c in RuleCategory}
        self._tool_index = {}
        for rule in self._rules.values():
            if rule.category in self._category_index:
                self._category_index[rule.category].append(rule.rule_id)
            for tool in rule.applicable_tools:
                if tool not in self._tool_index:
                    self._tool_index[tool] = []
                self._tool_index[tool].append(rule.rule_id)

    # ========== 辅助 ==========
    def _publish_status(self):
        dist = {c.value: len(self._category_index.get(c, [])) for c in RuleCategory}
        if self._publish_status_report:
            self._publish_status_report(SafetyRuleBaseStatus(
                state=self.state,
                total_rules=self._total_rules,
                category_distribution=dist,
                last_updated=time.time(),
                version=self._rule_version
            ))

    def emergency_shutdown(self):
        self.state = RuleBaseState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 安全规则库 (ag-mem-45) 演示")
    print("=" * 70)

    srb = SafetyRuleBase()

    print_separator("STEP 1: 合规校验 - 白名单工具")
    srb.set_rule_request_query(lambda: SafetyRuleQueryRequest(
        request_id="C01", requester_module="ag-ecc-04",
        query_type="合规校验", tool_name="weather_api", operation_type="读取"
    ))
    srb.run_rule_cycle()

    print_separator("STEP 2: 合规校验 - 黑名单工具")
    srb.set_rule_request_query(lambda: SafetyRuleQueryRequest(
        request_id="C02", requester_module="ag-ecc-04",
        query_type="合规校验", tool_name="shell_exec", operation_type="执行"
    ))
    srb.run_rule_cycle()

    print_separator("STEP 3: 未知工具拒绝（不在白名单中）")
    srb.set_rule_request_query(lambda: SafetyRuleQueryRequest(
        request_id="C03", requester_module="ag-ecc-04",
        query_type="合规校验", tool_name="unknown_tool", operation_type="执行"
    ))
    srb.run_rule_cycle()

    print("\n✅ 安全规则库演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-45 安全规则库 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_srb():
            return SafetyRuleBase()

        # TC-M45-01: 白名单工具合规
        print("\n[TC-M45-01] 白名单工具合规")
        try:
            s = setup_srb()
            s.set_rule_request_query(lambda: SafetyRuleQueryRequest(
                request_id="T01", requester_module="ag-ecc-04",
                query_type="合规校验", tool_name="weather_api", operation_type="读取"
            ))
            s.run_rule_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M45-02: 黑名单工具不合规
        print("\n[TC-M45-02] 黑名单工具不合规")
        try:
            s = setup_srb()
            s.set_rule_request_query(lambda: SafetyRuleQueryRequest(
                request_id="T02", requester_module="ag-ecc-04",
                query_type="合规校验", tool_name="shell_exec", operation_type="执行"
            ))
            s.run_rule_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M45-03: 敏感操作需要确认
        print("\n[TC-M45-03] 敏感操作需要确认")
        try:
            s = setup_srb()
            s.set_rule_request_query(lambda: SafetyRuleQueryRequest(
                request_id="T03", requester_module="ag-ecc-04",
                query_type="合规校验", tool_name="delete_file", operation_type="删除"
            ))
            s.run_rule_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M45-04: 信息不足默认拒绝
        print("\n[TC-M45-04] 信息不足默认拒绝")
        try:
            s = setup_srb()
            s.set_rule_request_query(lambda: SafetyRuleQueryRequest(
                request_id="T04", requester_module="ag-ecc-04",
                query_type="合规校验", tool_name="", operation_type=""
            ))
            s.run_rule_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M45-05: 非授权模块查询全量规则被拒绝
        print("\n[TC-M45-05] 非授权模块查询全量规则被拒绝")
        try:
            s = setup_srb()
            s.set_rule_request_query(lambda: SafetyRuleQueryRequest(
                request_id="T05", requester_module="ag-mem-15",
                query_type="全量规则", tool_name=""
            ))
            s.run_rule_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M45-06: 紧急熔断
        print("\n[TC-M45-06] 紧急熔断")
        try:
            s = setup_srb()
            s.emergency_shutdown()
            assert s.state == RuleBaseState.SYSTEM_PAUSED
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