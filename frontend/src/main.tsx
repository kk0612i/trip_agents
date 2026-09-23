import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import App from "./App";
import { AuthProvider } from "./components/auth/auth-page";
import "./index.css";
import "./workspace.css";

// 查询可恢复，写入不自动重试；一次逻辑提交的幂等键由数据层单独管理。
const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1 }, mutations: { retry: false } },
});

/** 将不可恢复的渲染错误限制在页面内，提供明确的恢复入口。 */
class WorkspaceErrorBoundary extends React.Component<React.PropsWithChildren, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return (
      <main className="fatal-error" role="alert">
        <h1>页面暂时无法显示</h1>
        <p>请刷新页面，重新读取当前会话。</p>
        <button onClick={() => window.location.reload()}>重新加载</button>
      </main>
    );
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WorkspaceErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider><TooltipProvider delayDuration={250}><App /></TooltipProvider></AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </WorkspaceErrorBoundary>
  </React.StrictMode>,
);
