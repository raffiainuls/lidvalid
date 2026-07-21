"""validation_core — unified data-validation engine.

Merges the aggregate/statistical validator from `validation-data` and the
chunked row-level validator from `validation_database` into one engine that
is agnostic to how it's invoked (CLI, FastAPI worker, tests).

Submodules:
  categories   - column type -> category mapping, value comparison helpers
  connectors   - per-engine SQL dialects + DB clients (mysql, clickhouse, sqlite)
  aggregate    - Report 1-5 statistical validator (ported from db_validator.py)
  rowlevel     - chunked-by-id missing-key + value-diff validator
  runner       - tiered orchestration (aggregate first, row-level for FAILs)
  models       - shared dataclasses (ConnectionParams, TableSpec, RunSettings)
  excel_export - Excel summary writer compatible with the legacy tool's output
"""

__version__ = "0.1.0"
