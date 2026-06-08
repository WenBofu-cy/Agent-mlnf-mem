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

依赖模块: 无
被依赖模块: ag-mem-43, ag-mem-29, ag-ecc-04, ag-mem-01

安全约束:
  SR-01: 安全规则库为只读数据，运行时不得被任何控制模块或自动化流程修改
  SR-02: 规则更新必须经过授权令牌验证、签名校验与沙箱逻辑一致性验证三道关卡
  SR-03: 规则库降级时强制加载内置最小安全规则集，确保安全底线在任何情况下均可用
  SR-04: 合规校验请求信息不足时，默认返回“不合规”，遵循安全保守原则
  SR-05: 全量规则查询仅向 ag-ecc-04 和 ag-mem-29 开放，其他模块无权获取
  SR-06: 所有规则更新与拒绝事件必须记录完整的安全事件日志，不可篡改
  SR-07: 本库完全独立于漏斗记忆系统，不参与记忆沉淀、晋升、遗忘任何环节

版本: V1.0 (总线集成版)
"""

import time
import uuid
import copy
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


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


class SafetyRuleBase:
    module_id = "ag-mem-45"
    module_name = "安全规则库"
    version = "V1.0"
    _rule_version = "1.0.0"

    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = RuleBaseState.LOADING
        self._rules: Dict[str, Dict[str, Any]] = {}
        self._category_index: Dict[str, List[str]] = {c.value: [] for c in RuleCategory}
        self._tool_index: Dict[str, List[str]] = {}
        self._query_cache: Dict[str, tuple] = {}
        self._total_rules: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        self._load_preset_rules()
        self.state = RuleBaseState.NORMAL_SERVICE

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 预置规则数={self._total_rules}")

    # ====================== 主循环 ======================
    def safety_rule_engine_main_loop(self):
        if self.state == RuleBaseState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 安全合规校验请求（来自 ag-mem-43 或 ag-ecc-04）
        if msg.topic == "ag-mem-45.safety_check":
            self._handle_safety_check(msg)
            return

        # 安全规则查询请求（通用查询）
        if msg.topic == "ag-mem-45.rule_query":
            self._handle_rule_query(msg)
            return

    def _handle_safety_check(self, msg: Message):
        """处理合规校验请求"""
        data = msg.data
        tool_name = data.get("tool_name", "")
        operation = data.get("operation_type", "")
        tool_call_history = data.get("tool_call_history", [])
        parameters = data.get("parameters", {})

        result = self._perform_compliance_check(tool_name, operation, tool_call_history)

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.safety_check_result",
                source_module=self.module_id,
                data={
                    "compliant": result["compliant"],
                    "violated_rules": result["violated_rules"],
                    "severity": result["severity"],
                    "required_action": result.get("required_action", ""),
                    "suggestion": result.get("suggestion", "")
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _handle_rule_query(self, msg: Message):
        """处理通用规则查询"""
        data = msg.data
        requester = msg.source_module
        query_type = data.get("query_type", "")

        # 全量规则仅授权模块可查
        if query_type == "全量规则" and requester not in ("ag-ecc-04", "ag-mem-29"):
            if self.bus:
                self.bus.publish(
                    topic=f"{requester}.rule_query_result",
                    source_module=self.module_id,
                    data={"matched_rules": [], "total_matched": 0},
                    target_module=requester,
                    correlation_id=msg.correlation_id
                )
            return

        matched = self._query_rules(query_type, data.get("tool_name", ""))
        if self.bus:
            self.bus.publish(
                topic=f"{requester}.rule_query_result",
                source_module=self.module_id,
                data={
                    "matched_rules": matched,
                    "total_matched": len(matched)
                },
                target_module=requester,
                correlation_id=msg.correlation_id
            )

    # ====================== 核心合规校验 ======================
    def _perform_compliance_check(self, tool_name: str, operation: str,
                                  tool_history: List[str]) -> Dict[str, Any]:
        # 保守默认：信息不足时拒绝（SR-04）
        if not tool_name and not operation:
            return {
                "compliant": False,
                "violated_rules": ["信息不足，默认拒绝"],
                "severity": "严重",
                "suggestion": "请提供工具名称或操作类型"
            }

        # 1. 黑名单检查
        blacklist = self._find_rules_by_category_and_tool(RuleCategory.BLACKLIST.value, tool_name)
        if blacklist:
            return {
                "compliant": False,
                "violated_rules": [r["name"] for r in blacklist],
                "severity": "严重",
                "required_action": "禁止执行",
                "suggestion": "该工具或操作在黑名单中"
            }

        # 2. 合规模式检查
        compliance = self._check_compliance_patterns(tool_history)
        if compliance:
            return {
                "compliant": False,
                "violated_rules": [r["name"] for r in compliance],
                "severity": "严重",
                "required_action": "禁止执行",
                "suggestion": "工具调用序列触发合规模式告警"
            }

        # 3. 白名单检查
        whitelist = self._find_rules_by_category_and_tool(RuleCategory.WHITELIST.value, tool_name)
        is_whitelisted = len(whitelist) > 0

        # 4. 敏感操作检查
        sensitive = self._find_rules_by_category_and_tool(RuleCategory.SENSITIVE.value, tool_name)
        needs_confirm = len(sensitive) > 0

        # 未知工具保守拒绝
        if not is_whitelisted and not needs_confirm:
            return {
                "compliant": False,
                "violated_rules": ["不在白名单中"],
                "severity": "高",
                "required_action": "禁止执行",
                "suggestion": "该工具未在白名单中注册，禁止自动执行"
            }

        if not is_whitelisted and needs_confirm:
            return {
                "compliant": True,
                "violated_rules": [],
                "severity": "中",
                "required_action": "需要用户确认",
                "suggestion": "该操作不在白名单中且属于敏感操作，需用户确认"
            }

        if needs_confirm:
            return {
                "compliant": True,
                "violated_rules": [],
                "severity": "中",
                "required_action": "需要用户确认",
                "suggestion": "敏感操作，需要用户二次确认"
            }

        return {
            "compliant": True,
            "violated_rules": [],
            "severity": "低",
            "suggestion": "操作合规"
        }

    # ====================== 规则查询辅助 ======================
    def _find_rules_by_category_and_tool(self, category: str, tool_name: str) -> List[Dict]:
        results = []
        for rule_id in self._category_index.get(category, []):
            rule = self._rules.get(rule_id)
            if rule and (not tool_name or tool_name in rule.get("applicable_tools", [])):
                results.append(rule)
        return results

    def _check_compliance_patterns(self, tool_history: List[str]) -> List[Dict]:
        violated = []
        for rule_id in self._category_index.get(RuleCategory.COMPLIANCE.value, []):
            rule = self._rules.get(rule_id)
            if rule and rule.get("condition"):
                required_tools = rule.get("applicable_tools", [])
                if required_tools and all(t in tool_history for t in required_tools):
                    violated.append(rule)
        return violated

    def _query_rules(self, query_type: str, tool_name: str) -> List[Dict]:
        if query_type == "全量规则":
            return list(self._rules.values())
        elif query_type == "权限分级":
            return self._find_rules_by_category_and_tool(RuleCategory.PERMISSION.value, tool_name)
        elif query_type == "敏感操作判定":
            return self._find_rules_by_category_and_tool(RuleCategory.SENSITIVE.value, tool_name)
        elif query_type == "安全等级查询":
            return [self._rules[rid] for rid in self._category_index.get(RuleCategory.SAFETY_LEVEL.value, []) if rid in self._rules]
        return []

    # ====================== 预置规则 ======================
    def _load_preset_rules(self):
        preset = [
            {
                "rule_id": "SEC-WL-001",
                "category": RuleCategory.WHITELIST.value,
                "name": "weather_api",
                "applicable_tools": ["weather_api"],
                "condition": "",
                "action": "允许",
                "severity": Severity.LOW.value
            },
            {
                "rule_id": "SEC-WL-002",
                "category": RuleCategory.WHITELIST.value,
                "name": "file_read",
                "applicable_tools": ["file_read"],
                "condition": "",
                "action": "允许",
                "severity": Severity.LOW.value
            },
            {
                "rule_id": "SEC-BL-001",
                "category": RuleCategory.BLACKLIST.value,
                "name": "db_delete / shell_exec",
                "applicable_tools": ["db_delete", "shell_exec"],
                "condition": "",
                "action": "拒绝",
                "severity": Severity.CRITICAL.value
            },
            {
                "rule_id": "SEC-SN-001",
                "category": RuleCategory.SENSITIVE.value,
                "name": "delete_file",
                "applicable_tools": ["delete_file"],
                "condition": "",
                "action": "确认",
                "severity": Severity.HIGH.value
            },
            {
                "rule_id": "SEC-CM-001",
                "category": RuleCategory.COMPLIANCE.value,
                "name": "敏感数据读取后禁止外传",
                "applicable_tools": ["file_read", "upload_api"],
                "condition": "禁止读取敏感文件后上传",
                "action": "拒绝",
                "severity": Severity.CRITICAL.value
            }
        ]
        for rule in preset:
            self._rules[rule["rule_id"]] = rule
        self._rebuild_indexes()
        self._total_rules = len(self._rules)

    def _rebuild_indexes(self):
        self._category_index = {c.value: [] for c in RuleCategory}
        self._tool_index = {}
        for rule in self._rules.values():
            cat = rule.get("category", "")
            if cat in self._category_index:
                self._category_index[cat].append(rule["rule_id"])
            for tool in rule.get("applicable_tools", []):
                if tool not in self._tool_index:
                    self._tool_index[tool] = []
                self._tool_index[tool].append(rule["rule_id"])

    # ====================== 状态上报 ======================
    def _report_status(self):
        if self.bus:
            dist = {c.value: len(self._category_index.get(c.value, [])) for c in RuleCategory}
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_rules": self._total_rules,
                    "category_distribution": dist,
                    "version": self._rule_version
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = RuleBaseState.SYSTEM_PAUSED
        self._log_event("SYSTEM_EVENT", {"sub_type": "emergency_shutdown"})

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        log_entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(log_entry)
        if self.bus:
            self.bus.publish_to_module("ag-mem-51", "log_event", self.module_id, log_entry)

    def collect_pending_logs(self) -> List[Dict]:
        tmp = self._pending_logs.copy()
        self._pending_logs.clear()
        return tmp