# Trip Agents 前端

基于 React、TypeScript、Vite 和 shadcn/ui 的旅行工作台。页面采用“我的旅行 / 对话 / 当前行程”三栏布局，支持需求摘要、运行进度、每日时间线、版本历史，以及搜索结果与地图。手机通过页签切换工作区。

后端 HTTP 接口目前仍是设计草案。前端已按契约连接版本列表和详情接口；默认长沙演示可创建、修改并保存本地样例版本，费用保持未知，路线校验明确标为演示检查。它不代表真实 Agent、地图路线和数据库保存已实现。切换 API 模式前，需要先实现 [`docs/API_CONTRACT.md`](../docs/API_CONTRACT.md) 中对应路由。

只有 `completed` 且含 `saved_version` 的运行会自动切换正式行程；追问、生成和失败保留原版本。失败返回保存回执时提供明确的已保存版本查看入口，避免盲目重试。搜索添加通过新的消息运行发起，不直接修改行程数据。历史版本只读，继续修改时明确返回当前版本并携带 `expected_version_no`。

目前契约没有会话列表接口，左栏显示本浏览器创建或访问过的会话，索引按数据模式与 API 地址隔离。历史版本通过专用分页接口加载，不依赖聊天历史是否翻页。

## 本地运行

建议使用 Node.js 24 LTS 与 npm；当前 Vitest 版本要求 Node.js 22.12+、24 或 26+，具体以依赖包的 `engines` 为准。

在项目根目录执行：

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local
npm run dev
```

开发地址通常为 `http://127.0.0.1:5173`。端口被占用时，以 Vite 输出的实际地址为准。默认演示模式无需后端服务或高德 Key，地图底图及景点照片仍需要网络。

```powershell
npm run typecheck
npm run build
npm test
npm run preview
```

`build` 先运行 TypeScript 检查，再生成 `dist/`。`test` 运行 Vitest；上述命令是验证入口，实际执行结论以本次运行输出为准。`preview` 仅用于预览构建产物。

## 配置

从 `.env.example` 创建 `.env.local` 后修改。Vite 启动时读取配置，修改后重启开发服务器；生产配置需要重新构建。

| 变量 | 默认值 / 示例 | 用途 |
| --- | --- | --- |
| `VITE_DATA_MODE` | `demo` | `demo` 使用浏览器本地夹具；`api` 调用真实接口 |
| `VITE_API_BASE_URL` | `/api/v1` | JSON 请求与 SSE 的基础路径 |
| `API_PROXY_TARGET` | `http://127.0.0.1:8000` | 仅供 Vite 开发服务器转发 `/api` 请求 |
| `VITE_AMAP_JS_KEY` | 留空 | 高德 JavaScript API 专用浏览器 Key；留空使用 OpenStreetMap |
| `VITE_AMAP_SECURITY_PROXY` | 留空 | 高德安全代理的 `serviceHost`；正式环境优先配置 |
| `VITE_AMAP_SECURITY_CODE` | 留空 | 仅开发模式读取的高德安全码，不用于正式部署 |

所有 `VITE_` 变量均可被浏览器访问，不能保存模型服务密钥、数据库口令或后端 MCP Key。高德浏览器 Key 和后端服务 Key 分开申请、配置与限制；推荐通过高德安全代理保护安全码。代码在生产环境不读取 `VITE_AMAP_SECURITY_CODE`，仍应避免把任何服务端秘密放入前端环境文件。

## 数据和部署边界

- 演示数据使用 `localStorage`，前缀为 `trip-agents:demo:v1:`；它是本地界面夹具，不是真实搜索或服务器保存的旅行。演示会话与 API 会话、查询缓存分别隔离。
- API 模式不会因为请求失败而自动展示演示结果。连接错误会保留原输入，要求用户按实际状态恢复或重试。
- 缺少高德浏览器配置或 SDK 加载失败时，地图使用 Leaflet + OpenStreetMap。接口地点坐标为 GCJ-02，使用 `gcoord` 转换为 OSM 所需的 WGS-84。
- OSM 公共瓦片用于当前演示。正式部署应选择适合流量与地区要求的瓦片服务，遵循其许可、署名、缓存与访问策略；不能将公共服务当作无限额生产服务。
- 发布 `dist/` 时，服务器需要为前端路由配置 `index.html` 回退，并将 `/api` 反向代理到后端。`API_PROXY_TARGET` 不会在构建产物或 `vite preview` 中建立生产代理；SSE 路径还需关闭代理缓冲并配置合适的连接超时。

更完整的目录、状态设计、图片许可和人工验收清单见 [`docs/FRONTEND.md`](../docs/FRONTEND.md)。
