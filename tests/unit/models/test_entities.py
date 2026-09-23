"""验证实际 ORM 约束及 MySQL DDL，不以离线编译替代数据库事务测试。"""

from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import configure_mappers

from app.models import AgentRun, AppUser, Base, ChatSession, ItineraryVersion, RunEvent, Trip
from scripts.export_schema_sql import render_schema_sql


def unique_sets(table):
    return {tuple(column.name for column in constraint.columns)
            for constraint in table.constraints if isinstance(constraint, UniqueConstraint)}


def test_six_tables_and_existing_repository_relationships_resolve():
    configure_mappers()
    assert set(Base.metadata.tables) == {"app_user", "chat_session", "agent_run", "run_event", "trip", "itinerary_version"}
    assert Trip.current_version.property.mapper.class_ is ItineraryVersion
    assert Trip.versions.property.mapper.class_ is ItineraryVersion


def test_database_enforces_submission_and_version_uniqueness():
    assert ("email",) in unique_sets(AppUser.__table__)
    assert {("run_id",), ("session_id", "client_request_id")} <= unique_sets(AgentRun.__table__)
    assert {("trip_id", "version_no"), ("source_run_id",)} <= unique_sets(ItineraryVersion.__table__)
    assert tuple(RunEvent.__table__.primary_key.columns.keys()) == ("run_id", "seq")


def test_ownership_foreign_keys_and_logical_session_pointers():
    for table in (Trip.__table__, ChatSession.__table__):
        assert next(iter(table.c.user_id.foreign_keys)).target_fullname == "app_user.id"
        assert not table.c.user_id.nullable
    assert not ChatSession.__table__.c.active_run_id.foreign_keys
    assert not ChatSession.__table__.c.latest_run_id.foreign_keys
    assert next(iter(ItineraryVersion.__table__.c.source_run_id.foreign_keys)).target_fullname == "agent_run.run_id"
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.name.endswith("_at"):
                assert isinstance(column.type, DATETIME) and column.type.fsp == 6
            for fk in column.foreign_keys:
                assert fk.column is not None


def test_exported_sql_matches_orm_and_defers_only_current_version_link():
    sql = render_schema_sql()
    assert sql == Path("scripts/sql/trip.sql").read_text(encoding="utf-8")
    assert sql.count("CREATE TABLE ") == 6
    assert sql.count("ALTER TABLE ") == 1
    assert sql.index("CREATE TABLE agent_run") < sql.index("CREATE TABLE itinerary_version")
    assert sql.index("CREATE TABLE itinerary_version") < sql.index("ALTER TABLE trip")
    assert "FOREIGN KEY(current_version_id) REFERENCES itinerary_version (id)" in sql
    assert "DROP " not in sql
