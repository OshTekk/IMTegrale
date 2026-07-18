from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0014_canonical_engineering_semesters.py"
    )
    spec = importlib.util.spec_from_file_location("semester_migration_0014", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_normalizes_legacy_semesters_without_rewriting_explicit_s5() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    settings = sa.Table(
        "ue_settings",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("metadata_source", sa.String(32), nullable=False),
        sa.Column("semester", sa.String(32)),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
    )
    entries = sa.Table(
        "simulation_entries",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("semester", sa.String(16)),
        sa.Column("base_semester", sa.String(16)),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("base_title", sa.String(200)),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            settings.insert(),
            [
                {
                    "id": 1,
                    "metadata_source": "competences",
                    "semester": "S1",
                    "title": "Outils mathematiques S5",
                },
                {
                    "id": 2,
                    "metadata_source": "competences",
                    "semester": "S6",
                    "title": "Projet de fin d'etudes S10",
                },
                {"id": 3, "metadata_source": "manual", "semester": "S2", "title": "UE libre"},
                {"id": 4, "metadata_source": "manual", "semester": "S5", "title": "UE libre"},
                {"id": 5, "metadata_source": "competences", "semester": "S5", "title": "Management S5"},
            ],
        )
        connection.execute(
            entries.insert(),
            [
                {
                    "id": 1,
                    "origin": "imported",
                    "semester": "S1",
                    "base_semester": "S1",
                    "title": "Outils mathematiques S5",
                    "base_title": "Outils mathematiques S5",
                },
                {
                    "id": 2,
                    "origin": "imported",
                    "semester": "S2",
                    "base_semester": "S1",
                    "title": "Hypothese",
                    "base_title": "Outils mathematiques S5",
                },
                {
                    "id": 3,
                    "origin": "imported",
                    "semester": "S5",
                    "base_semester": "S1",
                    "title": "Hypothese",
                    "base_title": "Outils mathematiques S5",
                },
                {
                    "id": 4,
                    "origin": "simulated",
                    "semester": "S3",
                    "base_semester": None,
                    "title": "UE libre",
                    "base_title": None,
                },
                {
                    "id": 5,
                    "origin": "simulated",
                    "semester": "S5",
                    "base_semester": None,
                    "title": "UE libre",
                    "base_title": None,
                },
                {
                    "id": 6,
                    "origin": "imported",
                    "semester": "S5",
                    "base_semester": "S5",
                    "title": "Management S5",
                    "base_title": "Management S5",
                },
                {
                    "id": 7,
                    "origin": "imported",
                    "semester": "S6",
                    "base_semester": "S6",
                    "title": "Projet final S10",
                    "base_title": "Projet final S10",
                },
            ],
        )

        migration = load_migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        setting_rows = connection.execute(
            sa.text("SELECT id, semester, source_semester FROM ue_settings ORDER BY id")
        ).all()
        entry_rows = connection.execute(
            sa.text("SELECT id, semester, base_semester FROM simulation_entries ORDER BY id")
        ).all()

    assert setting_rows == [
        (1, "S5", "S1"),
        (2, "S10", "S6"),
        (3, "S6", "S2"),
        (4, "S5", "S5"),
        (5, "S5", "S5"),
    ]
    assert entry_rows == [
        (1, "S5", "S5"),
        (2, "S6", "S5"),
        (3, "S5", "S5"),
        (4, "S7", None),
        (5, "S5", None),
        (6, "S5", "S5"),
        (7, "S10", "S10"),
    ]
