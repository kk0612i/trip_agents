"""LLM 能力的应用层接口。

这里只定义工作流需要的输入输出，不在服务层放模型客户端或临时返回值。
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from app.agents.prompts import (
    BUILD_ITINERARY_SYSTEM_PROMPT,
    BUILD_ITINERARY_USER_PROMPT,
    PARSE_REQUEST_SYSTEM_PROMPT,
    PARSE_REQUEST_USER_PROMPT,
    REVISE_ITINERARY_SYSTEM_PROMPT,
    REVISE_ITINERARY_USER_PROMPT,
)
from app.core.logger import logger
from app.models.schemas import (
    Itinerary,
    ParsedTripRequest,
    PlaceCandidate,
    TripChangeRequest,
    TripRequest,
    ValidationResult,
)

StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class LLMServiceError(RuntimeError):
    """LLM 服务无法完成当前业务操作。"""


class LLMInvocationError(LLMServiceError):
    """模型调用失败，例如网络异常、超时或上游服务不可用。"""


class LLMOutputParseError(LLMServiceError):
    """模型响应无法解析为业务所需的结构化数据。"""


class LLMService:
    """封装请求解析、行程生成、修改和校验修复。"""

    def __init__(self, llm):
        self.llm = llm

    async def parse_request(
        self,
        user_message: str,
        current_itinerary: Itinerary | None,
    ) -> ParsedTripRequest:
        return await self._invoke_structured(
            output_type=ParsedTripRequest,
            system_prompt=PARSE_REQUEST_SYSTEM_PROMPT,
            user_prompt=PARSE_REQUEST_USER_PROMPT,
            inputs={
                "has_current_itinerary": current_itinerary is not None,
                "current_itinerary": current_itinerary,
                "user_message": user_message,
            },
            output_name="旅行请求",
            operation="解析旅行请求",
        )

    async def build_itinerary(
        self,
        trip_request: TripRequest,
        candidate_places: list[PlaceCandidate],
    ) -> Itinerary:
        return await self._invoke_structured(
            output_type=Itinerary,
            system_prompt=BUILD_ITINERARY_SYSTEM_PROMPT,
            user_prompt=BUILD_ITINERARY_USER_PROMPT,
            inputs={
                "trip_request": trip_request,
                "candidate_places": candidate_places,
            },
            output_name="行程",
            operation="生成行程",
        )

    async def revise_itinerary(
        self,
        current_itinerary: Itinerary,
        change_request: TripChangeRequest,
        candidate_places: list[PlaceCandidate],
    ) -> Itinerary:
        return await self._invoke_structured(
            output_type=Itinerary,
            system_prompt=REVISE_ITINERARY_SYSTEM_PROMPT,
            user_prompt=REVISE_ITINERARY_USER_PROMPT,
            inputs={
                "current_itinerary": current_itinerary,
                "change_request": change_request,
                "candidate_places": candidate_places,
            },
            output_name="修改后行程",
            operation="修改行程",
        )

    async def repair_itinerary(
        self,
        draft_itinerary: Itinerary,
        validation_result: ValidationResult,
    ) -> Itinerary:
        pass

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
        logger.info("LLM 操作开始: {}", operation)
        try:
            result = await chain.ainvoke(inputs)
        except OutputParserException as exc:
            logger.warning(
                "LLM 输出解析失败: {}, 耗时 {:.3f}s",
                operation, time.perf_counter() - started_at,
            )
            raise LLMOutputParseError(
                f"LLM 返回的{output_name}格式不正确"
            ) from exc
        except Exception as exc:
            logger.error(
                "LLM 调用失败: {}, 类型={}, 耗时 {:.3f}s",
                operation, type(exc).__name__, time.perf_counter() - started_at,
            )
            raise LLMInvocationError(f"调用 LLM {operation}失败") from exc
        logger.info("LLM 操作完成: {}, 耗时 {:.3f}s", operation, time.perf_counter() - started_at)
        return result
