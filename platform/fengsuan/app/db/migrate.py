from __future__ import annotations

from sqlalchemy import Boolean, DateTime, Integer, String, Text, inspect, text

from fengsuan.app.db.base import Base
from fengsuan.app.db.session import get_engine


target_metadata = Base.metadata


def _sql_type(col) -> str:
    typ = col.type
    if isinstance(typ, Boolean):
        return "INTEGER"
    if isinstance(typ, Integer):
        return "INTEGER"
    if isinstance(typ, DateTime):
        return "TIMESTAMP"
    if isinstance(typ, Text):
        return "TEXT"
    if isinstance(typ, String):
        return f"VARCHAR({typ.length or 255})"
    return "TEXT"


def _literal(value) -> str | None:
    if value is None or callable(value):
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _column_ddl(col) -> str:
    sql_type = _sql_type(col)
    default = None
    if col.server_default is not None and getattr(col.server_default, "arg", None) is not None:
        default = _literal(str(col.server_default.arg))
    if default is None and col.default is not None:
        default = _literal(getattr(col.default, "arg", None))
    if col.nullable:
        return f"{sql_type} DEFAULT {default}" if default else sql_type
    if default is None:
        if isinstance(col.type, Boolean):
            default = "0"
        elif isinstance(col.type, Integer):
            default = "0"
        elif isinstance(col.type, (String, Text)):
            default = "''"
        else:
            return sql_type
    return f"{sql_type} NOT NULL DEFAULT {default}"


def ensure_model_columns(engine=None) -> None:
    """给已有表补齐模型新列。create_all 不会 ALTER 旧表。"""
    from fengsuan.app import models  # noqa: F401

    eng = engine or get_engine()
    with eng.begin() as conn:
        insp = inspect(conn)
        existing_tables = set(insp.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {_column_ddl(col)}'))
            if table.name == "orgs" and "status" in {c.name for c in table.columns}:
                conn.execute(text("UPDATE orgs SET status = 'approved' WHERE status IS NULL OR status = ''"))


def ensure_org_onboarding_columns(engine=None) -> None:
    ensure_model_columns(engine)


def ensure_perf_indexes(engine=None) -> None:
    """Existing-table indexes. create_all will not add them to old clouds."""
    eng = engine or get_engine()
    specs = [
        ("research_runs", "ix_research_runs_lab_status_created", ["lab_id", "status", "created_at"]),
        ("literature_analyses", "ix_literature_analyses_item_type_by", ["item_id", "analysis_type", "created_by"]),
        ("literature_file_pages", "ix_literature_file_pages_item", ["item_id", "page_no"]),
        ("kb_chunks", "ix_kb_chunks_lab_id", ["lab_id"]),
        ("kb_chunks", "ix_kb_chunks_literature_item_id", ["literature_item_id"]),
        ("kb_chunks", "ix_kb_chunks_lab_doc", ["lab_id", "doc_id"]),
        ("kb_chunks", "ix_kb_chunks_embed_model", ["embed_model"]),
        ("literature_items", "ix_literature_items_read_level", ["read_level"]),
        ("literature_files", "ix_literature_files_structure_state", ["structure_state"]),
        ("literature_items", "ix_literature_items_openalex_id", ["openalex_id"]),
        ("literature_items", "ix_literature_items_lab_read", ["lab_id", "read_level", "read_score"]),
    ]
    with eng.begin() as conn:
        insp = inspect(conn)
        have_tables = set(insp.get_table_names())
        for table, name, cols in specs:
            if table not in have_tables:
                continue
            if any(idx.get("name") == name for idx in insp.get_indexes(table)):
                continue
            quoted = ", ".join(f'"{col}"' for col in cols)
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS {name} ON "{table}" ({quoted})'))


def ensure_legacy_columns(engine=None) -> None:
    from fengsuan.app.services.search_index import ensure_search_schema

    ensure_model_columns(engine)
    ensure_perf_indexes(engine)
    ensure_search_schema(engine or get_engine())


def run_migrations() -> None:
    from fengsuan.app import models  # noqa: F401

    eng = get_engine()
    Base.metadata.create_all(bind=eng)
    ensure_legacy_columns(eng)
