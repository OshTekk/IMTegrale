from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

AUDIENCE_ID = "fip:2028"
CATALOG_KINDS = {
    "audience",
    "curriculum",
    "promotion",
    "level",
    "semester",
    "ue",
    "module",
    "chapter",
    "concept",
    "lesson",
    "exercise",
    "pc_td",
    "past_exam",
    "source",
}
SOURCE_BYTES = b"FICTITIOUS-NON-DOCUMENT-SOURCE-BYTES\n"
SECOND_SOURCE_BYTES = b"FICTITIOUS-SECOND-NON-DOCUMENT-SOURCE-BYTES\n"
IMAGE_BYTES = b"FICTITIOUS-NON-IMAGE-ASSET-BYTES\n"

BundleDict = dict[str, Any]
BundleMutator = Callable[[BundleDict], None]


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def write_fictitious_learning_bundle(
    parent: Path,
    release_id: str = "fictitious-release-a",
    *,
    audience_id: str = AUDIENCE_ID,
    manifest_mutator: BundleMutator | None = None,
    search_mutator: BundleMutator | None = None,
) -> Path:
    """Create a small, conspicuously fictitious bundle for isolated tests only."""

    release = parent / release_id
    release.mkdir(parents=True)
    search_payload: BundleDict = {
        "schema_version": 1,
        "release_id": release_id,
        "revision": "fictitious-r1",
        "documents": [
            {
                "id": "search-document-fiction",
                "catalog_node_id": "lesson-fiction",
                "target_id": "content-fiction",
                "audience_ids": [audience_id],
                "title": f"[FICTIF] Leçon {release_id}",
                "body": "Une équation fictive pour la démonstration technique.",
            },
            {
                "id": "search-exercise-fiction",
                "catalog_node_id": "exercise-fiction",
                "target_id": "exercise-content-fiction",
                "audience_ids": [audience_id],
                "title": "[FICTIF] Exercice guidé",
                "body": "Un exercice fictif avec un indice progressif.",
            },
            {
                "id": "search-source-fiction",
                "catalog_node_id": "source-node-fiction",
                "target_id": "source-fiction",
                "audience_ids": [audience_id],
                "title": "[FICTIF] Source de démonstration",
                "body": "Une source fictive consultable page par page.",
            },
        ],
    }
    if search_mutator is not None:
        search_mutator(search_payload)
    search_bytes = json_bytes(search_payload)
    manifest = _manifest(release_id, search_bytes, audience_id)
    if manifest_mutator is not None:
        manifest_mutator(manifest)

    (release / "assets").mkdir()
    (release / "search").mkdir()
    (release / "assets" / "source-fiction.bin").write_bytes(SOURCE_BYTES)
    (release / "assets" / "image-fiction.bin").write_bytes(IMAGE_BYTES)
    (release / "search" / "index.json").write_bytes(search_bytes)
    (release / "manifest.json").write_bytes(json_bytes(manifest))
    return release


def write_fictitious_metadata_only_preview_bundle(
    parent: Path,
    release_id: str = "fictitious-preview-a",
    *,
    audience_id: str = "personal:fictive-owner",
    omit_source_asset_id: bool = False,
    manifest_mutator: BundleMutator | None = None,
) -> Path:
    """Create a personal preview whose source is citation metadata only."""

    def preview_manifest(manifest: BundleDict) -> None:
        manifest["release_mode"] = "private_preview"
        for node in manifest["catalog"]:
            node["review_status"] = "private_preview"
        for document in manifest["content"]:
            document["frontmatter"]["review_status"] = "private_preview"
            document["blocks"] = [block for block in document["blocks"] if block["type"] != "image"]
        source = manifest["sources"][0]
        if omit_source_asset_id:
            source.pop("asset_id")
        else:
            source["asset_id"] = None
        manifest["assets"] = []
        manifest["rights"][0].update(
            {
                "publication_allowed": False,
                "private_preview_allowed": True,
                "source_serving_allowed": False,
                "rights_holder": None,
                "basis": "requester_private_processing",
            }
        )
        manifest["checksums"] = [
            checksum
            for checksum in manifest["checksums"]
            if checksum["file_id"] == manifest["search_index"]["file_id"]
        ]
        if manifest_mutator is not None:
            manifest_mutator(manifest)

    release = write_fictitious_learning_bundle(
        parent,
        release_id,
        audience_id=audience_id,
        manifest_mutator=preview_manifest,
    )
    assets_dir = release / "assets"
    for asset_path in assets_dir.iterdir():
        asset_path.unlink()
    assets_dir.rmdir()
    return release


def write_fictitious_personal_library_bundle(
    parent: Path,
    release_id: str = "fictitious-personal-library-a",
    *,
    audience_id: str = "personal:fictive-owner",
    manifest_mutator: BundleMutator | None = None,
    search_mutator: BundleMutator | None = None,
) -> Path:
    """Create a schema-v3 personal library containing only synthetic material."""

    release = write_fictitious_learning_bundle(
        parent,
        release_id,
        audience_id=audience_id,
    )
    manifest = json.loads((release / "manifest.json").read_bytes())
    search_payload = json.loads((release / "search" / "index.json").read_bytes())

    manifest["schema_version"] = 3
    manifest["release_mode"] = "personal_library"
    manifest["search_index"]["format"] = "json-v3"
    manifest["audiences"][0].update(
        {
            "label": "[FICTIF] Bibliothèque personnelle",
            "curriculum": "Cursus entièrement fictif",
            "promotion": "2099 fictive",
            "level_label": "Niveau fictif",
        }
    )
    sections = {
        "chapter": "course",
        "lesson": "course",
        "exercise": "practice",
        "pc_td": "practice",
        "past_exam": "exam",
        "concept": "glossary",
        "source": "sources",
    }
    for node in manifest["catalog"]:
        node["review_status"] = "reviewed"
        if node["kind"] in sections:
            node["section"] = sections[node["kind"]]
            node["reader_visibility"] = "secondary" if node["kind"] in {"concept", "source"} else "primary"
    for document in manifest["content"]:
        document["frontmatter"]["review_status"] = "reviewed"

    manifest["rights"][0].update(
        {
            "publication_allowed": False,
            "private_preview_allowed": False,
            "personal_use_allowed": True,
            "source_serving_allowed": True,
            "download_allowed": True,
            "rights_holder": None,
            "basis": "requester_private_processing",
        }
    )
    manifest["catalog"].append(
        {
            "id": "source-node-inline-fiction",
            "kind": "source",
            "title": "[FICTIF] Mémo consultable",
            "audience_ids": [audience_id],
            "parent_id": "module-fiction",
            "content_id": None,
            "source_id": "source-inline-fiction",
            "prerequisite_ids": [],
            "difficulty": None,
            "estimated_minutes": None,
            "section": "sources",
            "reader_visibility": "secondary",
            "review_status": "reviewed",
            "revision": "fictitious-r1",
            "position": len(manifest["catalog"]),
        }
    )
    manifest["rights"].append(
        {
            "id": "rights-inline-fiction",
            "publication_allowed": False,
            "private_preview_allowed": False,
            "personal_use_allowed": True,
            "source_serving_allowed": True,
            "download_allowed": False,
            "audience_ids": [audience_id],
            "rights_holder": None,
            "basis": "requester_private_processing",
            "reviewed_at": "2026-07-19T08:00:00Z",
            "note": "Politique exclusivement fictive pour les tests publics.",
        }
    )
    manifest["assets"].append(
        {
            "id": "asset-inline-fiction",
            "file_id": "file-inline-fiction",
            "rights_id": "rights-inline-fiction",
            "kind": "pdf",
            "audience_ids": [audience_id],
            "media_type": "application/pdf",
            "filename": "memo-fictif.pdf",
            "alt_text": "[FICTIF] Mémo consultable",
        }
    )
    manifest["sources"].append(
        {
            "id": "source-inline-fiction",
            "title": "[FICTIF] Mémo consultable",
            "audience_ids": [audience_id],
            "asset_id": "asset-inline-fiction",
            "rights_id": "rights-inline-fiction",
            "revision": "fictitious-r1",
            "pages": [{"page": 1, "label": "[FICTIF] Page unique"}],
        }
    )

    node_title_by_id = {node["id"]: node["title"] for node in manifest["catalog"]}
    excerpts = {
        "search-document-fiction": "Une relation fictive illustre une méthode de lecture accessible.",
        "search-exercise-fiction": "Un exercice inventé propose une progression guidée.",
        "search-source-fiction": "Un carnet synthétique accompagne la démonstration.",
    }
    search_payload["schema_version"] = 3
    for document in search_payload["documents"]:
        document["title"] = node_title_by_id[document["catalog_node_id"]]
        document["reader_excerpt"] = excerpts[document["id"]]
    search_payload["documents"].append(
        {
            "id": "search-source-inline-fiction",
            "catalog_node_id": "source-node-inline-fiction",
            "target_id": "source-inline-fiction",
            "audience_ids": [audience_id],
            "title": "[FICTIF] Mémo consultable",
            "body": "Une nébuleuse indexable reste réservée au corpus de recherche fictif.",
            "reader_excerpt": "Un mémo entièrement fictif complète la bibliothèque personnelle.",
        }
    )
    if search_mutator is not None:
        search_mutator(search_payload)
    search_bytes = json_bytes(search_payload)
    manifest["search_index"]["document_count"] = len(search_payload["documents"])
    manifest["checksums"] = [
        checksum
        for checksum in manifest["checksums"]
        if checksum["file_id"] != manifest["search_index"]["file_id"]
    ]
    manifest["checksums"].extend(
        [
            _checksum("file-inline-fiction", "assets/source-inline-fiction.bin", SECOND_SOURCE_BYTES),
            _checksum("file-search-fiction", "search/index.json", search_bytes),
        ]
    )
    if manifest_mutator is not None:
        manifest_mutator(manifest)

    (release / "assets" / "source-inline-fiction.bin").write_bytes(SECOND_SOURCE_BYTES)
    (release / "search" / "index.json").write_bytes(search_bytes)
    (release / "manifest.json").write_bytes(json_bytes(manifest))
    return release


def _checksum(file_id: str, path: str, payload: bytes) -> BundleDict:
    return {
        "file_id": file_id,
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _catalog_nodes(release_marker: str, audience_id: str) -> list[BundleDict]:
    hierarchy = [
        ("audience", "catalog-audience", None),
        ("curriculum", "curriculum-fip", "catalog-audience"),
        ("promotion", "promotion-2028", "curriculum-fip"),
        ("level", "level-2a", "promotion-2028"),
        ("semester", "semester-s7", "level-2a"),
        ("ue", "ue-fiction", "semester-s7"),
        ("module", "module-fiction", "ue-fiction"),
        ("chapter", "chapter-fiction", "module-fiction"),
        ("concept", "concept-fiction", "chapter-fiction"),
        ("lesson", "lesson-fiction", "chapter-fiction"),
        ("exercise", "exercise-fiction", "chapter-fiction"),
        ("pc_td", "pc-td-fiction", "module-fiction"),
        ("past_exam", "past-exam-fiction", "ue-fiction"),
        ("source", "source-node-fiction", "chapter-fiction"),
    ]
    return [
        {
            "id": node_id,
            "kind": kind,
            "title": f"[FICTIF] {kind} {release_marker}",
            "audience_ids": [audience_id],
            "parent_id": parent_id,
            "content_id": (
                "content-fiction"
                if kind == "lesson"
                else "concept-content-fiction"
                if kind == "concept"
                else "exercise-content-fiction"
                if kind == "exercise"
                else None
            ),
            "source_id": "source-fiction" if kind == "source" else None,
            "prerequisite_ids": [],
            "difficulty": "standard" if kind in {"lesson", "exercise"} else None,
            "estimated_minutes": 15 if kind in {"lesson", "exercise"} else None,
            "review_status": "published",
            "revision": "fictitious-r1",
            "position": position,
        }
        for position, (kind, node_id, parent_id) in enumerate(hierarchy)
    ]


def _manifest(release_id: str, search_bytes: bytes, audience_id: str) -> BundleDict:
    return {
        "schema_version": 1,
        "release_id": release_id,
        "generated_at": "2026-07-19T08:00:00Z",
        "audiences": [
            {
                "id": audience_id,
                "label": "[FICTIF] FIP 2028",
                "curriculum": "FIP fictive",
                "promotion": "2028 fictive",
                "level_label": "[FICTIF] 2A",
            }
        ],
        "catalog": _catalog_nodes(release_id, audience_id),
        "content": [
            {
                "id": "content-fiction",
                "frontmatter": {
                    "catalog_node_id": "lesson-fiction",
                    "title": f"[FICTIF] Leçon {release_id}",
                    "audience_ids": [audience_id],
                    "review_status": "published",
                    "revision": "fictitious-r1",
                    "prerequisite_ids": ["concept-fiction"],
                    "difficulty": "standard",
                    "estimated_minutes": 15,
                },
                "blocks": [
                    {
                        "type": "heading",
                        "id": "section-demonstration",
                        "level": 2,
                        "inlines": [{"type": "text", "text": "[FICTIF] Démonstration"}],
                    },
                    {
                        "type": "paragraph",
                        "inlines": [
                            {"type": "text", "text": "Équation entièrement fictive. "},
                            {
                                "type": "source_ref",
                                "id": "reference-source-fiction",
                                "source_id": "source-fiction",
                                "page": 1,
                                "end_page": None,
                                "label": "page fictive",
                            },
                            {
                                "type": "concept_ref",
                                "concept_id": "concept-fiction",
                                "label": "concept fictif",
                            },
                            {
                                "type": "exercise_ref",
                                "exercise_id": "exercise-fiction",
                                "label": "exercice fictif",
                            },
                        ],
                    },
                    {
                        "type": "image",
                        "asset_id": "asset-image-fiction",
                        "alt_text": "[FICTIF] Schéma de démonstration",
                        "caption": "Illustration technique sans donnée réelle.",
                    },
                    {
                        "type": "directive",
                        "id": "hint-fiction-one",
                        "name": "hint",
                        "title": "[FICTIF] Indice progressif",
                        "inlines": [{"type": "text", "text": "Indice sans donnée réelle."}],
                    },
                ],
            },
            {
                "id": "concept-content-fiction",
                "frontmatter": {
                    "catalog_node_id": "concept-fiction",
                    "title": "[FICTIF] Concept expliqué",
                    "audience_ids": [audience_id],
                    "review_status": "published",
                    "revision": "fictitious-r1",
                    "prerequisite_ids": [],
                    "difficulty": "introductory",
                    "estimated_minutes": 5,
                },
                "blocks": [
                    {
                        "type": "heading",
                        "id": "concept-section-fiction",
                        "level": 2,
                        "inlines": [{"type": "text", "text": "[FICTIF] Concept"}],
                    },
                    {
                        "type": "paragraph",
                        "inlines": [{"type": "text", "text": "Définition entièrement fictive."}],
                    },
                ],
            },
            {
                "id": "exercise-content-fiction",
                "frontmatter": {
                    "catalog_node_id": "exercise-fiction",
                    "title": "[FICTIF] Exercice guidé",
                    "audience_ids": [audience_id],
                    "review_status": "published",
                    "revision": "fictitious-r1",
                    "prerequisite_ids": ["concept-fiction"],
                    "difficulty": "standard",
                    "estimated_minutes": 20,
                },
                "blocks": [
                    {
                        "type": "heading",
                        "id": "exercise-section-fiction",
                        "level": 2,
                        "inlines": [{"type": "text", "text": "[FICTIF] Énoncé guidé"}],
                    },
                    {
                        "type": "paragraph",
                        "inlines": [{"type": "text", "text": "Question de démonstration fictive."}],
                    },
                    {
                        "type": "directive",
                        "id": "exercise-hint-fiction-one",
                        "name": "hint",
                        "title": "[FICTIF] Indice progressif",
                        "inlines": [{"type": "text", "text": "Indice sans donnée réelle."}],
                    },
                ],
            },
        ],
        "assets": [
            {
                "id": "asset-source-fiction",
                "file_id": "file-source-fiction",
                "rights_id": "rights-fiction",
                "kind": "pdf",
                "audience_ids": [audience_id],
                "media_type": "application/pdf",
                "filename": "source-fictive.pdf",
                "alt_text": "[FICTIF] Source de test",
            },
            {
                "id": "asset-image-fiction",
                "file_id": "file-image-fiction",
                "rights_id": "rights-fiction",
                "kind": "image",
                "audience_ids": [audience_id],
                "media_type": "image/png",
                "filename": "illustration-fictive.png",
                "alt_text": "[FICTIF] Schéma de démonstration",
            },
        ],
        "sources": [
            {
                "id": "source-fiction",
                "title": "[FICTIF] Source de démonstration",
                "audience_ids": [audience_id],
                "asset_id": "asset-source-fiction",
                "rights_id": "rights-fiction",
                "revision": "fictitious-r1",
                "pages": [{"page": 1, "label": "[FICTIF] Page unique"}],
            }
        ],
        "rights": [
            {
                "id": "rights-fiction",
                "publication_allowed": True,
                "audience_ids": [audience_id],
                "rights_holder": "[FICTIF] Générateur de tests",
                "basis": "fictitious",
                "reviewed_at": "2026-07-19T08:00:00Z",
                "note": "Métadonnée exclusivement fictive.",
            }
        ],
        "search_index": {
            "file_id": "file-search-fiction",
            "format": "json-v1",
            "language": "fr",
            "revision": "fictitious-r1",
            "document_count": 3,
        },
        "checksums": [
            _checksum("file-source-fiction", "assets/source-fiction.bin", SOURCE_BYTES),
            _checksum("file-image-fiction", "assets/image-fiction.bin", IMAGE_BYTES),
            _checksum("file-search-fiction", "search/index.json", search_bytes),
        ],
    }
