from __future__ import annotations

from dataclasses import dataclass

from app.repository.trip_repository import TripRepository
from app.services.amap_service import AmapService
from app.services.llm_service import LLMService
from app.services.validation_service import ValidationService


@dataclass(slots=True)
class TripGraphContext:
    """Graph运行时依赖。"""

    repository: TripRepository
    llm: LLMService
    amap: AmapService
    validator: ValidationService
