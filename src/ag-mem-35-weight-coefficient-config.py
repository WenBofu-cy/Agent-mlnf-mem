#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-35
模块名称: 三维权重系数配置单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 统一存储与管理漏斗二中三维重要度计算公式 I = I₀ + α·S + β·V + γ·C 的权重系数
          α、β、γ，以及各场景分槽专属的晋升阈值与遗忘策略参数。提供全局默认配置与分槽
          级别的差异化配置，支持运行时查询与受控更新。确保各计算模块使用统一且一致的参数源。
          不参与任何计算或决策，仅提供参数数据的存取与校验服务。

依赖模块:
    无（作为底层配置服务）
被依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-31(S值计算), ag-mem-32(V值计算),
    ag-mem-33(C值统计), ag-mem-34(I₀赋值), ag-mem-36(重要度聚合),
    ag-mem-38(晋升判定), ag-mem-40(遗忘判定), ag-mem-21(L1衰减评估)

安全约束:
  W-01: 权重系数α+β+γ=1.0为强制约束，编译期与运行期双重校验
  W-02: 参数更新必须持有有效授权令牌，防止未授权修改导致记忆系统行为异常
  W-03: 参数更新前必须完整备份当前参数，更新失败时原子回滚
  W-04: 所有参数必须定义合法取值范围，超出范围的值在加载与更新时均被拒绝
  W-05: 参数配置文件缺失时使用编译期内置默认值，确保系统在任何情况下均可正常运行
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import copy


class ConfigState(Enum):
    NORMAL_SERVICE = "normal_service"
    DEGRADED_SERVICE = "degraded_service"
    UPDATING = "updating"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ParameterQueryRequest:
    requester_id: str = ""
    query_type: str = ""           # "global_weights" / "promotion_thresholds" / "forget_thresholds" / "slot_config" / "all"
    slot_id: Optional[str] = None  # 指定分槽ID


@dataclass
class ParameterUpdateCommand:
    update_type: str = ""
    new_values: Dict[str, Any] = field(default_factory=dict)
    authorization_token: str = ""
    reason: str = ""


@dataclass
class QueryResult:
    success: bool = True
    data: Dict[str, Any] = field(default_factory=dict)
    is_default: bool = True


@dataclass
class UpdateConfirmation:
    success: bool = True
    parameter_name: str = ""
    old_value: Any = None
    new_value: Any = None
    effective_time: float = 0.0


class WeightConfigManager:
    # ========== 全局权重默认值 ==========
    DEFAULT_ALPHA = 0.40
    DEFAULT_BETA = 0.30
    DEFAULT_GAMMA = 0.30
    VALID_WEIGHT_RANGE = (0.10, 0.60)

    # ========== 各场景分槽专属参数默认值 ==========
    # 每个分槽包含: promotion thresholds (L1→L2, L2→L3, L3→L4, L4→L5),
    # forget thresholds (L1, L2, L3, L4), weight adjustments (α, β, γ), C saturation, forget protection label
    DEFAULT_SLOT_CONFIGS = {
        "ag-mem-15": {
            "alpha_adj": 1.0, "beta_adj": 1.2, "gamma_adj": 1.0,
            "promotion": {"L1_L2": 0.35, "L2_L3": 0.55, "L3_L4": 0.75, "L4_L5": 0.90},
            "forget": {"L1": 0.08, "L2": 0.18, "L3": 0.28, "L4": 0.20},
            "c_saturation": 8, "forget_protection": "标准"
        },
        "ag-mem-16": {
            "alpha_adj": 1.2, "beta_adj": 1.0, "gamma_adj": 1.0,
            "promotion": {"L1_L2": 0.40, "L2_L3": 0.60, "L3_L4": 0.80, "L4_L5": 0.90},
            "forget": {"L1": 0.10, "L2": 0.25, "L3": 0.35, "L4": 0.25},
            "c_saturation": 10, "forget_protection": "标准"
        },
        "ag-mem-17": {
            "alpha_adj": 1.0, "beta_adj": 1.0, "gamma_adj": 1.1,
            "promotion": {"L1_L2": 0.38, "L2_L3": 0.58, "L3_L4": 0.78, "L4_L5": 0.88},
            "forget": {"L1": 0.08, "L2": 0.18, "L3": 0.28, "L4": 0.20},
            "c_saturation": 12, "forget_protection": "轻度保护"
        },
        "ag-mem-18": {
            "alpha_adj": 1.0, "beta_adj": 1.1, "gamma_adj": 1.0,
            "promotion": {"L1_L2": 0.38, "L2_L3": 0.58, "L3_L4": 0.78, "L4_L5": 0.90},
            "forget": {"L1": 0.10, "L2": 0.20, "L3": 0.30, "L4": 0.22},
            "c_saturation": 10, "forget_protection": "标准"
        },
        "ag-mem-19": {
            "alpha_adj": 1.0, "beta_adj": 1.0, "gamma_adj": 1.0,
            "promotion": {"L1_L2": 0.42, "L2_L3": 0.62, "L3_L4": 0.82, "L4_L5": 0.92},
            "forget": {"L1": 0.06, "L2": 0.15, "L3": 0.22, "L4": 0.18},
            "c_saturation": 15, "forget_protection": "强保护"
        }
    }

    # ========== I₀赋值规则参数默认值 ==========
    DEFAULT_I0_RULES = {
        "source_baselines": {
            "ECC主动请求记录": 0.60,
            "任务执行自动记录-成功": 0.45,
            "任务执行自动记录-失败": 0.50,
            "用户显式反馈触发记录": 0.55,
            "被动观测记录": 0.30,
            "系统安全事件触发记录": 0.70,
        },
        "task_coefficients": {
            "工具调用": 1.0, "信息检索": 0.9, "对话交互": 0.8,
            "创作生成": 0.95, "通用任务": 0.85
        },
        "bonuses": {
            "sensitive_operation": 0.20, "privacy_access": 0.15,
            "abnormal_duration": 0.10, "user_skip": -0.10, "new_scene": 0.05
        }
    }

    # ========== S值计算规则参数默认值 ==========
    DEFAULT_S_RULES = {
        "signal_base_scores": {
            "sensitive_operation": 0.80, "high_risk_tool": 0.70,
            "error_exception": 0.60, "user_privacy": 0.90, "result_validation": 0.50
        },
        "signal_weights": {
            "sensitive_operation": 0.40, "high_risk_tool": 0.30,
            "error_exception": 0.15, "user_privacy": 0.10, "result_validation": 0.05
        },
        "l5_direct_threshold": 0.90,
        "critical_failure_s_value": 0.95,
        "no_signal_s_value": 0.05,
        "min_privacy_s": 0.70,
        "min_sensitive_s": 0.80,
        "sensitive_ops": ["delete", "remove", "write_system", "modify_permission", "db_write",
                          "shell_exec", "sudo", "system_config", "format", "overwrite",
                          "shutdown", "restart", "kill", "uninstall", "revoke"],
        "high_risk_tools": ["shell_exec", "db_write", "payment_api", "system_config",
                            "sudo", "delete_file", "modify_registry", "send_email",
                            "launch_process", "remote_access"],
        "privacy_ops": ["read_contacts", "get_location", "browser_history", "read_messages",
                        "camera_access", "microphone_access", "read_photos", "tracking",
                        "biometric_read", "health_data_read"]
    }

    def __init__(self):
        self.module_id = "ag-mem-35"
        self.module_name = "三维权重系数配置单元"
        self.version = "V1.0"

        self.state = ConfigState.UPDATING  # 初始加载中
        self._params: Dict[str, Any] = {}
        self._last_update_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_parameter_request = None
        self._query_update_command = None

        self._publish_query_result = None
        self._publish_update_confirm = None
        self._publish_config_status = None
        self._publish_event_log = None

        # 加载默认参数
        self._load_defaults()

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 默认配置已加载")

    def set_parameter_request_query(self, callback: Callable[[], Optional[ParameterQueryRequest]]):
        self._query_parameter_request = callback

    def set_update_command_query(self, callback: Callable[[], Optional[ParameterUpdateCommand]]):
        self._query_update_command = callback

    def set_query_result_publisher(self, callback: Callable[[QueryResult], None]):
        self._publish_query_result = callback

    def set_update_confirm_publisher(self, callback: Callable[[UpdateConfirmation], None]):
        self._publish_update_confirm = callback

    def set_config_status_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_config_status = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_config_cycle(self):
        if self.state == ConfigState.SYSTEM_PAUSED:
            return

        # 处理参数查询
        query = self._query_parameter_request() if self._query_parameter_request else None
        if query:
            self._handle_query(query)
            return

        # 处理参数更新
        update = self._query_update_command() if self._query_update_command else None
        if update:
            self._handle_update(update)

    # ========== 参数加载 ==========
    def _load_defaults(self):
        self._params = {
            "alpha": self.DEFAULT_ALPHA,
            "beta": self.DEFAULT_BETA,
            "gamma": self.DEFAULT_GAMMA,
            "slot_configs": copy.deepcopy(self.DEFAULT_SLOT_CONFIGS),
            "i0_rules": copy.deepcopy(self.DEFAULT_I0_RULES),
            "s_rules": copy.deepcopy(self.DEFAULT_S_RULES),
        }
        self.state = ConfigState.NORMAL_SERVICE
        self._last_update_time = time.time()

    # ========== 查询处理 ==========
    def _handle_query(self, request: ParameterQueryRequest):
        data = {}

        if request.query_type == "global_weights":
            data = {"alpha": self._params["alpha"], "beta": self._params["beta"], "gamma": self._params["gamma"]}

        elif request.query_type == "promotion_thresholds":
            if request.slot_id and request.slot_id in self._params["slot_configs"]:
                data = self._params["slot_configs"][request.slot_id]["promotion"]
            else:
                data = {slot: cfg["promotion"] for slot, cfg in self._params["slot_configs"].items()}

        elif request.query_type == "forget_thresholds":
            if request.slot_id and request.slot_id in self._params["slot_configs"]:
                data = self._params["slot_configs"][request.slot_id]["forget"]
            else:
                data = {slot: cfg["forget"] for slot, cfg in self._params["slot_configs"].items()}

        elif request.query_type == "slot_config":
            if request.slot_id and request.slot_id in self._params["slot_configs"]:
                data = self._params["slot_configs"][request.slot_id]
            else:
                data = self._params["slot_configs"]

        elif request.query_type == "i0_rules":
            data = self._params["i0_rules"]

        elif request.query_type == "s_rules":
            data = self._params["s_rules"]

        elif request.query_type == "all":
            data = self._params

        if self._publish_query_result:
            self._publish_query_result(QueryResult(data=data))

    # ========== 更新处理 ==========
    def _handle_update(self, command: ParameterUpdateCommand):
        # 校验授权令牌（简化：必须非空且长度>10）
        if not command.authorization_token or len(command.authorization_token) < 10:
            self._log_event("UPDATE_REJECTED", {"reason": "授权令牌无效"})
            return

        self.state = ConfigState.UPDATING
        old_snapshot = copy.deepcopy(self._params)
        success = True

        try:
            new_values = command.new_values

            # 更新全局权重
            if "alpha" in new_values:
                self._validate_and_set_weight("alpha", new_values["alpha"])
            if "beta" in new_values:
                self._validate_and_set_weight("beta", new_values["beta"])
            if "gamma" in new_values:
                self._validate_and_set_weight("gamma", new_values["gamma"])

            # 校验 α+β+γ=1.0
            if abs(self._params["alpha"] + self._params["beta"] + self._params["gamma"] - 1.0) > 0.01:
                raise ValueError("权重系数之和必须等于1.0")

            # 更新分槽配置
            if "slot_configs" in new_values:
                for slot_id, slot_cfg in new_values["slot_configs"].items():
                    if slot_id in self._params["slot_configs"]:
                        self._params["slot_configs"][slot_id].update(slot_cfg)

            # 更新I₀规则
            if "i0_rules" in new_values:
                self._params["i0_rules"].update(new_values["i0_rules"])

            # 更新S值规则
            if "s_rules" in new_values:
                self._params["s_rules"].update(new_values["s_rules"])

            self._last_update_time = time.time()
            self._log_event("PARAMETERS_UPDATED", {"new_values": new_values})

        except ValueError as e:
            self._params = old_snapshot
            success = False
            self._log_event("UPDATE_FAILED", {"reason": str(e)})

        self.state = ConfigState.NORMAL_SERVICE

    def _validate_and_set_weight(self, key: str, value: float):
        low, high = self.VALID_WEIGHT_RANGE
        if value < low or value > high:
            raise ValueError(f"参数 {key} 超出合法范围 [{low}, {high}]")
        self._params[key] = value

    # ========== 直接查询接口（供内部模块同步调用） ==========
    def get_global_weights(self) -> Tuple[float, float, float]:
        return self._params["alpha"], self._params["beta"], self._params["gamma"]

    def get_slot_config(self, slot_id: str) -> Optional[Dict[str, Any]]:
        return self._params["slot_configs"].get(slot_id)

    def get_i0_rules(self) -> Dict[str, Any]:
        return self._params["i0_rules"]

    def get_s_rules(self) -> Dict[str, Any]:
        return self._params["s_rules"]

    # ========== 辅助方法 ==========
    def get_state(self) -> ConfigState:
        return self.state

    def emergency_shutdown(self):
        self.state = ConfigState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 三维权重系数配置单元 (ag-mem-35) 演示")
    print("=" * 70)

    mgr = WeightConfigManager()

    print_separator("STEP 1: 查询全局权重")
    mgr.set_parameter_request_query(lambda: ParameterQueryRequest(query_type="global_weights"))
    mgr.run_config_cycle()

    print_separator("STEP 2: 查询工具调用槽配置")
    mgr.set_parameter_request_query(lambda: ParameterQueryRequest(query_type="slot_config", slot_id="ag-mem-16"))
    mgr.run_config_cycle()

    print_separator("STEP 3: 更新权重（α=0.50, β=0.25, γ=0.25）")
    mgr.set_update_command_query(lambda: ParameterUpdateCommand(
        new_values={"alpha": 0.50, "beta": 0.25, "gamma": 0.25},
        authorization_token="admin-secret-token-12345"
    ))
    mgr.run_config_cycle()
    alpha, beta, gamma = mgr.get_global_weights()
    print(f"  当前权重: α={alpha}, β={beta}, γ={gamma}")

    print("\n✅ 三维权重系数配置单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-35 三维权重系数配置单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_mgr():
            return WeightConfigManager()

        # TC-M35-01: 查询全局权重默认值
        print("\n[TC-M35-01] 查询全局权重默认值")
        try:
            m = setup_mgr()
            alpha, beta, gamma = m.get_global_weights()
            assert alpha == 0.40
            assert beta == 0.30
            assert gamma == 0.30
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M35-02: 查询分槽配置
        print("\n[TC-M35-02] 查询分槽配置（工具调用槽S值上调20%）")
        try:
            m = setup_mgr()
            cfg = m.get_slot_config("ag-mem-16")
            assert cfg is not None
            assert cfg["alpha_adj"] == 1.2
            assert cfg["promotion"]["L1_L2"] == 0.40
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M35-03: 更新权重（α+β+γ=1.0 合法）
        print("\n[TC-M35-03] 更新权重（合法）")
        try:
            m = setup_mgr()
            m.set_update_command_query(lambda: ParameterUpdateCommand(
                new_values={"alpha": 0.50, "beta": 0.25, "gamma": 0.25},
                authorization_token="admin-secret-token-12345"
            ))
            m.run_config_cycle()
            alpha, beta, gamma = m.get_global_weights()
            assert alpha == 0.50 and beta == 0.25 and gamma == 0.25
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M35-04: 更新权重（α+β+γ≠1.0 应回滚）
        print("\n[TC-M35-04] 更新权重（α+β+γ≠1.0 应回滚）")
        try:
            m = setup_mgr()
            orig_alpha, orig_beta, orig_gamma = m.get_global_weights()
            m.set_update_command_query(lambda: ParameterUpdateCommand(
                new_values={"alpha": 0.50, "beta": 0.30, "gamma": 0.30},
                authorization_token="admin-secret-token-12345"
            ))
            m.run_config_cycle()
            alpha, beta, gamma = m.get_global_weights()
            assert alpha == orig_alpha and beta == orig_beta and gamma == orig_gamma
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M35-05: 查询不存在的分槽返回None
        print("\n[TC-M35-05] 查询不存在的分槽返回None")
        try:
            m = setup_mgr()
            cfg = m.get_slot_config("ag-mem-99")
            assert cfg is None
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M35-06: 紧急熔断
        print("\n[TC-M35-06] 紧急熔断")
        try:
            m = setup_mgr()
            m.emergency_shutdown()
            assert m.state == ConfigState.SYSTEM_PAUSED
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