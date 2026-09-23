"""单次系统动作的工具上下文；不存入 Graph State 或共享工具对象。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.agent.supervisor.supervisor_state import AgentRunState
    from app.services.amap_service import AmapService
    from app.tools.registry import ToolRegistry


class AgentPermissions(Protocol):
    """工具执行层查询 Agent 权限的最小接口。"""

    def allowed_tools(self, name: str) -> frozenset[str]:
        """查询指定 Agent 的工具权限。

        Args:
            name: 已注册的专业任务名称。

        Returns:
            Agent 侧不可变权限集合，工具侧白名单仍需单独检查。
        """
        ...


@dataclass
class ToolContext:
    """运行作用域内的可信依赖和执行账本，由 ToolRegistry.scope 负责失效。

    Attributes:
        registry: 创建当前作用域的注册表，用于验证上下文身份。
        agent_name: 当前调用者在 Agent 注册表中的稳定名称。
        agents: 提供 Agent 侧权限集合的只读接口。
        state: 调用开始时深拷贝的状态，不写回主管图。
        remaining: 当前作用域允许的调用总上限，包含 calls 已消费次数。
        amap: 外部注入的高德服务；本上下文不创建或关闭其资源。
        calls: 已接受的调用数，执行失败也计入额度。
        traces: 只属于当前作用域的调用账本，保留拒绝和失败原因。
        violation: 首次权限或参数违规；非空时后续排队调用拒绝执行。
        active: 退出作用域后置为 False，阻止持有旧上下文的工具继续执行。
    """

    registry: ToolRegistry
    agent_name: str
    agents: AgentPermissions
    state: AgentRunState
    remaining: int
    amap: AmapService | None = None
    # 只在当前 Agent 作用域存放搜索事实，作用域结束即丢弃，避免跨运行复用 POI 证据。
    place_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: int = 0
    traces: list[dict[str, Any]] = field(default_factory=list)
    violation: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # 串行化本作用域的额度消费与账本写入。
    active: bool = True

    def remember_places(self, places: list[dict[str, Any]]) -> None:
        """记录本轮搜索事实，供详情工具复核 POI 来源。

        Args:
            places: 已规范化的高德候选；缺少编号的记录忽略。
        """
        for place in places:
            place_id = place.get("place_id")
            if place_id:
                self.place_evidence[str(place_id)] = dict(place)
