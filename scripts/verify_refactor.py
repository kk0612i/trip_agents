"""离线验收包迁移、显式导入依赖及 ORM 建表 SQL，不修改文件或访问数据库。

在项目根目录执行 ``uv run python -m scripts.verify_refactor``。
静态依赖检查排除 TYPE_CHECKING 和函数内延迟导入，不替代动态运行测试。
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
# 只检查导入语法，历史迁移文档可以继续解释旧路径。
OLD_IMPORTS = (
    "app.agents", "app.db", "app.models.entities", "app.models.schemas",
    "app.models.api_schemas", "app.tools.schemas", "app.client.llm_client",
    "app.core.logger", "app.api.main", "app.api.dependencies",
    "app.api.auth.router", "app.api.sessions.router", "app.api.runs.router",
    "app.api.trips.router", "app.agent.specialists", "test", "script",
)


def module_name(path: Path) -> str:
    """将项目源码路径转换为可导入模块名，包初始化文件对应包本身。"""
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class ImportCollector(ast.NodeVisitor):
    """收集运行期显式依赖，并分别统计类型检查和函数延迟导入。"""

    def __init__(self, module: str, is_package: bool) -> None:
        """保存被扫描模块的相对导入基准及作用域计数。"""
        self.package = module if is_package else module.rpartition(".")[0]
        self.function_depth = 0
        self.typing_depth = 0
        self.imports: list[tuple[str, int, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """函数体依赖记为延迟执行，不参与模块初始化环检测。"""
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node: ast.If) -> None:
        """识别标准 TYPE_CHECKING 守卫，else 分支仍按运行期扫描。"""
        is_typing = (
            isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        ) or (
            isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
        )
        self.typing_depth += int(is_typing)
        for child in node.body:
            self.visit(child)
        self.typing_depth -= int(is_typing)
        for child in node.orelse:
            self.visit(child)

    def _record(self, name: str, line: int) -> None:
        kind = "typing" if self.typing_depth else "local" if self.function_depth else "runtime"
        self.imports.append((name, line, kind))

    def visit_Import(self, node: ast.Import) -> None:
        """记录普通导入路径与位置。"""
        for alias in node.names:
            self._record(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """解析绝对和相对 from 导入，保留可能指向子模块的名称。"""
        base = node.module or ""
        if node.level:
            base = importlib.util.resolve_name("." * node.level + base, self.package)
        self._record(base, node.lineno)
        for alias in node.names:
            if alias.name != "*":
                self._record(f"{base}.{alias.name}", node.lineno)


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """查找显式运行期依赖环；返回包含起止模块的路径。"""
    visited: set[str] = set()
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(module: str) -> None:
        if module in stack:
            cycles.append(stack[stack.index(module):] + [module])
            return
        if module in visited:
            return
        visited.add(module)
        stack.append(module)
        for dependency in sorted(graph[module]):
            visit(dependency)
        stack.pop()

    for module in sorted(graph):
        visit(module)
    return cycles


def check_sources() -> dict[str, object]:
    """编译源码并检查旧导入和显式依赖环，不写入 Python 缓存。"""
    files = sorted(path for folder in ("app", "tests", "scripts")
                   for path in (ROOT / folder).rglob("*.py"))
    modules = {module_name(path) for path in files if path.is_relative_to(ROOT / "app")}
    graph: dict[str, set[str]] = {module: set() for module in modules}
    stale: list[str] = []
    excluded: dict[str, set[str]] = {"typing": set(), "local": set()}
    for path in files:
        source = path.read_text(encoding="utf-8-sig")
        compile(source, str(path), "exec")
        module = module_name(path)
        collector = ImportCollector(module, path.name == "__init__.py")
        collector.visit(ast.parse(source, filename=str(path)))
        for name, line, kind in collector.imports:
            if any(name == old or name.startswith(old + ".") for old in OLD_IMPORTS):
                stale.append(f"{path.relative_to(ROOT)}:{line}: {name}")
            if module not in graph:
                continue
            if kind != "runtime":
                excluded[kind].add(f"{module}:{line}")
                continue
            if name in modules and name != module:
                graph[module].add(name)
    cycles = find_cycles(graph)
    if stale or cycles:
        raise AssertionError({"stale_imports": sorted(set(stale)), "cycles": cycles})
    return {"compiled_files": len(files), "app_modules": len(modules),
            "runtime_dependency_edges": sum(map(len, graph.values())),
            "excluded_typing_import_statements": len(excluded["typing"]),
            "excluded_local_import_statements": len(excluded["local"]),
            "stale_imports": [], "explicit_runtime_cycles": []}


def check_isolated_imports_and_ddl() -> dict[str, object]:
    """独立进程禁止外网和资源模块导入，验证全部 DTO、ORM 与 DDL。"""
    script = '''
import importlib
import importlib.abc
import json
from pathlib import Path
import socket
import sys

class BlockResources(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("app.core.config", "app.core.db", "app.core.llm", "app.core.log",
                   "app.core.resources", "openai", "langchain_openai")
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Schema/ORM 导入触发资源模块: " + fullname)

def forbidden(*args, **kwargs):
    raise AssertionError("离线验收禁止网络")

socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
sys.meta_path.insert(0, BlockResources())
schemas = sorted(Path("app/schemas").glob("*.py"))
for path in schemas:
    importlib.import_module("app.schemas" if path.stem == "__init__"
                            else "app.schemas." + path.stem)
from app.models import Base
from sqlalchemy.orm import configure_mappers
from scripts.export_schema_sql import render_schema_sql
configure_mappers()
expected = {"app_user", "chat_session", "agent_run", "run_event", "trip", "itinerary_version"}
assert set(Base.metadata.tables) == expected
rendered = render_schema_sql()
assert rendered == Path("scripts/sql/trip.sql").read_text(encoding="utf-8")
print(json.dumps({"schema_modules": len(schemas), "tables": sorted(expected),
                  "ddl_matches_file": True, "resource_module_imports": []}))
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def main() -> None:
    """输出验收证据；失败抛出异常并使用非零退出码。"""
    report = {"source_checks": check_sources(),
              "isolated_import_and_ddl": check_isolated_imports_and_ddl(),
              "scope": "静态显式导入及离线 DDL；动态依赖、真实事务与外部服务需另行验证"}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
