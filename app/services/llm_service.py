"""LLM 能力的应用层接口。

这里只定义工作流需要的输入输出，不在服务层放模型客户端或临时返回值。
"""

from __future__ import annotations

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
        pass

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
