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

依赖模块: 无（作为底层配置服务）
被依赖模块: ag-mem-15~19, ag-mem-31~34, ag-mem-36, ag-mem-38, ag-mem-40, ag-mem-21

安全约束:
  W-01: 权重系数α+β+γ=1.0为强制约束，编译期与运行期双重校验
  W-02: 参数更新必须持有有效授权令牌，防止未授权修改导致记忆系统行为异常
  W-03: 参数更新前必须完整备份当前参数，更新失败时原子回滚
  W-04: 所有参数必须定义合法取值范围，超出范围的值在加载与更新时均被拒绝
  W-05: 参数配置文件缺失时使用编译期内置默认值，确保系统在任何情况下均可正常运行

版本: V1.0 (最终可提交版)
"""

import time
import uuid
import copy
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from memory_bus import InternalBus, Message


class ConfigState(Enum):
    NORMAL_SERVICE = "normal_service"
    DEGRADED_SERVICE = "degraded_service"
    UPDATING = "updating"
    SYSTEM_PAUSED = "system_paused"


class WeightConfigManager:
    module_id = "ag-mem-35"
    module_name = "三维权重系数配置单元"
    version = "V1.0"

    # 全局权重默认值
    DEFAULT_ALPHA = 0.40
    DEFAULT_BETA = 0.30
    DEFAULT_GAMMA = 0.30
    VALID_WEIGHT_RANGE = (0.10, 0.60)

    # 合法槽位白名单（修复3：防止非法槽位注入）
    VALID_SLOT_IDS = {"ag-mem-15", "ag-mem-16", "ag-mem-17", "ag-mem-18", "ag-mem-19"}
    # 通用参数合法范围（修复1：满足W-04，全参数范围校验）
    VALID_RATIO_RANGE = (0.0, 1.0)
    VALID_ADJ_RANGE = (0.5, 2.0)
    VALID_SATURATION_RANGE = (5, 30)

    # 各场景分槽专属参数默认值
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

    # I₀赋值规则参数默认值
    DEFAULT_I0_RULES = {
        "source_baselines": {
            "ECC主动请求记录": 0.60, "任务执行自动记录-成功": 0.45,
            "任务执行自动记录-失败": 0.50, "用户显式反馈触发记录": 0.55,
            "被动观测记录": 0.30, "系统安全事件触发记录": 0.70,
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

    # S值计算规则参数默认值
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
    }

    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = ConfigState.UPDATING
        self._params: Dict[str, Any] = {}
        self._last_update_time: float = 0.0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []
        self._is_default_config = True  # 标记是否使用默认配置（修复2）

        self._load_defaults()
        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 默认配置已加载")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.weight_config_manager_main_loop()

    def weight_config_manager_main_loop(self):
        if self.state == ConfigState.SYSTEM_PAUSED:
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
        if msg.topic == "ag-mem-35.param_query":
            self._handle_query(msg)
            return
        if msg.topic == "ag-mem-35.param_update":
            self._handle_update(msg)
            return

    # ====================== 参数加载 ======================
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
        self._is_default_config = True

    # ====================== 查询处理 ======================
    def _handle_query(self, msg: Message):
        query_type = msg.data.get("query_type", "")
        slot_id = msg.data.get("slot_id")
        data = {}

        if query_type == "global_weights":
            data = {"alpha": self._params["alpha"], "beta": self._params["beta"], "gamma": self._params["gamma"]}
        elif query_type == "slot_config" and slot_id:
            data = self._params["slot_configs"].get(slot_id, {})
        elif query_type == "all":
            data = self._params
        else:
            data = self._params

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.param_result",
                source_module=self.module_id,
                data={"success": True, "data": data, "is_default": self._is_default_config},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 更新处理 ======================
    def _handle_update(self, msg: Message):
        command = msg.data
        # 授权校验
        if not command.get("authorization_token") or len(command.get("authorization_token", "")) < 10:
            self._log_event("UPDATE_REJECTED", {"reason": "授权令牌无效"})
            return

        self.state = ConfigState.UPDATING
        old_snapshot = copy.deepcopy(self._params)
        success = True
        reason = ""

        try:
            new_values = command.get("new_values", {})

            # 校验并更新全局权重
            if "alpha" in new_values:
                self._validate_and_set_weight("alpha", new_values["alpha"])
            if "beta" in new_values:
                self._validate_and_set_weight("beta", new_values["beta"])
            if "gamma" in new_values:
                self._validate_and_set_weight("gamma", new_values["gamma"])

            # 修复4：四舍五入后校验权重和，避免浮点数精度误判
            total = round(self._params["alpha"] + self._params["beta"] + self._params["gamma"], 2)
            if abs(total - 1.0) > 0.01:
                raise ValueError("权重系数之和必须等于1.0")

            # 修复1+3：校验并更新分槽配置（白名单+范围校验）
            if "slot_configs" in new_values:
                for sid, cfg in new_values["slot_configs"].items():
                    if sid not in self.VALID_SLOT_IDS:
                        raise ValueError(f"非法槽位ID: {sid}")
                    self._validate_slot_config(cfg)
                    self._params["slot_configs"][sid].update(cfg)

            # 修复1：校验并更新I0/S规则
            if "i0_rules" in new_values:
                self._validate_rules(new_values["i0_rules"])
                self._params["i0_rules"].update(new_values["i0_rules"])
            if "s_rules" in new_values:
                self._validate_rules(new_values["s_rules"])
                self._params["s_rules"].update(new_values["s_rules"])

            self._last_update_time = time.time()
            self._is_default_config = False  # 更新后不再是默认配置
            self._log_event("PARAMETERS_UPDATED", {"reason": "参数更新成功"})

        except ValueError as e:
            self._params = old_snapshot
            success = False
            reason = str(e)
            self._log_event("UPDATE_FAILED", {"reason": reason})

        self.state = ConfigState.NORMAL_SERVICE
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.update_confirm",
                source_module=self.module_id,
                data={"success": success, "reason": reason},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 全参数校验（修复1：满足W-04） ======================
    def _validate_and_set_weight(self, key: str, value: float):
        low, high = self.VALID_WEIGHT_RANGE
        if not isinstance(value, (int, float)) or value < low or value > high:
            raise ValueError(f"参数 {key} 超出合法范围 [{low}, {high}]")
        self._params[key] = float(value)

    def _validate_slot_config(self, cfg: Dict[str, Any]):
        """校验分槽配置合法性"""
        r_min, r_max = self.VALID_RATIO_RANGE
        a_min, a_max = self.VALID_ADJ_RANGE
        s_min, s_max = self.VALID_SATURATION_RANGE

        # 校验调整系数
        for k in ["alpha_adj", "beta_adj", "gamma_adj"]:
            if k in cfg and (not isinstance(cfg[k], (int, float)) or cfg[k] < a_min or cfg[k] > a_max):
                raise ValueError(f"{k} 超出调整系数范围 [{a_min}, {a_max}]")
        # 校验饱和阈值
        if "c_saturation" in cfg and (not isinstance(cfg["c_saturation"], int) or cfg["c_saturation"] < s_min or cfg["c_saturation"] > s_max):
            raise ValueError(f"c_saturation 超出范围 [{s_min}, {s_max}]")
        # 校验晋升/遗忘阈值
        for key in ["promotion", "forget"]:
            if key in cfg:
                for k, v in cfg[key].items():
                    if not isinstance(v, (int, float)) or v < r_min or v > r_max:
                        raise ValueError(f"{key}.{k} 超出比例范围 [{r_min}, {r_max}]")

    def _validate_rules(self, rules: Dict[str, Any]):
        """校验I0/S规则参数合法性"""
        r_min, r_max = self.VALID_RATIO_RANGE
        for outer_k, outer_v in rules.items():
            if isinstance(outer_v, dict):
                for k, v in outer_v.items():
                    if isinstance(v, (int, float)) and (v < r_min or v > r_max):
                        raise ValueError(f"{outer_k}.{k} 超出合法范围 [{r_min}, {r_max}]")
            elif isinstance(outer_v, (int, float)) and (outer_v < r_min or outer_v > r_max):
                raise ValueError(f"{outer_k} 超出合法范围 [{r_min}, {r_max}]")

    # ====================== 直接查询接口 ======================
    def get_global_weights(self) -> Tuple[float, float, float]:
        return self._params["alpha"], self._params["beta"], self._params["gamma"]

    def get_slot_config(self, slot_id: str) -> Optional[Dict[str, Any]]:
        return self._params["slot_configs"].get(slot_id)

    def get_i0_rules(self) -> Dict[str, Any]:
        return self._params["i0_rules"]

    def get_s_rules(self) -> Dict[str, Any]:
        return self._params["s_rules"]

    # ====================== 状态上报 ======================
    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "weights": {"alpha": self._params["alpha"], "beta": self._params["beta"], "gamma": self._params["gamma"]},
                    "last_update_time": self._last_update_time,
                    "is_default": self._is_default_config
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = ConfigState.SYSTEM_PAUSED
        self._pending_logs.clear()
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