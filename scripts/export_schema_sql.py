"""从 ORM 编译新库建表 SQL；只写文件，不连接数据库。

在项目根目录执行：uv run python -m scripts.export_schema_sql
"""

from pathlib import Path

from sqlalchemy import String
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql.base import MySQLDDLCompiler
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable, sort_tables_and_constraints

from app.models import Base


class StableDDLCompiler(MySQLDDLCompiler):
    """SQLAlchemy 的表选项遍历集合，固定顺序避免跨进程产生无意义差异。"""

    def post_create_table(self, table):
        if set(table.kwargs) != {"mysql_engine", "mysql_charset", "mysql_collate"}:
            raise ValueError("表选项发生变化，请同步 SQL 导出器")
        options = table.dialect_options["mysql"]
        comment = self.sql_compiler.render_literal_value(table.comment, String())
        return (f" ENGINE={options['engine']} DEFAULT CHARSET={options['charset']} "
                f"COLLATE={options['collate']} COMMENT={comment}")


def render_schema_sql() -> str:
    dialect = mysql.dialect()
    dialect.ddl_compiler = StableDDLCompiler
    statements = [
        "-- 从 SQLAlchemy ORM 生成；仅供新库使用，不包含删除或迁移语句。",
        "-- 更新方式：uv run python -m scripts.export_schema_sql",
        "-- 应用数据库连接也必须使用 UTC 会话时区。",
        "SET time_zone = '+00:00';",
    ]
    # 当前版本的反向外键最后添加，其余表按依赖顺序创建。
    for table, constraints in sort_tables_and_constraints(Base.metadata.tables.values()):
        if table is None:
            for constraint in sorted(constraints, key=lambda item: item.name):
                statements.append(str(AddConstraint(constraint).compile(dialect=dialect)).strip() + ";")
        else:
            statements.append(str(CreateTable(table, include_foreign_key_constraints=constraints).compile(dialect=dialect)).strip() + ";")
            for index in sorted(table.indexes, key=lambda item: item.name):
                statements.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    return "\n".join(line.rstrip() for line in "\n\n".join(statements).splitlines()) + "\n"


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "sql" / "trip.sql"
    target.write_text(render_schema_sql(), encoding="utf-8", newline="\n")
    print(f"已导出 {len(Base.metadata.tables)} 张表至 {target}；未连接数据库。")
