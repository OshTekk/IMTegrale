from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.learning import bundle as learning_bundle_module
from app.learning.bundle import (
    LearningCatalogUnavailable,
    get_learning_bundle,
    load_learning_bundle,
    reset_learning_bundle_cache,
)
from app.learning.schemas import LearningBundleManifest

from tests.learning_bundle_factory import (
    AUDIENCE_ID,
    CATALOG_KINDS,
    SOURCE_BYTES,
    write_fictitious_learning_bundle,
    write_fictitious_metadata_only_preview_bundle,
)

SECOND_AUDIENCE_ID = "fip:2029"


def _add_second_fictitious_audience(manifest) -> None:
    manifest["audiences"].append(
        {
            "id": SECOND_AUDIENCE_ID,
            "label": "[FICTIF] FIP 2029",
            "curriculum": "FIP fictive",
            "promotion": "2029 fictive",
            "level_label": "[FICTIF] 2A",
        }
    )


def _foreign_catalog_node(node_id: str, kind: str = "concept") -> dict[str, object]:
    return {
        "id": node_id,
        "kind": kind,
        "title": "[FICTIF] Nœud d'une autre audience",
        "audience_ids": [SECOND_AUDIENCE_ID],
        "parent_id": None,
        "content_id": None,
        "source_id": None,
        "prerequisite_ids": [],
        "difficulty": None,
        "estimated_minutes": None,
        "review_status": "published",
        "revision": "fictitious-r1",
        "position": 999,
    }


def _move_exercise_manifest_to_second_audience(manifest) -> None:
    _add_second_fictitious_audience(manifest)
    exercise_node = next(node for node in manifest["catalog"] if node["id"] == "exercise-fiction")
    exercise_node["audience_ids"] = [SECOND_AUDIENCE_ID]
    exercise_node["parent_id"] = None
    exercise_content = next(
        content for content in manifest["content"] if content["id"] == "exercise-content-fiction"
    )
    exercise_content["frontmatter"]["audience_ids"] = [SECOND_AUDIENCE_ID]
    exercise_content["frontmatter"]["prerequisite_ids"] = []


def _move_exercise_search_to_second_audience(search) -> None:
    document = next(item for item in search["documents"] if item["id"] == "search-exercise-fiction")
    document["audience_ids"] = [SECOND_AUDIENCE_ID]


def _move_source_chain_manifest_to_second_audience(manifest) -> None:
    _add_second_fictitious_audience(manifest)
    source_node = next(node for node in manifest["catalog"] if node["id"] == "source-node-fiction")
    source_node["audience_ids"] = [SECOND_AUDIENCE_ID]
    source_node["parent_id"] = None
    source = manifest["sources"][0]
    source["audience_ids"] = [SECOND_AUDIENCE_ID]
    source["rights_id"] = "rights-source-fiction"
    source_asset = next(asset for asset in manifest["assets"] if asset["id"] == "asset-source-fiction")
    source_asset["audience_ids"] = [SECOND_AUDIENCE_ID]
    source_asset["rights_id"] = "rights-source-fiction"
    manifest["rights"].append(
        {
            "id": "rights-source-fiction",
            "publication_allowed": True,
            "audience_ids": [SECOND_AUDIENCE_ID],
            "rights_holder": "[FICTIF] Titulaire pour autre audience",
            "basis": "fictitious",
            "reviewed_at": "2026-07-19T08:00:00Z",
            "note": "Métadonnée exclusivement fictive.",
        }
    )


def _move_source_search_to_second_audience(search) -> None:
    document = next(item for item in search["documents"] if item["id"] == "search-source-fiction")
    document["audience_ids"] = [SECOND_AUDIENCE_ID]


def _remove_inline_reference(manifest, reference_type: str) -> None:
    lesson = next(content for content in manifest["content"] if content["id"] == "content-fiction")
    paragraph = next(block for block in lesson["blocks"] if block["type"] == "paragraph")
    paragraph["inlines"] = [inline for inline in paragraph["inlines"] if inline["type"] != reference_type]


def _assert_public_unavailable(call, *private_values: str) -> None:
    with pytest.raises(LearningCatalogUnavailable) as captured:
        call()
    assert str(captured.value) == "LEARNING_CATALOG_UNAVAILABLE"
    assert captured.value.code == "LEARNING_CATALOG_UNAVAILABLE"
    for private_value in private_values:
        assert private_value not in str(captured.value)


def test_loads_fictitious_direct_release_and_resolves_only_by_id(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(tmp_path)

    snapshot = load_learning_bundle(release)

    assert snapshot.release_id == "fictitious-release-a"
    assert snapshot.manifest.release_mode == "published"
    assert snapshot.catalog_version == snapshot.release_id
    assert LearningBundleManifest.model_validate_json(snapshot.manifest.model_dump_json())
    assert str(release) not in repr(snapshot)
    assert "[FICTIF]" not in repr(snapshot)
    assert {node.kind for node in snapshot.catalog_for_audience(AUDIENCE_ID)} == CATALOG_KINDS
    assert snapshot.get_content("content-fiction", AUDIENCE_ID) is not None
    assert snapshot.get_content("concept-content-fiction", AUDIENCE_ID) is not None
    assert snapshot.get_content("exercise-content-fiction", AUDIENCE_ID) is not None
    assert snapshot.get_content("content-fiction", "fip:2029") is None
    assert snapshot.get_asset("../assets/source-fiction.bin", AUDIENCE_ID) is None
    assert snapshot.get_source("source-fiction", "fip:2029") is None
    assert snapshot.get_source("source-fiction", AUDIENCE_ID).page_count == 1
    assert snapshot.get_rights("rights-fiction", AUDIENCE_ID).publication_allowed is True
    search_result = snapshot.search(AUDIENCE_ID, "equation fictive")[0]
    assert search_result.entity_id == "content-fiction"
    assert search_result.entity_type == "lesson"
    assert search_result.module_id == "module-fiction"
    assert "body" not in search_result.model_dump()
    exercise_result = snapshot.search(
        AUDIENCE_ID,
        "fictif",
        entity_types=("exercise",),
    )
    assert [result.entity_id for result in exercise_result] == ["exercise-content-fiction"]
    source_result = snapshot.search(AUDIENCE_ID, "source page", entity_types=("source",))
    assert [result.entity_id for result in source_result] == ["source-fiction"]
    assert source_result[0].catalog_node_id == "source-node-fiction"
    with snapshot.open_source("source-fiction", AUDIENCE_ID).stream as stream:
        assert stream.read() == SOURCE_BYTES
    with pytest.raises(KeyError):
        snapshot.open_asset("../assets/source-fiction.bin", AUDIENCE_ID)


@pytest.mark.parametrize("omit_source_asset_id", [False, True])
def test_metadata_only_source_loads_only_with_explicit_fail_closed_preview_rights(
    tmp_path: Path,
    omit_source_asset_id: bool,
) -> None:
    release = write_fictitious_metadata_only_preview_bundle(
        tmp_path,
        omit_source_asset_id=omit_source_asset_id,
    )

    snapshot = load_learning_bundle(release)
    source = snapshot.get_source("source-fiction", "personal:fictive-owner")
    rights = snapshot.get_rights("rights-fiction", "personal:fictive-owner")

    assert snapshot.manifest.release_mode == "private_preview"
    assert source is not None
    assert source.asset_id is None
    assert source.page_count == 1
    assert rights is not None
    assert rights.publication_allowed is False
    assert rights.private_preview_allowed is True
    assert rights.source_serving_allowed is False
    assert rights.rights_holder is None
    assert rights.basis == "requester_private_processing"
    assert snapshot.get_source_asset("source-fiction", "personal:fictive-owner") is None
    assert snapshot.get_asset("asset-source-fiction", "personal:fictive-owner") is None
    assert [
        result.entity_id
        for result in snapshot.search(
            "personal:fictive-owner",
            "source page",
            entity_types=("source",),
        )
    ] == ["source-fiction"]
    with pytest.raises(KeyError):
        snapshot.open_source("source-fiction", "personal:fictive-owner")


def test_published_bundle_can_keep_citation_metadata_without_serving_source(
    tmp_path: Path,
) -> None:
    def publish_metadata_only(manifest) -> None:
        manifest["release_mode"] = "published"
        for node in manifest["catalog"]:
            node["review_status"] = "published"
        for document in manifest["content"]:
            document["frontmatter"]["review_status"] = "published"
        rights = manifest["rights"][0]
        rights["publication_allowed"] = True
        rights["private_preview_allowed"] = False
        rights["rights_holder"] = "[FICTIF] Générateur de tests"
        rights["basis"] = "fictitious"

    release = write_fictitious_metadata_only_preview_bundle(
        tmp_path,
        manifest_mutator=publish_metadata_only,
    )

    snapshot = load_learning_bundle(release)

    assert snapshot.manifest.release_mode == "published"
    assert snapshot.get_source("source-fiction", "personal:fictive-owner") is not None
    assert snapshot.get_source_asset("source-fiction", "personal:fictive-owner") is None
    with pytest.raises(KeyError):
        snapshot.open_source("source-fiction", "personal:fictive-owner")


@pytest.mark.parametrize("policy_state", ["omitted", "allowed"])
def test_metadata_only_source_requires_explicit_source_serving_denial(
    tmp_path: Path,
    policy_state: str,
) -> None:
    def mutate(manifest) -> None:
        rights = manifest["rights"][0]
        if policy_state == "omitted":
            rights.pop("source_serving_allowed")
        else:
            rights["source_serving_allowed"] = True

    release = write_fictitious_metadata_only_preview_bundle(
        tmp_path,
        manifest_mutator=mutate,
    )

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_non_servable_source_cannot_keep_a_downloadable_asset(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        manifest["rights"][0]["source_serving_allowed"] = False

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize(
    ("invalid_field", "invalid_value"),
    [
        ("publication_allowed", True),
        ("private_preview_allowed", False),
        ("source_serving_allowed", True),
    ],
)
def test_private_preview_rights_are_strictly_fail_closed(
    tmp_path: Path,
    invalid_field: str,
    invalid_value: bool,
) -> None:
    def mutate(manifest) -> None:
        manifest["rights"][0][invalid_field] = invalid_value

    release = write_fictitious_metadata_only_preview_bundle(
        tmp_path,
        manifest_mutator=mutate,
    )

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize("invalid_basis_state", ["asserted-holder", "unsupported-null-holder"])
def test_private_processing_basis_never_asserts_unestablished_rights(
    tmp_path: Path,
    invalid_basis_state: str,
) -> None:
    def mutate(manifest) -> None:
        rights = manifest["rights"][0]
        if invalid_basis_state == "asserted-holder":
            rights["rights_holder"] = "[FICTIF] Titulaire non établi"
        else:
            rights["basis"] = "permission"

    release = write_fictitious_metadata_only_preview_bundle(
        tmp_path,
        manifest_mutator=mutate,
    )

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_requester_private_processing_basis_is_forbidden_in_published_release(
    tmp_path: Path,
) -> None:
    def mutate(manifest) -> None:
        rights = manifest["rights"][0]
        rights["basis"] = "requester_private_processing"
        rights["rights_holder"] = None

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_private_preview_requires_only_personal_audiences(tmp_path: Path) -> None:
    release = write_fictitious_metadata_only_preview_bundle(
        tmp_path,
        audience_id=AUDIENCE_ID,
    )

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_disabled_or_missing_root_is_safely_unavailable(tmp_path: Path) -> None:
    reset_learning_bundle_cache()
    settings = SimpleNamespace(learning_enabled=False, learning_content_root=tmp_path / "private-name")
    _assert_public_unavailable(lambda: get_learning_bundle(settings), "private-name")
    _assert_public_unavailable(lambda: load_learning_bundle(None))
    _assert_public_unavailable(lambda: load_learning_bundle(tmp_path / "missing-private"), "missing-private")


def test_service_requires_atomic_root_outside_test_environment(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    settings = SimpleNamespace(environment="production", learning_content_root=release)

    _assert_public_unavailable(lambda: get_learning_bundle(settings), str(release))


@pytest.mark.parametrize("bad_path", ["../outside.bin", "/private/outside.bin", "assets/./file.bin"])
def test_manifest_paths_cannot_traverse(tmp_path: Path, bad_path: str) -> None:
    def mutate(manifest) -> None:
        manifest["checksums"][0]["path"] = bad_path

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), bad_path, str(release))


def test_unknown_manifest_fields_are_refused_without_leaking_details(tmp_path: Path) -> None:
    private_marker = "PRIVATE-FILENAME-SHOULD-NOT-LEAK.pdf"

    def mutate(manifest) -> None:
        manifest["unknown_private_entry"] = private_marker

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), private_marker, str(release))


def test_duplicate_manifest_keys_are_refused(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    manifest_path = release / "manifest.json"
    payload = manifest_path.read_bytes()
    manifest_path.write_bytes(payload.replace(b'{"assets":', b'{"schema_version":1,"assets":', 1))

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize("invalid_version", [True, False, 1.0, "1", 0, 2])
@pytest.mark.parametrize("version_location", ["manifest", "search"])
def test_schema_versions_require_the_strict_integer_one(
    tmp_path: Path,
    version_location: str,
    invalid_version: object,
) -> None:
    def mutate_manifest(manifest) -> None:
        manifest["schema_version"] = invalid_version

    def mutate_search(search) -> None:
        search["schema_version"] = invalid_version

    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=mutate_manifest if version_location == "manifest" else None,
        search_mutator=mutate_search if version_location == "search" else None,
    )

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_invalid_checksum_and_undeclared_files_are_refused(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        manifest["checksums"][0]["sha256"] = "0" * 64

    bad_checksum_release = write_fictitious_learning_bundle(
        tmp_path,
        "fictitious-bad-checksum",
        manifest_mutator=mutate,
    )
    _assert_public_unavailable(lambda: load_learning_bundle(bad_checksum_release), str(bad_checksum_release))

    extra_file_release = write_fictitious_learning_bundle(tmp_path, "fictitious-extra-file")
    (extra_file_release / "private-catalog-name.txt").write_text("fictitious", encoding="utf-8")
    _assert_public_unavailable(
        lambda: load_learning_bundle(extra_file_release),
        "private-catalog-name.txt",
        str(extra_file_release),
    )


def test_invalid_immutable_release_is_negatively_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_learning_bundle_cache()

    def mutate(manifest) -> None:
        manifest["checksums"][0]["sha256"] = "0" * 64

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    settings = SimpleNamespace(environment="test", learning_content_root=release)
    original_load = learning_bundle_module._load_snapshot
    calls = 0

    def counted_load(location):
        nonlocal calls
        calls += 1
        return original_load(location)

    monkeypatch.setattr(learning_bundle_module, "_load_snapshot", counted_load)
    _assert_public_unavailable(lambda: get_learning_bundle(settings))
    _assert_public_unavailable(lambda: get_learning_bundle(settings))

    assert calls == 1


def test_internal_and_external_file_symlinks_are_refused(tmp_path: Path) -> None:
    internal_release = write_fictitious_learning_bundle(tmp_path, "fictitious-internal-symlink")
    internal_asset = internal_release / "assets" / "source-fiction.bin"
    internal_asset.unlink()
    internal_asset.symlink_to(Path("../search/index.json"))
    _assert_public_unavailable(lambda: load_learning_bundle(internal_release), str(internal_release))

    external_release = write_fictitious_learning_bundle(tmp_path, "fictitious-external-symlink")
    outside = tmp_path / "outside-private-name.bin"
    outside.write_bytes(SOURCE_BYTES)
    external_asset = external_release / "assets" / "source-fiction.bin"
    external_asset.unlink()
    external_asset.symlink_to(outside)
    _assert_public_unavailable(
        lambda: load_learning_bundle(external_release),
        str(outside),
        str(external_release),
    )


def test_hardlinked_bundle_files_are_refused(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    outside = tmp_path / "outside-hardlink-private-name.bin"
    outside.write_bytes(SOURCE_BYTES)
    asset = release / "assets" / "source-fiction.bin"
    asset.unlink()
    os.link(outside, asset)

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(outside), str(release))


def test_current_must_point_to_direct_internal_release(tmp_path: Path) -> None:
    root = tmp_path / "content-root"
    (root / "releases").mkdir(parents=True)
    outside_release = write_fictitious_learning_bundle(tmp_path / "outside", "fictitious-outside")
    (root / "current").symlink_to(outside_release)

    _assert_public_unavailable(lambda: load_learning_bundle(root), str(outside_release), str(root))


def test_current_switch_keeps_old_snapshot_coherent_and_loads_new_release(tmp_path: Path) -> None:
    reset_learning_bundle_cache()
    root = tmp_path / "content-root"
    releases = root / "releases"
    release_a = write_fictitious_learning_bundle(releases, "fictitious-release-a")
    write_fictitious_learning_bundle(releases, "fictitious-release-b")
    current = root / "current"
    current.symlink_to(Path("releases/fictitious-release-a"))
    settings = SimpleNamespace(learning_enabled=True, learning_content_root=root)

    snapshot_a = get_learning_bundle(settings)
    replacement = root / "current.next"
    replacement.symlink_to(Path("releases/fictitious-release-b"))
    os.replace(replacement, current)
    snapshot_b = get_learning_bundle(settings)

    assert snapshot_a.release_id == release_a.name
    assert snapshot_b.release_id == "fictitious-release-b"
    assert snapshot_a.get_content("content-fiction", AUDIENCE_ID).frontmatter.title.endswith(
        "fictitious-release-a"
    )
    assert snapshot_b.get_content("content-fiction", AUDIENCE_ID).frontmatter.title.endswith(
        "fictitious-release-b"
    )
    with snapshot_a.open_asset("asset-source-fiction", AUDIENCE_ID).stream as stream:
        assert stream.read() == SOURCE_BYTES


@pytest.mark.parametrize(
    "reference_update",
    [
        {"source_id": "missing-private-source"},
        {"page": 2},
        {"concept_id": "missing-private-concept"},
        {"exercise_id": "missing-private-exercise"},
    ],
)
def test_unresolved_content_references_and_pages_are_refused(
    tmp_path: Path,
    reference_update: dict[str, object],
) -> None:
    def mutate(manifest) -> None:
        blocks = manifest["content"][0]["blocks"]
        paragraph = next(block for block in blocks if block["type"] == "paragraph")
        key = next(iter(reference_update))
        target = next(inline for inline in paragraph["inlines"] if key in inline)
        target.update(reference_update)

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    private_value = str(next(iter(reference_update.values())))
    _assert_public_unavailable(lambda: load_learning_bundle(release), private_value, str(release))


def test_rights_must_explicitly_allow_the_declared_audience(tmp_path: Path) -> None:
    def forbid_publication(manifest) -> None:
        manifest["rights"][0]["publication_allowed"] = False

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=forbid_publication)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize("asset_id", ["missing-private-image", "asset-source-fiction"])
def test_image_block_requires_a_resolved_image_asset(tmp_path: Path, asset_id: str) -> None:
    def mutate(manifest) -> None:
        blocks = manifest["content"][0]["blocks"]
        image = next(block for block in blocks if block["type"] == "image")
        image["asset_id"] = asset_id

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), asset_id, str(release))


def test_image_block_cannot_cross_audience_boundary(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        manifest["audiences"].append(
            {
                "id": "fip:2029",
                "label": "[FICTIF] FIP 2029",
                "curriculum": "FIP fictive",
                "promotion": "2029 fictive",
                "level_label": "[FICTIF] 2A",
            }
        )
        image = next(asset for asset in manifest["assets"] if asset["id"] == "asset-image-fiction")
        image["audience_ids"] = ["fip:2029"]
        manifest["rights"][0]["audience_ids"] = [AUDIENCE_ID, "fip:2029"]

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_unreferenced_assets_are_refused(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        manifest["content"][0]["blocks"] = [
            block for block in manifest["content"][0]["blocks"] if block["type"] != "image"
        ]

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize("duplicate_kind", ["heading", "directive", "source_ref"])
def test_content_interaction_ids_must_be_unique(tmp_path: Path, duplicate_kind: str) -> None:
    def mutate(manifest) -> None:
        blocks = manifest["content"][0]["blocks"]
        heading = next(block for block in blocks if block["type"] == "heading")
        if duplicate_kind == "heading":
            blocks.insert(1, {**heading})
        elif duplicate_kind == "directive":
            directive = next(block for block in blocks if block["type"] == "directive")
            blocks.append({**directive})
        else:
            paragraph = next(block for block in blocks if block["type"] == "paragraph")
            source_ref = next(inline for inline in paragraph["inlines"] if inline["type"] == "source_ref")
            paragraph["inlines"].append({**source_ref})

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "<script>secret()</script>",
        "https://private.invalid/doc",
        "ftp://private.invalid/doc",
        "https:",
        "javascript:alert(1)",
        "javascript: alert(1)",
        "javascript:\nalert(1)",
        "data:text/plain,fictitious",
        "data: text/plain,fictitious",
        "mailto:student@example.test",
        "mailto: student@example.test",
        "custom+scheme:private-value",
        f"{'a' * 64}:private-value",
        "://private.invalid/doc",
        "//private.invalid/doc",
        "/private/doc",
        "www.private.invalid/doc",
    ],
)
def test_content_ast_refuses_raw_html_and_external_urls(tmp_path: Path, unsafe_text: str) -> None:
    def mutate(manifest) -> None:
        manifest["content"][0]["blocks"][0]["inlines"][0]["text"] = unsafe_text

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), unsafe_text, str(release))


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "https://private.invalid/doc",
        "ftp://private.invalid/doc",
        "https:",
        "javascript:alert(1)",
        "javascript: alert(1)",
        "javascript:\nalert(1)",
        "data:text/plain,fictitious",
        "data: text/plain,fictitious",
        "mailto:student@example.test",
        "mailto: student@example.test",
        "custom+scheme:private-value",
        f"{'a' * 64}:private-value",
        "://private.invalid/doc",
        "//private.invalid/doc",
        "/private/doc",
        "www.private.invalid/doc",
    ],
)
def test_search_document_refuses_uri_schemes_and_network_urls(
    tmp_path: Path,
    unsafe_text: str,
) -> None:
    def mutate_search(search) -> None:
        search["documents"][0]["body"] = unsafe_text

    release = write_fictitious_learning_bundle(tmp_path, search_mutator=mutate_search)
    _assert_public_unavailable(lambda: load_learning_bundle(release), unsafe_text, str(release))


def test_adjacent_text_inlines_cannot_reassemble_an_external_url(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        manifest["content"][0]["blocks"][0]["inlines"] = [
            {"type": "text", "text": "java"},
            {"type": "text", "text": "script"},
            {"type": "text", "text": ":alert(1)"},
        ]

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_math_and_code_keep_legitimate_non_uri_notation(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        manifest["content"][0]["blocks"].extend(
            [
                {
                    "type": "code",
                    "language": "python",
                    "code": "ratio = left / right\nfloor = left//right\nmapping = f:x",
                },
                {
                    "type": "math",
                    "latex": r"f: x \\mapsto x / 2",
                },
            ]
        )

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)

    assert load_learning_bundle(release).release_id == release.name


@pytest.mark.parametrize(
    ("block_type", "field", "unsafe_text"),
    [
        ("code", "code", "javascript: alert(1)"),
        ("code", "code", "custom+scheme://private.invalid"),
        ("math", "latex", r"https://private.invalid/value"),
        ("math", "latex", r"//private.invalid/value"),
    ],
)
def test_math_and_code_refuse_active_or_network_uris(
    tmp_path: Path,
    block_type: str,
    field: str,
    unsafe_text: str,
) -> None:
    def mutate(manifest) -> None:
        block = {"type": block_type, field: unsafe_text}
        if block_type == "code":
            block["language"] = "text"
        manifest["content"][0]["blocks"].append(block)

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), unsafe_text, str(release))


def test_search_index_is_strict_and_must_match_manifest_refs(tmp_path: Path) -> None:
    def mutate_search(search) -> None:
        search["documents"][0]["target_id"] = "missing-private-content"

    release = write_fictitious_learning_bundle(tmp_path, search_mutator=mutate_search)
    _assert_public_unavailable(lambda: load_learning_bundle(release), "missing-private-content", str(release))


def test_search_index_revision_must_match_manifest(tmp_path: Path) -> None:
    def mutate_search(search) -> None:
        search["revision"] = "stale-private-revision"

    release = write_fictitious_learning_bundle(tmp_path, search_mutator=mutate_search)
    _assert_public_unavailable(lambda: load_learning_bundle(release), "stale-private-revision")


def test_search_index_refuses_blank_display_text(tmp_path: Path) -> None:
    def mutate_search(search) -> None:
        search["documents"][0]["body"] = "   "

    release = write_fictitious_learning_bundle(tmp_path, search_mutator=mutate_search)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_search_normalizes_ligatures_accents_and_bounds_terms(tmp_path: Path) -> None:
    def mutate_search(search) -> None:
        search["documents"][0]["body"] = "Cœur algèbre alpha bravo charlie delta echo foxtrot golf hotel."

    release = write_fictitious_learning_bundle(tmp_path, search_mutator=mutate_search)
    snapshot = load_learning_bundle(release)

    assert snapshot.search(AUDIENCE_ID, "coeur, algèbre")[0].entity_id == "content-fiction"
    assert snapshot.search(
        AUDIENCE_ID,
        "coeur alpha bravo charlie delta echo foxtrot golf absent-ninth",
    )
    with pytest.raises(ValueError):
        snapshot.search(AUDIENCE_ID, "x" * 121)


def test_search_document_count_is_bounded(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        manifest["search_index"]["document_count"] = 20_001

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_search_index_file_cannot_be_reused_as_a_downloadable_asset(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        source_asset = next(asset for asset in manifest["assets"] if asset["kind"] == "pdf")
        source_asset["file_id"] = manifest["search_index"]["file_id"]
        manifest["checksums"] = [
            checksum for checksum in manifest["checksums"] if checksum["file_id"] != "file-source-fiction"
        ]
        source_path = manifest["checksums"][0]["path"]
        assert source_path != "assets/source-fiction.bin"

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize(
    "review_status",
    ["draft", "in_review", "private_preview", "retired"],
)
def test_activated_release_contains_only_published_content(
    tmp_path: Path,
    review_status: str,
) -> None:
    def mutate(manifest) -> None:
        manifest["content"][0]["frontmatter"]["review_status"] = review_status

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize("target", ["catalog", "content"])
def test_private_preview_requires_preview_review_status(
    tmp_path: Path,
    target: str,
) -> None:
    def mutate(manifest) -> None:
        if target == "catalog":
            manifest["catalog"][0]["review_status"] = "published"
        else:
            manifest["content"][0]["frontmatter"]["review_status"] = "published"

    release = write_fictitious_metadata_only_preview_bundle(
        tmp_path,
        manifest_mutator=mutate,
    )

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_catalog_content_link_must_be_unique_and_bidirectional(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        concept = next(node for node in manifest["catalog"] if node["id"] == "concept-fiction")
        concept["content_id"] = "content-fiction"

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize(
    ("namespace", "colliding_id"),
    [("content", "concept-fiction"), ("sources", "lesson-fiction")],
)
def test_resolved_id_namespaces_must_be_globally_disjoint(
    tmp_path: Path,
    namespace: str,
    colliding_id: str,
) -> None:
    def mutate(manifest) -> None:
        manifest[namespace][0]["id"] = colliding_id

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), colliding_id, str(release))


@pytest.mark.parametrize("structural_kind", ["module", "source"])
def test_structural_and_source_nodes_cannot_link_content(
    tmp_path: Path,
    structural_kind: str,
) -> None:
    def mutate(manifest) -> None:
        node = next(item for item in manifest["catalog"] if item["kind"] == structural_kind)
        node["content_id"] = "structural-content-fiction"
        node["source_id"] = None
        manifest["content"].append(
            {
                "id": "structural-content-fiction",
                "frontmatter": {
                    "catalog_node_id": node["id"],
                    "title": "[FICTIF] Contenu structurel interdit",
                    "audience_ids": [AUDIENCE_ID],
                    "review_status": "published",
                    "revision": "fictitious-r1",
                    "prerequisite_ids": [],
                    "difficulty": None,
                    "estimated_minutes": None,
                },
                "blocks": [
                    {
                        "type": "heading",
                        "id": "structural-section-fiction",
                        "level": 2,
                        "inlines": [{"type": "text", "text": "[FICTIF] Structure"}],
                    }
                ],
            }
        )

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_catalog_cycle_check_handles_a_long_chain_in_linear_passes() -> None:
    nodes = {
        f"node-{index}": SimpleNamespace(parent_id=f"node-{index - 1}" if index else None)
        for index in range(8_000)
    }

    LearningBundleManifest._reject_parent_cycles(nodes)
    nodes["node-0"].parent_id = "node-7999"
    with pytest.raises(ValueError, match="cycle"):
        LearningBundleManifest._reject_parent_cycles(nodes)


def test_open_asset_detects_release_mutation_after_validation(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    snapshot = load_learning_bundle(release)
    asset_path = release / "assets" / "source-fiction.bin"
    asset_path.write_bytes(SOURCE_BYTES + b"tampered")

    _assert_public_unavailable(
        lambda: snapshot.open_asset("asset-source-fiction", AUDIENCE_ID),
        str(asset_path),
    )


def test_open_asset_closes_descriptor_when_revalidation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    snapshot = load_learning_bundle(release)
    descriptor = os.open(release / "assets" / "source-fiction.bin", os.O_RDONLY)
    closed: list[int] = []
    real_close = os.close

    monkeypatch.setattr(learning_bundle_module, "_open_release_file", lambda *_args: descriptor)

    def record_close(value: int) -> None:
        closed.append(value)
        real_close(value)

    monkeypatch.setattr(learning_bundle_module, "_identity_from_stat", lambda _value: object())
    monkeypatch.setattr(learning_bundle_module.os, "close", record_close)

    _assert_public_unavailable(lambda: snapshot.open_asset("asset-source-fiction", AUDIENCE_ID))
    assert descriptor in closed


def test_open_asset_refuses_fifo_without_blocking(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    snapshot = load_learning_bundle(release)
    asset_path = release / "assets" / "source-fiction.bin"
    asset_path.unlink()
    os.mkfifo(asset_path)

    _assert_public_unavailable(
        lambda: snapshot.open_asset("asset-source-fiction", AUDIENCE_ID),
        str(asset_path),
    )


def test_open_asset_rechecks_ctime_when_size_and_mtime_are_restored(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    snapshot = load_learning_bundle(release)
    asset_path = release / "assets" / "source-fiction.bin"
    original_stat = asset_path.stat()
    asset_path.write_bytes(b"X" * len(SOURCE_BYTES))
    os.utime(
        asset_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )

    _assert_public_unavailable(
        lambda: snapshot.open_asset("asset-source-fiction", AUDIENCE_ID),
        str(asset_path),
    )


def test_snapshot_open_is_anchored_when_releases_component_is_replaced(tmp_path: Path) -> None:
    root = tmp_path / "content-root"
    release = write_fictitious_learning_bundle(root / "releases", "fictitious-release-a")
    (root / "current").symlink_to(Path("releases/fictitious-release-a"))
    snapshot = load_learning_bundle(root)

    held_releases = root / "held-releases"
    os.rename(root / "releases", held_releases)
    outside_releases = tmp_path / "outside-releases"
    write_fictitious_learning_bundle(outside_releases, release.name)
    (root / "releases").symlink_to(outside_releases)

    _assert_public_unavailable(
        lambda: snapshot.open_asset("asset-source-fiction", AUDIENCE_ID),
        str(outside_releases),
    )


def test_cross_audience_catalog_parent_is_refused(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        _add_second_fictitious_audience(manifest)
        manifest["catalog"].append(_foreign_catalog_node("foreign-parent-fiction", "chapter"))
        child = next(node for node in manifest["catalog"] if node["id"] == "pc-td-fiction")
        child["parent_id"] = "foreign-parent-fiction"

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_cross_audience_catalog_prerequisite_is_refused(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        _add_second_fictitious_audience(manifest)
        manifest["catalog"].append(_foreign_catalog_node("foreign-prerequisite-fiction"))
        node = next(item for item in manifest["catalog"] if item["id"] == "pc-td-fiction")
        node["prerequisite_ids"] = ["foreign-prerequisite-fiction"]

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_cross_audience_frontmatter_prerequisite_is_refused(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        _add_second_fictitious_audience(manifest)
        manifest["catalog"].append(_foreign_catalog_node("foreign-prerequisite-fiction"))
        lesson = next(item for item in manifest["content"] if item["id"] == "content-fiction")
        lesson["frontmatter"]["prerequisite_ids"] = ["foreign-prerequisite-fiction"]

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_cross_audience_source_reference_is_refused(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=_move_source_chain_manifest_to_second_audience,
        search_mutator=_move_source_search_to_second_audience,
    )

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_cross_audience_concept_reference_is_refused(tmp_path: Path) -> None:
    def mutate(manifest) -> None:
        _add_second_fictitious_audience(manifest)
        concept_node = next(node for node in manifest["catalog"] if node["id"] == "concept-fiction")
        concept_node["audience_ids"] = [SECOND_AUDIENCE_ID]
        concept_node["parent_id"] = None
        concept = next(
            content for content in manifest["content"] if content["id"] == "concept-content-fiction"
        )
        concept["frontmatter"]["audience_ids"] = [SECOND_AUDIENCE_ID]
        lesson = next(content for content in manifest["content"] if content["id"] == "content-fiction")
        lesson["frontmatter"]["prerequisite_ids"] = []

    release = write_fictitious_learning_bundle(tmp_path, manifest_mutator=mutate)
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_cross_audience_exercise_reference_is_refused(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=_move_exercise_manifest_to_second_audience,
        search_mutator=_move_exercise_search_to_second_audience,
    )

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_cross_audience_search_document_is_refused(tmp_path: Path) -> None:
    def mutate_manifest(manifest) -> None:
        _add_second_fictitious_audience(manifest)

    def mutate_search(search) -> None:
        search["documents"][0]["audience_ids"] = [SECOND_AUDIENCE_ID]

    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=mutate_manifest,
        search_mutator=mutate_search,
    )
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_cross_audience_search_target_is_refused(tmp_path: Path) -> None:
    def mutate_manifest(manifest) -> None:
        _move_exercise_manifest_to_second_audience(manifest)
        _remove_inline_reference(manifest, "exercise_ref")

    def mutate_search(search) -> None:
        _move_exercise_search_to_second_audience(search)
        search["documents"][0]["target_id"] = "exercise-content-fiction"

    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=mutate_manifest,
        search_mutator=mutate_search,
    )
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


@pytest.mark.parametrize("broken_link", ["source_asset", "asset_rights"])
def test_cross_audience_source_asset_rights_chain_is_refused(
    tmp_path: Path,
    broken_link: str,
) -> None:
    def mutate_manifest(manifest) -> None:
        _move_source_chain_manifest_to_second_audience(manifest)
        _remove_inline_reference(manifest, "source_ref")
        source_asset = next(asset for asset in manifest["assets"] if asset["id"] == "asset-source-fiction")
        if broken_link == "source_asset":
            source_asset["audience_ids"] = [AUDIENCE_ID]
            source_asset["rights_id"] = "rights-fiction"
        else:
            source_rights = next(
                rights for rights in manifest["rights"] if rights["id"] == "rights-source-fiction"
            )
            source_rights["audience_ids"] = [AUDIENCE_ID]

    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=mutate_manifest,
        search_mutator=_move_source_search_to_second_audience,
    )
    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))


def test_search_filters_before_top_k_with_more_than_fifty_documents(tmp_path: Path) -> None:
    def mutate_search(search) -> None:
        for index in range(55):
            search["documents"].append(
                {
                    "id": f"bulk-lesson-{index}",
                    "catalog_node_id": "lesson-fiction",
                    "target_id": "content-fiction",
                    "audience_ids": [AUDIENCE_ID],
                    "title": "[FICTIF] needle needle needle lesson",
                    "body": "needle dans une leçon fictive mieux classée.",
                }
            )
        search["documents"].append(
            {
                "id": "bulk-exercise-candidate",
                "catalog_node_id": "exercise-fiction",
                "target_id": "exercise-content-fiction",
                "audience_ids": [AUDIENCE_ID],
                "title": "[FICTIF] needle exercice",
                "body": "needle dans le candidat exercice filtré.",
            }
        )

    def mutate_manifest(manifest) -> None:
        manifest["search_index"]["document_count"] = 59

    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=mutate_manifest,
        search_mutator=mutate_search,
    )
    snapshot = load_learning_bundle(release)

    filtered = snapshot.search(
        AUDIENCE_ID,
        "needle",
        entity_types=("exercise",),
        limit=1,
    )
    assert [item.entity_id for item in filtered] == ["exercise-content-fiction"]
    assert len(snapshot.search(AUDIENCE_ID, "needle", limit=5)) == 5


def test_open_asset_does_not_preread_payload_after_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    snapshot = load_learning_bundle(release)
    reads: list[int] = []
    real_read = os.read

    def tracked_read(descriptor: int, length: int) -> bytes:
        reads.append(length)
        return real_read(descriptor, length)

    monkeypatch.setattr(learning_bundle_module.os, "read", tracked_read)

    opened = snapshot.open_asset("asset-source-fiction", AUDIENCE_ID)

    assert reads == []
    assert opened.stream.tell() == 0
    with opened.stream as stream:
        assert stream.read() == SOURCE_BYTES
    assert reads == []


def test_open_asset_refuses_same_payload_replacement_after_activation(tmp_path: Path) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    snapshot = load_learning_bundle(release)
    asset_path = release / "assets" / "source-fiction.bin"
    replacement = release / "assets" / "replacement.bin"
    replacement.write_bytes(SOURCE_BYTES)
    os.replace(replacement, asset_path)

    _assert_public_unavailable(
        lambda: snapshot.open_asset("asset-source-fiction", AUDIENCE_ID),
        str(asset_path),
    )


def test_search_text_is_normalized_once_when_snapshot_is_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    snapshot = load_learning_bundle(release)
    normalized_values: list[str] = []
    real_normalize = learning_bundle_module._normalize_search_text

    def tracked_normalize(value: str) -> str:
        normalized_values.append(value)
        return real_normalize(value)

    monkeypatch.setattr(learning_bundle_module, "_normalize_search_text", tracked_normalize)

    assert snapshot.search(AUDIENCE_ID, "équation fictive")
    assert normalized_values == ["équation fictive"]


def test_search_worst_accepted_index_filters_before_text_matching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate_search(search) -> None:
        exercise = next(
            document for document in search["documents"] if document["catalog_node_id"] == "exercise-fiction"
        )
        exercise["title"] = "[FICTIF] needle exercice"
        exercise["body"] = "needle dans le seul exercice fictif."
        for index in range(9_997):
            search["documents"].append(
                {
                    "id": f"worst-lesson-{index}",
                    "catalog_node_id": "lesson-fiction",
                    "target_id": "content-fiction",
                    "audience_ids": [AUDIENCE_ID],
                    "title": "[FICTIF] needle leçon",
                    "body": "needle " + "charge strictement fictive " * 45,
                }
            )

    def mutate_manifest(manifest) -> None:
        manifest["search_index"]["document_count"] = 10_000

    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=mutate_manifest,
        search_mutator=mutate_search,
    )
    assert (release / "search" / "index.json").stat().st_size > 8 * 1024 * 1024
    snapshot = load_learning_bundle(release)
    matched_document_ids: list[str] = []
    real_search_score = learning_bundle_module._search_score

    def tracked_search_score(prepared, terms):  # noqa: ANN001, ANN202
        matched_document_ids.append(prepared.document.id)
        return real_search_score(prepared, terms)

    monkeypatch.setattr(learning_bundle_module, "_search_score", tracked_search_score)

    results = snapshot.search(
        AUDIENCE_ID,
        "needle",
        entity_types=("exercise",),
        limit=1,
    )

    assert [result.entity_id for result in results] == ["exercise-content-fiction"]
    assert matched_document_ids == ["search-exercise-fiction"]


def test_search_global_concurrency_limit_is_nonblocking_and_detail_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    snapshot = load_learning_bundle(release)
    first_search_entered = threading.Event()
    release_first_search = threading.Event()
    real_search_score = learning_bundle_module._search_score

    def blocked_search_score(prepared, terms):  # noqa: ANN001, ANN202
        first_search_entered.set()
        if not release_first_search.wait(timeout=5):
            raise AssertionError("synthetic search was not released")
        return real_search_score(prepared, terms)

    monkeypatch.setattr(learning_bundle_module, "_search_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(learning_bundle_module, "_search_score", blocked_search_score)

    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(snapshot.search, AUDIENCE_ID, "fictif")
        assert first_search_entered.wait(timeout=5)
        _assert_public_unavailable(
            lambda: snapshot.search(AUDIENCE_ID, "fictif"),
            str(release),
        )
        release_first_search.set()
        assert running.result(timeout=5)


@pytest.mark.parametrize(
    ("limit_name", "limit_value"),
    [
        ("_MAX_MANIFEST_BYTES", 1),
        ("_MAX_SEARCH_INDEX_BYTES", 1),
        ("_MAX_SEARCH_DOCUMENTS", 2),
        ("_MAX_ASSET_BYTES", 1),
        ("_MAX_TOTAL_FILE_BYTES", 1),
    ],
)
def test_runtime_resource_limits_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit_value: int,
) -> None:
    release = write_fictitious_learning_bundle(tmp_path)
    monkeypatch.setattr(learning_bundle_module, limit_name, limit_value)

    _assert_public_unavailable(lambda: load_learning_bundle(release), str(release))
