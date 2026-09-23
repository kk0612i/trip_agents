"""复用已注入的模型，提供需求解析和 Supervisor 结构化决策。"""

from __future__ import annotations

import time
from typing import Any, TypeVar

from langchain_core.runnables import Runnable
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from app.prompts.requirement_prompt import MINIMAL_PARSE_REQUEST_SYSTEM_PROMPT, MINIMAL_PARSE_REQUEST_USER_PROMPT
from app.prompts.supervisor_prompt import SUPERVISOR_SYSTEM_PROMPT
from app.core.log import log_event, safe_log_identifier
from app.schemas.trip_schema import Itinerary, TripRequest
from app.schemas.requirement_schema import MinimalParsedRequest
from app.schemas.agent_schema import SupervisorDecision

StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class LLMServiceError(RuntimeError):
    """LLM 服务无法完成当前业务操作。"""


class LLMInvocationError(LLMServiceError):
    """模型调用失败，例如网络异常、超时或上游服务不可用。"""


class LLMOutputParseError(LLMServiceError):
    """模型响应无法解析为业务所需的结构化数据。"""


class LLMService:
    """封装需求解析和 Supervisor 决策的模型调用与输出校验。"""

    def __init__(self, llm: Runnable) -> None:
        """接收调用方拥有的模型，不负责创建或关闭连接。"""
        self.llm = llm

    async def parse_minimal_request(
        self,
        user_message: str,
        previous_request: TripRequest | None = None,
        previous_intent: str | None = None,
        *,
        instruction: str = "",
        current_itinerary: Itinerary | None = None,
    ) -> MinimalParsedRequest:
        """解析意图和需求补丁；由运行器合并并检查必填信息。"""

        return await self._invoke_structured(
            output_type=MinimalParsedRequest,
            system_prompt=MINIMAL_PARSE_REQUEST_SYSTEM_PROMPT,
            user_prompt=MINIMAL_PARSE_REQUEST_USER_PROMPT,
            inputs={"previous_request": previous_request.model_dump_json() if previous_request else "null",
                    "previous_intent": previous_intent,
                    "instruction": instruction,
                    "current_itinerary": current_itinerary.model_dump_json() if current_itinerary else "null",
                    "user_message": user_message},
            output_name="最小切片旅行请求",
            operation="解析最小切片旅行请求",
        )

    async def supervisor_decide(
        self,
        state: dict[str, Any],
        agents: list[dict[str, Any]],
    ) -> SupervisorDecision:
        """让模型只生成经过 Pydantic 校验的下一步系统动作。"""

        return await self._invoke_structured(
            output_type=SupervisorDecision,
            system_prompt=SUPERVISOR_SYSTEM_PROMPT,
            user_prompt=(
                "当前运行状态：{state}\n可用 Agent：{agents}\n"
                "请只决定下一步动作。"
            ),
            inputs={"state": state, "agents": agents},
            output_name="Supervisor 决策",
            operation="生成 Supervisor 决策",
        )

    async def _invoke_structured(
        self,
        *,
        output_type: type[StructuredOutput],
        system_prompt: str,
        user_prompt: str,
        inputs: dict[str, Any],
        output_name: str,
        operation: str,
    ) -> StructuredOutput:
        parser = PydanticOutputParser(pydantic_object=output_type)
        prompt = ChatPromptTemplate.from_messages(
            [("system", system_prompt), ("human", user_prompt)]
        ).partial(format_instructions=parser.get_format_instructions())
        chain = prompt | self.llm | parser

        started_at = time.perf_counter()
        # 这里只能观察一次应用调用；SDK 内部重试与 token 用量未知时不伪造数值。
        fields = {"operation": operation, "model": safe_log_identifier(getattr(self.llm, "model_name", None)), "attempt": 1}
        timeout = getattr(self.llm, "request_timeout", None)
        if isinstance(timeout, (int, float)):
            fields["timeout_seconds"] = timeout
        log_event("llm_invocation_started", level="DEBUG", **fields, status="running")
        try:
            result = await chain.ainvoke(inputs)
        except OutputParserException as exc:
            log_event("llm_invocation_completed", level="WARNING", **fields, status="failed",
                      error_type=type(exc).__name__, duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
            raise LLMOutputParseError(
                f"LLM 返回的{output_name}格式不正确"
            ) from exc
        except Exception as exc:
            log_event("llm_invocation_completed", level="ERROR", **fields, status="failed",
                      error_type=type(exc).__name__, duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
            raise LLMInvocationError(f"调用 LLM {operation}失败") from exc
        log_event("llm_invocation_completed", **fields, status="completed",
                  duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
        return result
