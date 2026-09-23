"""主管运行依赖容器；服务与客户端不写入检查点状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.base import DecisionProvider
    from app.agent.registry import AgentRegistry
    from app.services.amap_service import AmapService
    from app.services.llm_service import LLMService
    from app.services.trip_service import TripService
    from app.services.validation_service import ValidationService
    from app.tools.registry import ToolRegistry


@dataclass(slots=True)
class AutonomousGraphContext:
    """自主决策 Graph 的运行依赖，None 表示使用构图时的默认依赖。

    资源由调用方拥有并释放；这里不持有某次数据库工作的 Session，
    TripService 通过会话工厂为每次加载单独创建和关闭短会话。
    """

    trip_service: TripService | None = None  # 旅行读取和保存边界；图不操作 Repository。
    llm: LLMService | None = None  # 模型服务；缺省时使用离线规则。
    amap: AmapService | None = None  # 外部通信服务；仅由需要的工具使用。
    validator: ValidationService | None = None  # 确定性行程校验服务。
    supervisor: DecisionProvider | None = None  # 可注入生产或测试决策器。
    agent_registry: AgentRegistry | None = None  # 本次可用的专业 Agent。
    tool_registry: ToolRegistry | None = None  # 持有双向权限和调用预算账本。
