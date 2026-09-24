# trip_agents 接口契约 V1

2026-09-24 会话创建接入更新：`POST /api/v1/sessions` 已接入认证身份和事务写入，成功返回 201 及 `Location`。空请求只创建会话；关联旅行时复制当前正式版本需求，旅行不存在或属于他人返回 404 `TRIP_NOT_FOUND`，无有效当前版本返回 409 `TRIP_HAS_NO_VERSION`。已添加离线数据库适配及 HTTP 测试，尚未验证真实 MySQL。下文关于会话创建未实现的历史说明以本段为准。

2026-09-24 会话列表接入更新：`GET /api/v1/sessions` 已实现登录用户隔离、签名游标分页和摘要查询，成功返回 200。游标在 `SessionService` 中解码，绑定用户与 `sessions` 资源；非法或篡改游标返回 422。以下阶段一至三的“全部返回 501”等描述是历史快照，不代表当前会话列表状态。此次未接入前端列表，也未实现会话创建、详情和运行业务。

状态：数据库实体与公开 API 模型已定义，阶段三完成离线验收和日志接入，HTTP 骨架已注册，真实业务接入待实现。依据：2026-09-23 当前工作区代码。本文约定前端 HTTP/SSE API 及其到 Graph、Repository 的映射，不代表这些端点已可用。数据库字段与关系见 [DATABASE_MODEL.md](DATABASE_MODEL.md)。

重构阶段一已完成公共路径迁移，详见 [阶段一交付说明](REFACTOR_PHASE1.md)。
会话、运行、旅行及 SSE 路由已注册，合法请求返回 **501 / CAPABILITY_UNAVAILABLE**；SSE 在建流前返回 JSON 错误。详见 [阶段二交付说明](REFACTOR_PHASE2.md)。
正式身份依赖、用户隔离、事务、调度和推送均未实现；以下成功响应与鉴权要求仍是后续目标契约。
认证已移除内存用户与临时签名密钥。`auth_schema` 复用正式 `api_schema`，注册登录合法请求返回 501；数据库 Repository、异步服务和延迟会话工厂已留好边界，但 SQL、密码业务及令牌校验仍未实现。

阶段三验证与日志边界见 [最终验收汇报](REFACTOR_PHASE3.md)。请求日志只记录路由模板、请求编号、状态码与响应头耗时，不记录请求体、认证头、完整查询字符串或内部 Agent 推理。

## 1. 当前能力与交付边界

| 已核对的代码 | 当前事实 | 对契约的影响 |
| --- | --- | --- |
| `app/main.py`、`app/api/*_router.py` | 已有 FastAPI 入口；注册登录已替换为数据库依赖骨架，所有业务路由合法请求返回 501 | 认证及其他业务均尚未接入真实存储与鉴权 |
| `app/agent/supervisor/supervisor_graph.py` | `build_autonomous_graph()` 是 Graph 执行入口 | API 通过应用服务调用此入口 |
| `app/agent/supervisor/runtime.py` | 支持多轮需求、追问、运行限额、校验与保存的调用规则 | 复用业务状态转换，不把整个 State 暴露给客户端 |
| `app/agent/place_search/place_search_graph.py` | 已有景点搜索实现，需要模型与地图服务依赖 | 首个可接入场景为需求补全和景点搜索 |
| `app/agent/planner/planner_graph.py`、`app/agent/registry.py` | Planner 可新建或修改草稿，查询相邻路线并汇总已知费用；住宿、知识、天气 Agent 为桩 | 草稿仍需校验、保存；未知费用不代表免费，不承诺住宿推荐、知识问答、天气分析已可用 |
| `app/repository/trip_repository.py` | `load_current()` 返回版本号与行程；`save_version()` 未实现 | 旅行查询和版本保存需要补齐持久化实现 |
| `app/models/` | 已定义用户、会话、运行、事件、旅行、版本六张表，版本包含路线与来源运行 | 建表 SQL 已导出，尚未建库或验证真实数据库事务 |
| `app/schemas/api_schema.py` | 已定义独立公开 DTO、请求校验、摘要及 SSE 事件匹配 | 已绑定占位业务路由及 501 错误；现有认证继续使用独立 Schema，Graph 行为不变 |

V1 面向单实例 MVP，包含邮箱注册、登录和用户数据隔离，不包含预订支付、角色权限、刷新令牌或分布式任务调度。只有登录注册无需身份；其余接口统一要求 `Authorization: Bearer <access_token>`，包括 SSE。用户编号只能来自服务端验证后的身份，不接受客户端指定所有者。旅行与会话必须属于同一用户；运行和事件通过会话判断归属，版本通过旅行判断归属。他人资源与不存在资源均返回对应 404。

## 2. 资源模型与通用约定

- **Session（会话）**：承接多轮需求；最多关联一个旅行。回答追问沿用会话，新旅行使用新会话。
- **Run（运行）**：处理一条用户消息。一次运行可能产生追问、搜索结果、已保存行程或失败结果。
- **Trip（旅行）**：已保存的业务对象，在首次成功保存版本时创建。创建会话不创建空旅行。
- **ItineraryVersion（行程版本）**：某次成功保存的不可变快照，包括需求、行程、路线和校验结果。

基础路径为 `/api/v1`，JSON 使用 UTF-8 与 `snake_case`。普通成功响应直接返回资源对象；列表统一返回 `items`、`next_cursor`。不再嵌套 `code=200`。

| 项目 | 约定 |
| --- | --- |
| 用户 `id`、`session_id`、`run_id`、`client_request_id` | UUID 字符串，模型输出规范的带连字符形式；客户端仅生成逻辑提交编号 |
| `trip_id` | 正整数的十进制字符串，如 `"42"`；避免数据库 BIGINT 超出 JavaScript 安全整数范围 |
| `version_no` | 大于等于 1 的 JSON 整数；它是版本序号，不是版本表主键 |
| 时间戳 | UTC RFC 3339，例如 `2026-09-20T10:00:00Z` |
| 旅行日期、每日时间 | 日期 `YYYY-MM-DD`，时间 `HH:mm:ss`；V1 中国境内行程按 `Asia/Shanghai` 解释 |
| 距离、时长、货币 | 公里、整数分钟、人民币 CNY；金额为有限非负 JSON number，最多两位小数，上限 9999999999.99，与 DECIMAL(12,2) 一致 |
| 空值 | 未知或尚未产生为 `null`，已执行但结果为空为 `[]`；不能用 0 表示未知价格 |
| 请求校验 | 拒绝未声明字段；不把字符串数字或布尔值宽松转换成数字；字符串去除首尾空白后校验 |
| 关联排查 | 每个 HTTP 请求返回 `X-Request-Id`，错误体包含相同的 `request_id`；它不同于运行编号 |
| 缓存 | 会话、运行、事件响应使用 `Cache-Control: no-store` |

成功响应可以增加可选字段。移除字段、改变字段类型或语义、增加客户端必须处理的终态属于破坏性变更，应升级主版本。

## 3. 路由清单

| 方法 | 路径 | 成功状态 | 用途 |
| --- | --- | --- | --- |
| POST | `/auth/register` | 201 | 邮箱注册并取得令牌 |
| POST | `/auth/login` | 200 | 登录并取得令牌 |
| POST | `/sessions` | 201 | 创建会话，可关联已有旅行 |
| GET | `/sessions` | 200 | 当前用户的会话摘要列表 |
| GET | `/sessions/{session_id}` | 200 | 查询需求、待回答问题、运行和旅行引用 |
| POST | `/sessions/{session_id}/runs` | 202；幂等重放为 200 | 提交一条消息，启动一次运行 |
| GET | `/sessions/{session_id}/runs` | 200 | 分页查询消息与运行历史 |
| GET | `/runs/{run_id}` | 200 | 查询运行当前状态及结果 |
| GET | `/runs/{run_id}/events` | 200，SSE | 订阅或重放执行进度 |
| GET | `/trips/{trip_id}` | 200 | 查询旅行与当前正式版本 |
| GET | `/trips/{trip_id}/versions` | 200 | 分页查询版本摘要 |
| GET | `/trips/{trip_id}/versions/{version_no}` | 200 | 查询某个不可变版本 |

列表参数均为 `limit`（默认 20，范围 1～100）和可选 `cursor`。会话按 `(created_at, session_id)` 倒序，运行按服务端内部自增提交序号倒序，版本按 `version_no` 倒序。游标为不透明字符串，绑定用户和查询资源；无后续结果时 `next_cursor=null`，非法游标返回 422。分页后新增的记录不会插入旧游标后方。`PageQuery` 校验已解析参数，HTTP 适配层需把合法十进制 limit 文本解析为整数，不能直接把原始 query 字符串传入严格整数模型。

### 3.1 注册与登录

两者请求均为 `Credentials`：

```json
{"email": "user@example.com", "password": "example-password"}
```

邮箱去首尾空白并转小写，最长 254 字符；密码 8～128 字符，**不去除密码空白**。拒绝额外字段。成功返回 `AuthResponse`：

```json
{
  "access_token": "server-issued-token",
  "token_type": "bearer",
  "user": {"id": "550e8400-e29b-41d4-a716-446655440020", "email": "user@example.com"}
}
```

邮箱重复返回 409 `EMAIL_EXISTS`；账号或密码错误返回 401 `AUTH_INVALID`；受保护接口缺少、无效或过期令牌返回 401 `AUTH_REQUIRED`。不回传密码、盐或哈希。后续认证接入沿用既定的 240000 次 PBKDF2-SHA256、16 字节随机盐、32 字节哈希；签名密钥须由服务端持久配置提供，不能继续每次进程启动随机生成。令牌时效沿用 7 天，本版不提供刷新或服务端撤销接口。

当前认证路由已接入本 DTO 和统一错误结构，但数据库读写与认证业务尚未实现，合法请求返回 501，不签发令牌。上述 201/200/401/409 为后续业务目标。

### 3.2 会话列表

`GET /sessions?limit=20` 返回 `Page<SessionSummary>`：

```json
{
  "items": [{
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "长沙 3 日游",
    "trip_id": "42",
    "current_version_no": 1,
    "created_at": "2026-09-22T02:00:00Z",
    "updated_at": "2026-09-22T02:05:00Z"
  }],
  "next_cursor": null
}
```

标题不入库：已绑定旅行使用当前正式版本需求，否则使用会话需求；有目的地和天数时为“长沙 3 日游”，只有目的地时为“长沙之旅”，否则为“新旅行”。`current_version_no` 从旅行当前版本读取。前端目前仍用 localStorage 导航索引，此次只添加类型，后续再替换列表请求与按用户隔离缓存。

后端列表已实现：请求首页不传 `cursor`，后续请求原样携带上次返回的 `next_cursor`；末页及空列表返回 `next_cursor=null`。分页按创建时间而非更新时间倒序，每页最多执行两条查询（会话分页、关联正式版本批量查询），不逐条加载旅行。MySQL 中保存的 UTC 无时区时间在响应中显式标为 UTC。签名使用持久配置 `AUTH_JWT_SECRET`，游标带独立签发来源与资源用途，不能作为登录令牌；密钥更换后旧游标失效，应重新请求首页。

## 4. 会话契约

### 4.1 创建会话

`POST /api/v1/sessions`，请求体可为 `{}`：

```json
{
  "trip_id": null
}
```

`trip_id` 省略或为 `null` 表示新旅行。提供 ID 时，从数据库加载当前版本及其需求快照；找不到旅行返回 404，旅行没有当前版本返回 409 `TRIP_HAS_NO_VERSION`。

201 响应（同时返回 `Location: /api/v1/sessions/{session_id}`）：

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "trip_id": null,
  "current_version_no": null,
  "trip_request": null,
  "latest_run_id": null,
  "active_run_id": null,
  "pending_question": null,
  "created_at": "2026-09-20T10:00:00Z",
  "updated_at": "2026-09-20T10:00:00Z"
}
```

GET 会话返回相同结构。`trip_request` 是最近已提交到会话的完整需求快照，`pending_question` 是最近一次运行产生且尚未被新消息回应的问题。`current_version_no` 从旅行当前版本读取；存在多个会话编辑同一旅行时，它可能高于此会话上次生成的版本。

只有终态发布后才提交本轮会话状态：保存已成功解析的需求、待回答问题、旅行引用和最新运行引用；失败运行不覆盖先前已保存行程。已解析需求可在失败后保留，便于用户修正。运行终态与会话更新应在同一事务中发布。

### 4.2 多轮语义

第一轮“我想去长沙”可能返回 `needs_input`，提示补充天数；第二轮提交“三天，想看博物馆”，仍使用同一 `session_id`，但产生新 `run_id`。

`needs_input` 是该次运行的终态。补充回答启动新一轮 Graph 调用，不是恢复同一个暂停中的运行。客户端只发送新消息，不上传上一轮 State、校验证明或计数。

会话建立后，客户端不能通过消息请求切换 `trip_id`。初次保存成功时，服务端把新旅行绑定到会话；以后对这份旅行的修改形成新版本。

## 5. 提交消息与幂等契约

`POST /api/v1/sessions/{session_id}/runs`：

```json
{
  "client_request_id": "550e8400-e29b-41d4-a716-446655440010",
  "message": "我想去长沙",
  "expected_version_no": null
}
```

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `client_request_id` | 是 | 一个逻辑提交固定使用同一个 UUID；网络重试沿用原值 |
| `message` | 是 | 去首尾空白后 1～4000 个 Unicode 字符 |
| `expected_version_no` | 条件必填 | 会话已绑定旅行时，必须等于客户端最近看到的当前版本号；新旅行省略或传 `null` |

服务端负责意图识别，不要求客户端选择 Agent 或填写内部 `intent`。请求不接受 `current_itinerary`、`draft_itinerary`、工具参数、运行预算、`steps` 或校验结果。

首次受理返回 202，并设置 `Location: /api/v1/runs/{run_id}`：

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440001",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "status_url": "/api/v1/runs/550e8400-e29b-41d4-a716-446655440001",
  "events_url": "/api/v1/runs/550e8400-e29b-41d4-a716-446655440001/events"
}
```

该响应为 `RunReceipt`。幂等重放返回同一结构和同一运行编号，HTTP 为 200，`status` 使用该运行当时的最新状态。

受理必须原子地完成以下检查与写入：

1. 验证身份与会话归属，再查询 `(session_id, client_request_id)`；幂等命中先于忙碌、版本和能力检查。
2. 同一键、同一规范化请求返回既有运行；同一键、不同请求返回 409 `IDEMPOTENCY_CONFLICT`。规范化请求包含去首尾空白后的消息，以及将缺省视为 `null` 的版本号。
3. 同一会话已有 `queued/running` 运行时，新的提交返回 409 `SESSION_BUSY`，错误详情携带 `active_run_id`。
4. 校验旅行基准版本；不一致返回 409 `VERSION_CONFLICT`，携带 `expected_version_no`、`current_version_no`。
5. 确认执行服务能接受任务，再持久化运行、消息、请求摘要和会话占用标记。随后才返回 202。

幂等记录与运行记录同生命周期。POST 响应丢失后重试不会再次调用 Graph。相同消息使用新 `client_request_id` 被视为新的业务请求。

## 6. 运行状态与结果

### 6.1 状态机

```text
queued → running → completed
                 → needs_input
                 → failed
queued → failed（启动失败或服务中断）
```

| 状态 | 准确含义 |
| --- | --- |
| `queued` | 已持久化受理，尚未开始 Graph 执行 |
| `running` | Graph 正在执行 |
| `needs_input` | 需要用户提供信息；本轮结束，`pending_question` 必须非空 |
| `completed` | 当前意图成功完成；创建/修改行程时还必须已校验并保存版本 |
| `failed` | 本轮未完成，`error` 必须非空；已提交的保存副作用仍应明确返回 |

终态一经发布不可更改。一次运行最多发布一个终态事件。新消息与用户主动重试均创建新运行，不重置既有运行。

### 6.2 RunView

`GET /api/v1/runs/{run_id}` 返回 `RunView`。下面是追问示例：

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440001",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "client_request_id": "550e8400-e29b-41d4-a716-446655440010",
  "message": "我想去长沙",
  "status": "needs_input",
  "intent": "create",
  "base_version_no": null,
  "response": "计划玩几天？",
  "pending_question": "计划玩几天？",
  "missing_fields": ["days"],
  "result": {
    "trip_request": {
      "destination": "长沙",
      "origin": null,
      "start_date": null,
      "days": null,
      "budget": null,
      "traveler_count": 1,
      "preferences": [],
      "constraints": [],
      "pace": "balanced"
    },
    "search": null,
    "itinerary": null,
    "routes": null,
    "validation": null,
    "saved_version": null
  },
  "error": null,
  "created_at": "2026-09-20T10:00:00Z",
  "started_at": "2026-09-20T10:00:01Z",
  "finished_at": "2026-09-20T10:00:03Z"
}
```

字段补充约束：

- `intent` 为 `create/revise/direct_search/knowledge/other/null`；解析前为 `null`。
- `base_version_no` 是本次实际加载的基准版本，不随着其他会话保存而变化。
- `queued/running` 时 `response`、`pending_question`、`finished_at`、`error` 为 `null`，`missing_fields=[]`。`result` 的字段均为 `null`，中间进度通过事件传递。
- 终态 `response` 为面向用户的文字；UI 根据结构化字段渲染卡片，不解析这段文字判断是否保存。
- `missing_fields` 使用需求字段名；询问已有行程时可使用 `trip_id`。开放式澄清可以没有缺失字段，不能据此忽略 `pending_question`。
- `result` 的六个字段固定存在；尚未产生对应结果时为 `null`。只投影通过结构校验的业务数据，不直接返回 `ActionResult.data` 任意字典。
- `saved_version` 非空时固定为 `{ "trip_id": "42", "version_no": 1 }`，并保证对应的版本查询已可读。
- `completed` 且意图为 `create/revise` 时，`saved_version`、`itinerary`、`routes`、`validation` 必须非空；返回内容与该保存版本一致，`validation.passed=true`。
- `completed` 且意图为 `direct_search` 时，`search` 必须非空。正常空搜索返回 `candidates=[]`，仍可完成；缺少依赖或外部查询失败不能伪装为空搜索。
- 保存已提交但后续收尾失败时，`failed` 仍返回已知的 `saved_version` 及对应版本内容，避免用户把失败理解为一定没有写入。

运行历史接口返回 `{items: RunView[], next_cursor: string|null}`；每条记录保留当时的输入、追问或回答，满足刷新后恢复聊天记录的需要。

### 6.3 公开业务 DTO

| DTO | 字段与约束 |
| --- | --- |
| `TripRequestDTO` | 字段见示例；`destination/origin/start_date/days/budget` 可空；`days` 为 1～14，`traveler_count>=1`；偏好、限制为字符串数组；`pace=relaxed/balanced/compact` |
| `SearchResultDTO` | `candidates`、`recommendations`、`data_sources`、`unmet_conditions`、`summary`、`limit_reached`；语义沿用 `PlaceSearchResult`，移除内部工具轨迹与调用计数 |
| `SearchCandidateDTO` | 沿用 `SearchPlaceCandidate`：地点 ID、名称、类别、地址、城市、经纬度、来源，以及价格/营业时间/室内属性；后三者在当前数据能力下固定为 `null` |
| `RecommendationDTO` | `place_id`、`reason`；ID 必须存在于同次返回的候选中 |
| `ItineraryDTO` | `summary`、`days`、`total_cost`、`currency`；每日包含 `day_index/date/items/total_cost/walking_distance_km/warnings`；项目包含现有 `ItineraryItem` 字段，见下方费用规则 |
| `RouteDTO` | `from_item_id/to_item_id/distance_km/duration_minutes/mode/provider`；路线端点必须引用该版本内的行程项目；V1 支持 `walking/driving` |
| `ValidationDTO` | 沿用 `ValidationResult`：`passed/issues/checked_at`；问题包含 `severity/code/message/day_index`，存在 error 时 `passed=false` |

`TripRequestDTO` 为完整快照，不能用作客户端可随意写入的 State。预算为整份行程所有出行人的总预算，项目费用也按该次全体出行人的预计合计解释，汇总时不再次乘人数。

需求补丁仍由 requirement Agent 产生。当前 `_agent_updates()` 使用 `exclude_none=True`：省略和 `null` 都表示保留旧值，`[]` 表示清空列表，`budget=0` 是有效更新。V1 不对客户端开放 PATCH 需求接口；若要支持“取消预算上限”等清除标量需求，须先增加明确的清除操作，不能把当前 `null` 宣称为清空。

公开 `ItineraryDTO` 的项目 `estimated_cost`、每日与全程 `total_cost` 允许 `null`；未知项目存在时对应汇总也为 `null`，DTO 已检查这一关系。有依据的预估值可以为数值，仍表示预估。本次不修改内部 Planner/Validator：当前内部汇总为数值、未知项目费用会阻止保存。后续开放未知费用行程前，应让内部模型支持空汇总、以 `budget_not_fully_verified` warning 表示预算未完全验证，已知小计超预算仍为 error；在完成业务改造前不得将未知费用当 0 或声称已支持保存该类行程。

公开候选字段明确为 `estimated_cost`，不是内部的 `ticket_price`；当前能力下价格、营业时间、室内属性均为 null。API 适配层需要显式映射，不得直接把内部 model_dump 展开到 DTO。`image_url/image_credit` 为可省略或 null 的显示字段。城市和经纬度必填，缺少有效坐标的内部候选不能直接成为公开地图候选；API 投影须明确过滤并留下缺失提示，不能补 0 坐标。`data_sources` 由已投影候选的 source 去重生成。

`ItineraryItem` 的其他字段为 `item_id/place_id/name/start_time/duration_minutes/travel_from_previous_minutes/address/notes`。天序号从 1 连续递增、数量等于需求天数；项目 ID 在整份行程内唯一；停留时间大于 0，交通时间和距离非负。首个地点交通时间为 0，同日相邻地点须有匹配路线；有出发日期时每日日期连续，否则为 `null`。经纬度必须是有限合法数值，使用高德的 GCJ-02 坐标，不得直接按 WGS84 渲染。以上是公开契约要求，当前 Validator 只覆盖部分规则，不能把现有 `passed` 当作全部已验证。

`knowledge` 当前没有完成态结果 DTO：该能力在 V1 返回 `CAPABILITY_UNAVAILABLE`；实现并确定来源引用结构后再扩展契约。住宿与天气也不发布占位成功结果。

## 7. SSE 事件契约

先 POST 创建运行，再 GET `events_url` 订阅。GET 只观察该运行，不启动或重复执行 Graph。客户端也可以只轮询 RunView。

订阅必须携带与普通请求相同的 Bearer 请求头，不允许把令牌拼入 URL。当前原生 `EventSource` 没有携带该请求头，接入鉴权前须改为支持自定义请求头的流式 fetch 客户端，并自行携带 `Last-Event-ID` 重连。本次不修改 SSE 客户端；现有订阅尚不能声称已支持受保护的服务。

响应使用 `Content-Type: text/event-stream; charset=utf-8`。每个业务事件以空行结束，`id` 格式为 `{run_id}:{seq}`，`seq` 为运行内从 1 开始递增的整数。

```text
id: 550e8400-e29b-41d4-a716-446655440001:2
event: progress
data: {"run_id":"550e8400-e29b-41d4-a716-446655440001","seq":2,"occurred_at":"2026-09-20T10:00:02Z","data":{"stage":"search","message":"正在查询候选景点"}}

```

所有事件 JSON 使用相同外壳：`run_id`、`seq`、`occurred_at`、`data`。

`RunEvent[T]` 对应此 JSON 外壳。`SSEEvent(event, envelope)` 仅是服务端校验包装：event 写入 SSE 的 event 行，envelope 序列化到 data 行，`event_id` 写入 id 行；不能把整个包装当作线上的 data。模型检查命名事件与 payload 类型、终态及运行编号匹配。

| event | data | 语义 |
| --- | --- | --- |
| `run.started` | `{status: "running"}` | 正式开始执行 |
| `progress` | `{stage, message}` | 阶段进度；stage 为 `understand/search/plan/validate/save` |
| `run.completed` | 完整 `RunView` | 成功终态 |
| `run.needs_input` | 完整 `RunView` | 追问终态 |
| `run.failed` | 完整 `RunView` | 失败终态，包括启动前失败 |

阶段事件由服务端从实际节点执行中投影，不要求固定顺序，允许重复出现。`message` 为受控的用户提示，不暴露 Supervisor 推理、Prompt、工具原始输入输出或密钥。V1 无 token 增量事件，最终文本从终态 RunView 获取。

每 15 秒可发送注释心跳 `: ping`，心跳没有编号。连接断开只终止订阅，运行继续；服务端不能把 Graph 生命周期绑定到 SSE HTTP 请求。

客户端重连传 `Last-Event-ID`，服务端重放其后事件。无此请求头则从第一个保留事件重放。事件按运行内顺序交付，可能重复，客户端按 `id` 去重。其他运行的 ID 或非法序号返回 422。

事件日志与运行记录同生命周期，不单独提前裁剪。已结束运行订阅时先补齐事件再关闭；客户端已消费终态后再次订阅可返回 204。若连接中断或终态事件未收到，GET RunView 是最终状态的权威查询入口。

状态更新与对应事件入库须原子提交，提交后才推送。先完成历史重放再衔接实时事件，不能漏掉这两个阶段之间产生的事件。

## 8. 旅行与版本读取

`GET /trips/{trip_id}` 返回：

```json
{
  "trip_id": "42",
  "current_version_no": 3,
  "current_version_url": "/api/v1/trips/42/versions/3",
  "created_at": "2026-09-20T10:00:00Z",
  "updated_at": "2026-09-20T10:20:00Z"
}
```

当前指针可能随后变化，客户端按响应里的版本 URL 读取同一不可变快照。版本详情固定包含：

| 字段 | 类型与含义 |
| --- | --- |
| `trip_id` | 十进制字符串 |
| `version_no` | 正整数 |
| `source_run_id` | 创建该版本的公开运行 UUID |
| `trip_request` | `TripRequestDTO`，保存当时的需求快照 |
| `itinerary` | `ItineraryDTO` |
| `routes` | `RouteDTO[]`，允许只有单点时为空数组 |
| `validation` | `ValidationDTO`，必须通过且不能包含 error |
| `created_at` | UTC 时间戳 |

版本列表的 `items` 只包含 `trip_id/version_no/source_run_id/summary/created_at`，其中 `summary` 来自行程。版本不存在返回 404 `VERSION_NOT_FOUND`。一次失败运行不产生虚假的空版本。

保存由 Runtime 的 `save` 动作进入 TripService，再由服务管理 Repository 工作单元；客户端通过发消息表达修改意图，不直接上传“已通过校验”的草稿绕过这条路径。

## 9. 错误契约

HTTP 拒绝响应统一为：

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440099",
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "行程已被更新，请刷新后重新提交。",
    "retryable": false,
    "details": {
      "expected_version_no": 2,
      "current_version_no": 3
    }
  }
}
```

`error` 四个字段固定存在，`details` 默认为 `{}`；参数错误可包含 `fields: [{field, message}]`。禁止直接返回上游响应正文、异常堆栈或服务地址中的密钥。

| HTTP | code | 触发条件 |
| --- | --- | --- |
| 400 | `INVALID_JSON` | JSON 语法错误 |
| 401 | `AUTH_REQUIRED` | 受保护接口的身份缺失、无效或过期 |
| 401 | `AUTH_INVALID` | 登录邮箱或密码错误 |
| 409 | `EMAIL_EXISTS` | 注册邮箱已存在 |
| 404 | `SESSION_NOT_FOUND/RUN_NOT_FOUND/TRIP_NOT_FOUND/VERSION_NOT_FOUND` | 对应资源不存在 |
| 409 | `SESSION_BUSY` | 同会话已有未结束运行 |
| 409 | `IDEMPOTENCY_CONFLICT` | 幂等键对应的请求内容不一致 |
| 409 | `VERSION_CONFLICT/TRIP_HAS_NO_VERSION` | 旅行版本前置条件不满足 |
| 422 | `INVALID_ARGUMENT` | 字段、游标、事件编号或范围不合法 |
| 429 | `RATE_LIMITED` | API 接入限流，附 `Retry-After` 秒数 |
| 501 | `CAPABILITY_UNAVAILABLE` | 已注册但尚未实现的业务能力；不受理运行、不建立事件流 |
| 503 | `SERVICE_UNAVAILABLE` | 执行服务暂时无法受理，尚未创建运行 |
| 500 | `INTERNAL_ERROR` | API 自身发生未预期异常 |

运行受理后再发生的错误写入 RunView.error，使用同一 Error 结构；GET 运行仍返回 200。即使业务失败，查询资源本身也是成功的。SSE 已建立后通过 `run.failed` 传达失败，不尝试改写 HTTP 状态。

| 运行错误 code | 含义 | retryable |
| --- | --- | --- |
| `CAPABILITY_UNAVAILABLE` | 请求的 Agent 未实现，或缺少必需的模型/地图/保存依赖 | false |
| `INTENT_UNSUPPORTED` | 不支持该意图 | false |
| `EXECUTION_LIMIT_EXCEEDED` | 达到循环、Agent 或工具预算 | false |
| `MODEL_OUTPUT_INVALID` | 模型输出未通过结构校验 | false |
| `UPSTREAM_UNAVAILABLE` | 外部模型或地图发生暂时性失败 | true |
| `VALIDATION_FAILED` | 行程确定性校验未通过 | false |
| `VERSION_CONFLICT` | 执行期间已有其他运行保存了新版本 | false |
| `PERSISTENCE_FAILED` | 已确认保存事务回滚 | true |
| `INTERNAL_ERROR` | 未预期的运行或结果投影异常 | false |
| `RUN_INTERRUPTED` | 进程中断，已核对保存结果并结束该次执行 | true |

`retryable=true` 表示排除已保存副作用后可以由用户发起新尝试，不代表服务端自动重试；如果 `saved_version` 非空，UI 应先展示该版本，而非直接再次生成。提交请求超时应先使用原 `client_request_id` 重放确认受理结果，不能直接生成新键。

当前 Runtime 主要返回文字错误，还没有上述稳定错误码。实现时要在错误产生处保留类型或 code，通过白名单映射到公开错误；不要靠匹配中文错误字符串判断类别。

## 10. 一致性、保存与服务中断

### 10.1 三种不同的去重边界

1. **消息受理去重**：`UNIQUE(session_id, client_request_id)` 防止网络重试触发重复运行。
2. **同会话串行**：会话行锁或原子占用保证最多一个 queued/running，跨进程也有效。
3. **版本写入去重**：`UNIQUE(source_run_id)` 保证一次公开运行最多保存一个版本；重入返回同一版本。同一运行请求保存不同内容时明确拒绝。

现有 `saved_fingerprint` 只防止一次 Graph 运行中重复保存，不能替代数据库去重。

### 10.2 保存事务

新旅行在同一事务内创建 Trip、插入 v1、更新当前版本指针，并记录 `source_run_id`。修改旅行则锁定旅行行，比较 `expected_version_no`，插入下一版本，再更新当前指针。

版本必须保存需求、行程、路线、校验结果，以及用于验证重复写入是否一致的内容摘要；保留 `(trip_id, version_no)` 唯一约束。比较版本与写入必须处于同一事务，不能先比较、等模型执行完后无条件覆盖。

模型及地图调用在事务外进行；仅在保存阶段开启短事务。事务提交前不得发送保存成功或完成事件。Repository 返回的旅行编号和版本号必须指向已提交、可查询的数据。

运行、会话和已保存版本之间必须可以通过 `source_run_id` 对账。无法确认保存是否提交时，不先发布不可修改的失败终态或允许盲目重试；先查询该运行对应版本，再补齐运行结果。

### 10.3 异步执行生命周期

202 表示已持久化受理，不表示已完成。运行执行由应用任务管理器管理，使用自己的依赖作用域；不能让响应结束时已释放的 FastAPI `Depends` 数据库 Session 继续服务后台任务。

V1 为单实例，不承诺从模型调用中间点自动恢复：服务重启后，先确认原执行者已失效，再核对它的 queued/running 记录。根据 `source_run_id` 恢复已提交的版本引用，将无法继续的运行标记为 `failed/RUN_INTERRUPTED`，释放会话并发布终态事件。多实例部署不在本次范围，将来需要额外设计执行者标识及租约，不能把其他存活实例的任务当成中断。

只有进程内 `create_task()`、内存字典或 `InMemorySaver` 的实现不满足本契约的持久化受理和恢复要求；LangGraph checkpoint 本身也不等同于任务调度器。

## 11. 与当前内部接口的映射

| 边界 | 现有接口 | API 适配要求 |
| --- | --- | --- |
| API → 应用服务 | SessionService、RunService、TripService 占位入口 | 真实受理、会话、运行、事件和结果投影待实现 |
| 应用服务 → Graph | `build_autonomous_graph().ainvoke/astream(...)` | 从可信会话与数据库加载输入；客户端 JSON 不能直接展开进 State |
| Graph 运行依赖 | `AutonomousGraphContext` | 注入模型、地图、TripService、Validator；连接依赖不进入 State |
| Supervisor → Runtime | `SupervisorDecision` | 保留 `ask_user/call_agent/validate/save/finish/fail`；不对 HTTP 客户端公开这些控制指令 |
| Runtime → Specialist | `run(state, instruction) -> ActionResult` | state 为独立快照；保持受控合并，不允许 Agent 写数据库或覆盖预算 |
| Agent → Tool | `ToolRegistry.tools_for/call` | 沿用双方权限、剩余额度、城市与 POI 证据约束 |
| Runtime → TripService → Repository | `load_current/save_version` | 补齐需求与路线快照、基准版本校验、来源运行去重、实际提交事务 |

专业 Agent 的结果 payload 应继续明确验证：requirement 使用 `MinimalParsedRequest`，attraction_search 使用 `PlaceSearchResult`，planner 成功结果必须包含 `draft_itinerary: Itinerary` 与 `route_info: list[RouteInfo]`。API 只投影这些已知结构。

公开 `run_id` 在受理时生成；当前 Graph 在 initialize 中另生成一个 `state.run_id`，在应用层将它记录为内部 `graph_run_id` 并关联到公开运行。不要假设这两个编号天然相同。Repository 通过可信运行上下文取得公开 `source_run_id`，不使用模型输出提供的编号。

`session_id` 映射到服务端生成的 LangGraph `thread_id`；不同会话必须隔离 checkpoint。同会话的新消息会进入新一轮初始化，清空上一轮结果和计数，保留受控的需求上下文。

绑定旅行后，加载时验证基准版本并取得该版本的需求与行程。后续保存再次校验版本。当前 `load_current()` 只返回行程，不足以独立恢复完整会话；持久化当前指针与需求快照的读取必须一致。

## 12. 已交付模型与后续接入验收

| 文件 | 新增或完善职责 |
| --- | --- |
| `app/models/` | 已定义六张表、关联、索引与唯一约束 |
| `app/schemas/api_schema.py` | 已定义严格请求 DTO、公开响应 DTO、Error、分页及 SSE 模型 |
| `scripts/sql/trip.sql` | 已提供新库完整 DDL；没有执行，也不包含旧数据迁移 |
| `scripts/export_schema_sql.py` | 从 ORM 编译 SQL 文件，不连接数据库 |
| `frontend/src/lib/types.ts`、`auth.ts` | 已补充会话摘要、严格在线认证类型和演示兼容类型 |
| `app/main.py` 与现有 `app/api/*_router.py` | 已接入延迟资源生命周期和 501 路由；正式身份与业务执行待实现 |
| 应用服务与会话 Repository | 后续实现会话串行、幂等、后台执行、事件和公开结果投影 |
| `app/repository/trip_repository.py` | 后续实现完整快照、幂等保存事务及版本冲突 |

本次验收：公开 DTO 的正常 JSON、空值、错误输入、金额边界、终态及事件匹配；ORM 映射、MySQL DDL 编译、关键唯一约束和外键；前端类型检查及现有测试。数据库仅做离线结构检查，不能据此宣称真实事务已验证。

后续实现顺序：先接用户存储与身份依赖，再补会话/运行存储与 Graph 适配，完成需求追问和景点搜索；再接受保护的事件重放；最后完善校验与保存事务。未开放的能力统一明确报错，不返回虚构成功数据。

后续业务接入必须另外验收（本次模型测试不替代）：

- 信息不足返回 needs_input，补充消息沿用会话、换运行编号，并保留已知需求。
- 用户 A 不能读取或修改用户 B 的会话、运行、事件和版本；会话列表仅返回自己的数据。
- POST 响应丢失后重试只产生一次 Graph 执行；同键不同内容明确冲突。
- 同会话并发提交被拒绝，不污染 checkpoint；不同会话互不串数据。
- SSE 断开不终止运行，重连不重新执行；事件重放与实时交接不漏终态。
- 空搜索、依赖缺失、真实查询失败三种情况具有不同且准确的结果。
- 未实现能力返回失败；所有终态均能由 GET 恢复，不泄露内部 trace。
- 两个会话修改同一版本，最多一个保存成功；冲突方不能覆盖新版本。
- 保存提交后、终态发布前发生进程中断，可以查到已保存版本且不会重复保存。
- 未知费用保持 null；校验不把未知价格当免费，也不把内部部分检查当成全部验证。
- 运行中断、结构化输出失败和结果投影失败均释放会话占用，并形成稳定错误结果。

本文已核对源代码边界；六表实体、SQL 与契约模型已定义，业务 HTTP、持久化、SSE 和真实服务联调仍待接入验证，不以现有离线 Graph 测试替代这些验收。
