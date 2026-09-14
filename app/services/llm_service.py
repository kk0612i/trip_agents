"""LLM 能力的应用层接口。

这里只定义工作流需要的输入输出，不在服务层放模型客户端或临时返回值。
"""

from __future__ import annotations

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.agents.prompts import PARSE_REQUEST_SYSTEM_PROMPT, PARSE_REQUEST_USER_PROMPT
from app.models.schemas import (
    Itinerary,
    ParsedTripRequest,
    PlaceCandidate,
    TripChangeRequest,
    TripRequest,
    ValidationResult,
)


class LLMService:
    """封装请求解析、行程生成、修改和校验修复。"""

    def __init__(self, llm):
        self.llm = llm

    async def parse_request(
        self,
        user_message: str,
        current_itinerary: Itinerary | None,
    ) -> ParsedTripRequest:
        has_current_itinerary = False
        # 存在已有行程
        if current_itinerary is not None:
            has_current_itinerary = True

        parser = PydanticOutputParser(pydantic_object=ParsedTripRequest)
        # 构建提示词
        prompt = ChatPromptTemplate.from_messages([
            ("system", PARSE_REQUEST_SYSTEM_PROMPT),
            ("human", PARSE_REQUEST_USER_PROMPT)
        ]).partial(
            format_instructions=parser.get_format_instructions()
        )
        # 构建执行链
        chain = prompt | self.llm | parser

        # 当前方法是异步接口，使用 ainvoke 避免同步请求阻塞事件循环。
        result = await chain.ainvoke({
            "has_current_itinerary": has_current_itinerary,
            "current_itinerary": current_itinerary,
            "user_message": user_message,
        })

        return result

    async def build_itinerary(
        self,
        trip_request: TripRequest,
        candidate_places: list[PlaceCandidate],
    ) -> Itinerary:
        pass

    async def revise_itinerary(
        self,
        current_itinerary: Itinerary,
        change_request: TripChangeRequest,
        candidate_places: list[PlaceCandidate],
    ) -> Itinerary:
        pass

    async def repair_itinerary(
        self,
        draft_itinerary: Itinerary,
        validation_result: ValidationResult,
    ) -> Itinerary:
        pass
