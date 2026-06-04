#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MemoryBus 全局记忆总线
Agent-mlnf-mem 内部模块间通信的唯一中枢

版本：V1.0
原创提出者：文波福
开源协议：CC BY-NC 4.0

职责：
  - 作为双漏斗记忆中枢 51 个模块间消息路由的唯一中转站
  - 每个模块通过 publish(topic, data) 发布消息，通过 subscribe(topic, callback) 订阅消息
  - 支持同步和异步两种投递模式
  - 维护模块注册表，支持按模块 ID 点对点路由

设计原则：
  - 模块间不直接引用，全部通过 MemoryBus 解耦
  - 每个 topic 对应一种消息类型，命名规范为 "模块ID.事件类型"
  - 发布者不关心订阅者是谁，订阅者不关心消息来源
"""

from typing import Dict, List, Callable, Any, Optional
from collections import defaultdict
from dataclasses import dataclass, field
import time
import uuid
import threading


@dataclass
class Message:
    """总线消息"""
    message_id: str = ""
    topic: str = ""                      # 消息主题（如 "ag-mem-01.experience_query"）
    source_module: str = ""              # 发布者模块 ID
    target_module: str = ""              # 目标模块 ID（空表示广播）
    data: Any = None                     # 消息负载
    timestamp: float = field(default_factory=time.time)


class MemoryBus:
    """
    全局记忆总线

    使用方式：
        bus = MemoryBus()
        
        # 订阅
        bus.subscribe("ag-mem-01.experience_query", handler_func)
        bus.subscribe_to_module("ag-mem-01", handler_func)  # 接收发给该模块的所有消息
        
        # 发布
        bus.publish("ag-mem-01.experience_query", {"user_id": "U001"})
        bus.publish_to_module("ag-mem-02", "slot_query", {"user_id": "U001"})
        
        # 主循环轮询
        bus.process_all()  # 同步处理所有待投递消息
    """

    def __init__(self):
        self._subscriptions: Dict[str, List[Callable]] = defaultdict(list)  # topic -> [handlers]
        self._module_subscriptions: Dict[str, List[Callable]] = defaultdict(list)  # module_id -> [handlers]
        self._message_queue: List[Message] = []
        self._lock = threading.Lock()
        self._message_counter: int = 0
        self._pending_logs: List[Dict[str, Any]] = []

    # ========== 订阅接口 ==========
    def subscribe(self, topic: str, handler: Callable[[Message], None]):
        """订阅指定 topic 的消息"""
        with self._lock:
            self._subscriptions[topic].append(handler)

    def subscribe_to_module(self, module_id: str, handler: Callable[[Message], None]):
        """订阅发给指定模块的所有消息（按 target_module 过滤）"""
        with self._lock:
            self._module_subscriptions[module_id].append(handler)

    # ========== 发布接口 ==========
    def publish(self, topic: str, source_module: str, data: Any = None, target_module: str = ""):
        """
        发布消息到指定 topic

        Args:
            topic: 消息主题
            source_module: 发布者模块 ID
            data: 消息负载
            target_module: 目标模块 ID（空表示广播给所有订阅该 topic 的模块）
        """
        self._message_counter += 1
        message = Message(
            message_id=f"MSG-{self._message_counter:06d}",
            topic=topic,
            source_module=source_module,
            target_module=target_module,
            data=data
        )
        with self._lock:
            self._message_queue.append(message)

    def publish_to_module(self, target_module: str, event_type: str, source_module: str, data: Any = None):
        """
        向指定模块发布点对点消息

        Args:
            target_module: 目标模块 ID
            event_type: 事件类型
            source_module: 发布者模块 ID
            data: 消息负载
        """
        topic = f"{target_module}.{event_type}"
        self.publish(topic, source_module, data, target_module)

    # ========== 消息处理 ==========
    def process_one(self) -> int:
        """
        处理队列中的下一条消息（同步）
        Returns:
            处理的消息数（0 或 1）
        """
        with self._lock:
            if not self._message_queue:
                return 0
            message = self._message_queue.pop(0)

        # 1. 投递给 topic 订阅者
        handlers = self._subscriptions.get(message.topic, [])
        for handler in handlers:
            try:
                handler(message)
            except Exception as e:
                self._log_error("topic_handler_error", str(e))

        # 2. 投递给目标模块的订阅者
        if message.target_module:
            module_handlers = self._module_subscriptions.get(message.target_module, [])
            for handler in module_handlers:
                try:
                    handler(message)
                except Exception as e:
                    self._log_error("module_handler_error", str(e))

        return 1

    def process_all(self) -> int:
        """处理队列中的所有待投递消息（同步）"""
        total = 0
        while True:
            processed = self.process_one()
            if processed == 0:
                break
            total += processed
        return total

    def process_batch(self, max_count: int = 50) -> int:
        """处理最多 max_count 条消息"""
        total = 0
        for _ in range(max_count):
            processed = self.process_one()
            if processed == 0:
                break
            total += processed
        return total

    # ========== 查询接口 ==========
    def pending_count(self) -> int:
        """待处理消息数"""
        with self._lock:
            return len(self._message_queue)

    def subscription_count(self) -> int:
        """总订阅数"""
        total = sum(len(v) for v in self._subscriptions.values())
        total += sum(len(v) for v in self._module_subscriptions.values())
        return total

    # ========== 辅助 ==========
    def _log_error(self, error_type: str, detail: str):
        self._pending_logs.append({
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": error_type,
            "source_module": "MemoryBus",
            "details": {"error": detail},
            "timestamp": time.time()
        })

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs


# ========== 演示与测试 ==========
def demo_main():
    print("=" * 60)
    print("  MemoryBus 全局记忆总线 演示")
    print("=" * 60)

    bus = MemoryBus()
    received_messages = []

    # 订阅
    def handler(msg):
        received_messages.append(msg)
    bus.subscribe("ag-mem-01.experience_query", handler)

    # 发布
    bus.publish("ag-mem-01.experience_query", "ag-ecc-05", {"user_id": "U001"})
    bus.publish_to_module("ag-mem-02", "slot_query", "ag-mem-01", {"user_id": "U001"})

    # 处理
    processed = bus.process_all()
    print(f"  待处理: {bus.pending_count()}")
    print(f"  已处理: {processed}")
    print(f"  收到消息: {len(received_messages)}")

    print("\n✅ MemoryBus 演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("MemoryBus 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        # TC-BUS-01: 基本发布订阅
        print("\n[TC-BUS-01] 基本发布订阅")
        try:
            bus = MemoryBus()
            received = []
            bus.subscribe("test.topic", lambda msg: received.append(msg))
            bus.publish("test.topic", "source", {"key": "value"})
            bus.process_all()
            assert len(received) == 1
            assert received[0].data == {"key": "value"}
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-BUS-02: 点对点路由
        print("\n[TC-BUS-02] 点对点路由")
        try:
            bus = MemoryBus()
            received = []
            bus.subscribe_to_module("target_module", lambda msg: received.append(msg))
            bus.publish_to_module("target_module", "event", "source", {"data": 1})
            bus.process_all()
            assert len(received) == 1
            assert received[0].target_module == "target_module"
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-BUS-03: 多个订阅者
        print("\n[TC-BUS-03] 多个订阅者")
        try:
            bus = MemoryBus()
            r1, r2 = [], []
            bus.subscribe("multi.topic", lambda msg: r1.append(msg))
            bus.subscribe("multi.topic", lambda msg: r2.append(msg))
            bus.publish("multi.topic", "source", "data")
            bus.process_all()
            assert len(r1) == 1 and len(r2) == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-BUS-04: 批量处理
        print("\n[TC-BUS-04] 批量处理")
        try:
            bus = MemoryBus()
            received = []
            bus.subscribe("batch.topic", lambda msg: received.append(msg))
            for i in range(100):
                bus.publish("batch.topic", "source", {"index": i})
            processed = bus.process_all()
            assert processed == 100
            assert len(received) == 100
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-BUS-05: 无订阅者不报错
        print("\n[TC-BUS-05] 无订阅者不报错")
        try:
            bus = MemoryBus()
            bus.publish("no_subscribers", "source", "data")
            processed = bus.process_all()
            assert processed == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-BUS-06: 线程安全
        print("\n[TC-BUS-06] 线程安全")
        try:
            bus = MemoryBus()
            received = []
            bus.subscribe("thread.topic", lambda msg: received.append(msg))

            def publisher():
                for _ in range(50):
                    bus.publish("thread.topic", "source", "data")

            threads = [threading.Thread(target=publisher) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            bus.process_all()
            assert len(received) == 200
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