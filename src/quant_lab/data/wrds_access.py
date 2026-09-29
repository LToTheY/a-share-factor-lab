"""Read-only WRDS access without creating or updating password files."""

from __future__ import annotations

import getpass
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def candidate_csmar_libraries(libraries: list[str]) -> list[str]:
    markers = ("csmar", "china_stock", "china_market")
    return sorted({name for name in libraries if any(
        marker in name.lower() for marker in markers
    )})


@contextmanager
def open_wrds(username: str) -> Iterator[Any]:
    """Use WRDS query helpers, bypassing its interactive pgpass-saving flow.

    Only a hidden terminal prompt supplies the password. Never accept it as a
    command-line argument, dotenv setting or saved connection URL. The engine
    keeps credentials in memory for the duration of this context only.
    """
    import sqlalchemy as sa
    import wrds
    from wrds.sql import WRDS_POSTGRES_DB, WRDS_POSTGRES_HOST, WRDS_POSTGRES_PORT

    if not username.strip():
        raise ValueError("A WRDS username is required")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass("WRDS password (hidden, not saved): ")
        except getpass.GetPassWarning:
            raise RuntimeError("Run in a local terminal with hidden password input") from None
    if not password:
        raise ValueError("Empty password; no connection attempted")
    client = wrds.Connection(autoconnect=False, wrds_username=username)
    engine = sa.create_engine(
        sa.URL.create(
            "postgresql+psycopg2",
            username=username,
            password=password,
            host=WRDS_POSTGRES_HOST,
            port=WRDS_POSTGRES_PORT,
            database=WRDS_POSTGRES_DB,
        ),
        isolation_level="AUTOCOMMIT",
        connect_args={
            "sslmode": "require",
            "connect_timeout": 60,
            "application_name": "a-share-factor-lab",
            "options": "-c default_transaction_read_only=on -c statement_timeout=120000",
        },
    )
    del password
    connection = None
    try:
        # The official client's default connect() may offer to write pgpass.
        # Bind a SQLAlchemy connection directly instead; no credential file I/O.
        connection = engine.connect()
        client.engine = engine
        client.connection = connection
        client.load_library_list()
        yield client
    finally:
        if connection is not None:
            connection.close()
        engine.dispose()


def describe_library(connection: Any, library: str) -> dict[str, Any]:
    """Read accessible table/column metadata, never table contents or counts."""
    if library not in connection.list_libraries():
        raise ValueError("Requested library is not visible to this account")
    columns = connection.raw_sql(
        """
        SELECT c.relname AS table_name, a.attname AS column_name,
               pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
               pg_catalog.col_description(c.oid, a.attnum) AS comment
        FROM pg_catalog.pg_namespace n
        JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = %(library)s
          AND c.relkind IN ('r', 'v', 'm', 'f', 'p')
          AND a.attnum > 0 AND NOT a.attisdropped
          AND has_column_privilege(c.oid, a.attnum, 'SELECT')
        ORDER BY c.relname, a.attnum
        """,
        params={"library": library},
        chunksize=None,
    )
    tables: dict[str, list[dict[str, str | None]]] = {}
    for row in columns.itertuples(index=False):
        import pandas as pd

        tables.setdefault(str(row.table_name), []).append({
            "name": str(row.column_name),
            "type": str(row.data_type),
            "comment": None if pd.isna(row.comment) else str(row.comment),
        })
    return {"tables": tables, "table_count": len(tables)}
