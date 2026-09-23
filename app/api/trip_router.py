"""旅行和版本查询骨架；不把内部无归属查询直接暴露为 HTTP 接口。"""

from typing import Annotated
from fastapi import APIRouter, Depends, Path

from app.api.deps import get_trip_service, page_query
from app.schemas.api_schema import ErrorResponse, ItineraryVersion, Page, PageQuery, TripId, TripView, VersionSummary
from app.services.trip_service import TripService

router = APIRouter(responses={501: {"model": ErrorResponse, "description": "能力尚未实现"}})
TripDep = Annotated[TripService, Depends(get_trip_service)]
PageDep = Annotated[PageQuery, Depends(page_query)]


@router.get("/{trip_id}", response_model=TripView)
async def get_trip(trip_id: TripId, service: TripDep) -> TripView:
    """查询旅行待实现；不会调用内部 load_current 绕过鉴权。

    Args:
        trip_id: 旅行编号；公开入口使用正整数十进制字符串，内部读取使用整数。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
    """
    return await service.get_trip(trip_id)


@router.get("/{trip_id}/versions", response_model=Page[VersionSummary])
async def list_versions(trip_id: TripId, service: TripDep, query: PageDep) -> Page[VersionSummary]:
    """版本摘要分页待实现；合法请求统一返回 501。

    Args:
        trip_id: 旅行编号；公开入口使用正整数十进制字符串，内部读取使用整数。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
        query: 已解析的页长与不透明游标；游标的数据语义尚待存储接入。
    """
    return await service.list_versions(trip_id, query)


@router.get("/{trip_id}/versions/{version_no}", response_model=ItineraryVersion)
async def get_version(trip_id: TripId, version_no: Annotated[int, Path(ge=1, le=2**32-1)],
                      service: TripDep) -> ItineraryVersion:
    """读取不可变版本待实现；版本序号必须为正整数。

    Args:
        trip_id: 旅行编号；公开入口使用正整数十进制字符串，内部读取使用整数。
        version_no: 从 1 开始的业务版本序号，不是版本表主键。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
    """
    return await service.get_version(trip_id, version_no)
