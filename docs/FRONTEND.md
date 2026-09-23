# 前端工作台实现说明

本文保留 `frontend/` 第一版的设计说明，以下部分页面描述已落后于当前代码（当前已有登录、行程和版本面板）。最新接口与实现边界以 [`API_CONTRACT.md`](API_CONTRACT.md) 为准：数据库实体与公开 DTO 已定义，业务 HTTP/SSE 接入仍待实现，现有登录路由使用内存存储。

## 交付范围

当前工作台提供旅行需求对话、需求摘要、候选景点搜索与分类筛选、地点详情以及列表和地图的双向选中。宽屏采用对话、候选地点、地图三栏；窄屏通过视图切换保留主要操作空间。消息区和候选列表分别滚动，地图保留稳定显示区域。

第一版没有每日行程编排、拖拽重排、行程保存、行程版本、酒店、天气、知识问答、订票或支付页面。服务端也尚未实现契约中的 HTTP 路由。类型文件保留部分行程结果结构用于契约对齐，不代表已经开放这些能力。

默认的演示模式只读取本地长沙夹具，界面标明“体验模式”和“示例地点”。规则匹配用于演示追问和景点筛选，不能作为模型理解或真实检索能力的证据。未知票价、营业时间、室内属性维持 `null`；详情显示暂无可靠数据，不能用零元或免费替代未知值。

## 技术和目录

| 选择 | 职责 |
| --- | --- |
| React + TypeScript | 页面组件、交互状态和接口类型 |
| Vite | 本地开发、环境变量、开发代理和静态构建 |
| shadcn/ui + Radix UI | 可在仓库内维护的按钮、弹窗、提示等基础组件 |
| Tailwind CSS + 页面样式 | 统一基础样式、间距与响应式布局 |
| Lucide React | 操作与状态图标 |
| React Router | 会话地址和刷新定位 |
| TanStack Query | 会话、历史消息和运行状态的查询缓存与刷新 |
| 高德 JS API 2.0 | 主要地图底图和 GCJ-02 地点展示 |
| Leaflet + OpenStreetMap + gcoord | 地图回退、坐标转换与相同的标记交互 |
| Vitest | 前端数据协议与边界逻辑的测试入口 |

```text
frontend/
  .env.example                 环境配置模板
  src/
    main.tsx                   Router、QueryClient 和错误边界
    App.tsx                    页面布局及面板状态连接
    index.css                  工作台和响应式样式
    components/
      ui/                      通过 shadcn CLI 引入并本地化的基础组件
      workspace/
        chat-panel.tsx         对话、需求摘要和消息草稿
        places-panel.tsx       关键词/类别筛选与地点详情
        map-panel.tsx          地图容器和交互工具
    hooks/
      use-workspace.ts         会话恢复及运行生命周期
    lib/
      types.ts                 与接口契约对应的 DTO
      api.ts                   HTTP、错误对象、提交对象和 SSE 订阅
      demo.ts                  localStorage 演示夹具
      map-loader.ts            高德和 OSM 适配器及资源清理
      place-images.ts          已核对的实景照片、署名和来源
      utils.ts                 基础样式工具
```

组件接收数据和回调，不在聊天或候选组件中直接请求后端。地图适配器只负责坐标、视野、标记和 SDK 生命周期，不修改接口原始地点。原始候选数组确定地图编号，关键词或类别筛选不会重新给地图地点编号。

## 状态和请求语义

### 数据来源隔离

`VITE_DATA_MODE=demo` 使用本地夹具；`api` 使用 HTTP 和 SSE。演示记录以 `trip-agents:demo:v1:` 为 `localStorage` 前缀，保存会话、运行和尚待完成的演示结果。演示通过已保存的开始时间推进状态，因此运行期间刷新后可以继续恢复，不会永久卡在处理中。

API 模式不能在请求失败后静默切换到演示。API 中尚未确认受理的提交使用独立键 `trip-agents:api:pending:<baseUrl>:<sessionId>`，保留原始消息和幂等编号供刷新后确认。查询缓存也包含数据模式和会话编号。`localStorage` 只是演示和前端恢复辅助存储，不是后端数据库；清理浏览器站点数据会清掉本地记录及待确认提交。API 模式的会话与运行应以服务端查询结果为准。

### 多轮运行

每条用户消息对应一个 `Run`。`queued` 和 `running` 表示当前轮尚未完成；`completed`、`needs_input`、`failed` 都是终态。

`needs_input` 表示本轮已经结束，用户补充信息时沿用 `session_id`、提交新的消息并创建新的 `run_id`，不尝试恢复同一个终态运行。界面仅发送本轮文本、逻辑提交编号和需要时的版本号，不上传 Agent 内部状态、推理记录或校验证明。

聊天输入支持中文输入法组合输入。组合输入的回车只确认候选词，普通回车发送，Shift+Enter 保留换行。消息按 Unicode 字符计数限制为 1 至 4000 个字符；发送期间使用同步锁阻止连续点击或连续回车。只有提交成功才清空当前草稿，请求失败保留用户文本。

### 幂等与错误恢复

一次逻辑提交生成一个 `client_request_id`。网络中断、超时或响应丢失不能证明服务端没有受理，重试必须复用原始提交对象和编号；新的用户消息才使用新的编号。不能让通用写入重试自动创建第二个逻辑请求。

待确认提交存在时，发送不同消息会被阻止，用户先通过重试确认原请求。已确定为失败终态的运行，只有错误声明 `retryable=true` 且没有 `saved_version` 副作用时，重试才创建新运行；其他情况的重试只刷新查询。HTTP 明确拒绝的 4xx 请求可以清除待确认记录，非法响应仍按不确定受理结果保留。

`ApiError` 保留错误码、HTTP 状态、是否可重试、错误详情和 `request_id`，便于界面恢复和排查。`SESSION_BUSY`、`VERSION_CONFLICT`、`IDEMPOTENCY_CONFLICT` 有对应提示；存储被禁止、数据损坏、网络失败和非法响应也需要明确反馈，不能显示成空搜索结果。真实失败轮次不覆盖已经保存的正式旅行。

刷新页面时按当前会话地址重新查询会话和运行，读取 `active_run_id` 后恢复监听。分页历史按服务端游标拉取，界面按时间顺序展示；加载旧消息不能把视口强制拉回最新消息。路由切换时必须关闭旧订阅并取消无效查询，避免旧会话结果覆盖新页面。

### 运行进度

API 通过原生 `EventSource` 读取命名事件，只展示面向用户的阶段描述和最终回答，不模拟逐字输出。订阅校验 `run_id`、序号及事件 ID，忽略重复或无关运行事件。

收到匹配的终态事件后立即 `close()`，再更新最终结果；GET 运行查询保留为权威兜底。原生 SSE 断线重连会携带 `Last-Event-ID`，服务端必须按契约支持事件重放。开发代理和生产代理都需要允许长连接，生产代理还应避免缓存、缓冲 SSE 响应。

## 运行配置和部署

安装、启动和检查命令见 [`frontend/README.md`](../frontend/README.md)。推荐 Node.js 24 LTS。常用命令在 `frontend/` 下执行：

```powershell
npm install
npm run dev
npm run typecheck
npm run build
npm test
```

`frontend/.env.example` 是配置清单。`VITE_API_BASE_URL` 同时作用于 JSON 与 SSE；默认 `/api/v1` 有利于浏览器保持同源。`API_PROXY_TARGET` 只由 Vite 开发服务器使用，生产构建不会自动提供它代表的反向代理。部署静态文件时必须在服务器上配置 `/api` 转发及前端路由的 `index.html` 回退。

高德浏览器 Key 配置在 `VITE_AMAP_JS_KEY`，不能复用后端 Web 服务调用 Key。`VITE_AMAP_SECURITY_PROXY` 对应高德安全代理 `serviceHost`，生产环境推荐配置。`VITE_AMAP_SECURITY_CODE` 仅在开发模式读取；所有 `VITE_` 变量均属于浏览器可见配置，不能当成秘密存储。

## 地图与实景图片

候选 DTO 中经纬度是 GCJ-02。高德直接使用该坐标；Leaflet 使用 WGS-84，`map-loader.ts` 调用 `gcoord` 转换，再按 Leaflet 要求传入 `[latitude, longitude]`。前端先检查坐标是有限数值且在合法经纬度范围，非法坐标不生成标记。

高德未配置、SDK 加载失败或超时后回退到 OSM；回退状态会显示在地图上。地图瓦片仍依赖网络，网络故障时显示错误与重试入口。地图视野适配、缩放和选中状态使用相同组件接口，卸载时销毁实例并清理监听。

OSM 底图保留贡献者署名。目前使用 `tile.openstreetmap.org` 公共瓦片进行演示。正式部署应根据使用地区、流量及可用性要求选择地图供应商，并遵循 [OSM 瓦片使用策略](https://operations.osmfoundation.org/policies/tiles/) 与 [署名要求](https://www.openstreetmap.org/copyright)，包括访问、缓存和禁止批量离线下载等规定。

实景照片配置在 `frontend/src/lib/place-images.ts` 的 `getPlaceImage()` 中，仅匹配已经核对的长沙地点名称，不使用无关风景图片替代结果。图片没有匹配或加载失败时显示地点图标。详情页提供作者、许可文本和可点击的原始文件页：

| 地点 | 作者 | 许可 | 原始文件 |
| --- | --- | --- | --- |
| 湖南博物院 | Siyuwj | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | [Entrance of Hunan Museum, 2018-09-28](https://commons.wikimedia.org/wiki/File:Entrance_of_Hunan_Museum,_2018-09-28.jpg) |
| 岳麓书院 | xiquinhosilva | [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/) | [Yuelu academy](https://commons.wikimedia.org/wiki/File:Yuelu_academy.jpg) |
| 橘子洲 | Huangdan2060 | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) | [Orange Isle 2021122636](https://commons.wikimedia.org/wiki/File:Orange_Isle_2021122636.jpg) |
| 太平老街 | EditQ | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | [Taiping Street, Changsha](https://commons.wikimedia.org/wiki/File:Taiping_Street,_Changsha.jpg) |

保留来源链接和署名；如后续裁剪、加工、替换或再分发图片，应核对原始页面许可并标明相关修改。图片与地图数据来源分别说明，照片不构成开放时间、预约或票价已经核实的依据。

## 中文注释约定

- 对导出的组件、公共类型、适配器和关键数据函数添加简短中文文档注释，说明职责与输入输出边界。
- 对幂等请求、终态识别、重连、坐标转换、异步取消、IME 和存储恢复等不直观逻辑添加中文注释，说明为什么这样处理。
- 注释不逐行翻译赋值或循环；接口字段和代码标识符保留英文，注释与当前代码行为一起更新。
- 未实现能力、夹具来源与未知数据需要在文档和类型中明确，不能用注释宣称尚未验证的生产能力。

## 人工验收清单

下面是验收步骤，不代表这些步骤已经在当前环境通过；实际结果需要记录测试环境、执行时间与失败限制。

- 默认演示进入长沙工作台，来源标记可见；新建会话后出现空对话，输入可用。
- 输入“去长沙”，确认追问天数；补充“三天”后产生新运行，并出现候选地点。
- 中文输入法回车确认不会误发送；Shift+Enter 换行；快速连续发送不会创建重复轮次。
- 运行期间刷新页面，恢复会话和运行；终态后输入重新可用；历史分页不重复且不打乱顺序。
- 关键词和类别筛选可以组合，清除筛选恢复结果；空结果提示明确，筛选前后编号与地图一致。
- 点击候选定位地图，点击地图标记高亮候选；缩放和显示全部地点有效。
- 地点详情可以关闭；票价、营业时间显示未知；图片作者与许可来源链接可打开。
- “找类似的地方”只填入对话草稿，用户确认后才发送；不创建虚假的每日行程。
- 未配置高德 Key 时显示 OSM；配置有效 Key 后显示高德；模拟地图网络失败时显示错误与重试。
- 桌面、窄屏和手机宽度下无文字重叠、横向溢出或被遮挡的输入按钮；切换回地图时地图尺寸正确。
- 在已实现契约的后端或受控接口夹具中，检查 POST 丢失响应后的同键重试、SSE 断线、终态关闭与 GET 兜底。
- API 返回连接错误、会话忙碌、版本冲突时提示正确，不伪装演示成功；API 和演示会话不会互相读取。
- 执行类型检查、构建和测试，并记录实际输出；真实服务端联调需要另行记录后端版本和依赖服务状态。

## 本次验证记录

验证日期：2026-09-20，Windows、Node.js 24.13.0。只运行前端检查，没有修改或运行 Python 后端业务。

- `npm run build` 通过，包含 TypeScript 检查与生产构建。
- `npm test` 通过：3 个测试文件，共 26 项，覆盖 HTTP 错误、SSE 去重及终态、演示追问、直接搜索、幂等重试、会话切换隔离和终态不可回退。
- Playwright 检查 1440px 桌面、1024px 窄屏、390px 与 320px 手机布局，无页面横向溢出。
- 实测列表和地图双向选择、类别过滤、筛选外及同编号重复选择、地点详情、关闭后焦点恢复、填写相似地点需求、新建会话进入对话、两轮追问和运行中刷新恢复。
- 实测 OpenStreetMap 瓦片与四个地点标记正常显示，四张 Wikimedia 照片正常加载；截屏保存在 `frontend/output/playwright/`，该目录不入库。
- 未提供高德浏览器 Key，尚未进行高德 SDK 的真实服务验收；真实 HTTP 后端尚未实现，API 层使用受控测试夹具验证，不能视为前后端联调完成。

默认入口仍为明确标记的本地演示模式，真实规划、版本保存和后端服务交付需按接口契约继续实现。
