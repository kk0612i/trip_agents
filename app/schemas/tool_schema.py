"""应用级工具的输入模型。"""

from typing import Annotated, Literal

from langchain_core.tools import InjectedToolArg
from pydantic import BaseModel, ConfigDict, Field


# 工具参数公共配置；类说明使用注释，避免改变既有工具模型 JSON Schema。
class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    runtime: Annotated[object, InjectedToolArg] = Field(exclude=True)  # 框架注入的可信上下文，模型不可提供。


# 景点搜索输入；城市最终由当前旅行需求覆盖，模型不能扩展授权范围。
class SearchAttractionsArguments(ToolArguments):
    keyword: str = Field(min_length=1)
    city: str | None = None
    limit: int = Field(default=10, ge=1, le=25)


# 地点详情输入，只允许查询本轮已经取得证据的 POI。
class GetPlaceDetailArguments(ToolArguments):
    place_id: str = Field(min_length=1)


# 路线端点引用；坐标仅由工具边界绑定的地点证据或外部服务补充。
class PlaceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    place_id: str | None = None  # 来源地点编号；缺失时服务按名称与地址定位。
    name: str = Field(min_length=1)
    address: str | None = None
    longitude: float | None = Field(default=None, ge=-180, le=180)  # 经度，单位为角度。
    latitude: float | None = Field(default=None, ge=-90, le=90)  # 纬度，单位为角度。


# 两个可信地点之间的路线查询参数。
class CalculateRouteArguments(ToolArguments):
    origin: PlaceReference
    destination: PlaceReference
    mode: Literal["walking", "driving"] = "walking"


# 普通费用函数接收的单项金额，不以零替代未知价格。
class CostItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    category: Literal["transportation", "tickets", "meals", "accommodation", "other"]
    amount: float | None = Field(default=None, ge=0)  # 金额单位为人民币；None 表示未知。
    description: str = ""


class EstimateItineraryCostArguments(ToolArguments):
    """费用工具的可选显式输入；未传入时读取当前草稿和旅行需求。"""

    items: list[CostItem] = Field(default_factory=list)
    budget: float | None = Field(default=None, ge=0)
    currency: Literal["CNY"] = "CNY"


# 住宿 POI 搜索参数；结果不代表已验证房价、库存或可预订性。
class SearchAccommodationArguments(ToolArguments):
    keyword: str = Field(default="住宿", min_length=1)
    city: str | None = None
    limit: int = Field(default=10, ge=1, le=25)


# 天气查询输入；实际查询城市由当前旅行需求确定。
class GetWeatherForecastArguments(ToolArguments):
    city: str | None = None
