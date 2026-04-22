"""Tests for _merge_manifests — duplicate path detection in chunked mode."""

import json


from tasks.implement import _merge_manifests


def _manifest(*files: tuple[str, str]) -> str:
    """Build a manifest JSON string from (path, content) pairs."""
    return json.dumps({"files": [{"path": p, "content": c} for p, c in files]})


# ── Basic merging ────────────────────────────────────────────────────────────


class TestBasicMerge:
    def test_single_manifest_unchanged(self):
        m = _manifest(("src/main.py", "print('hi')"))
        merged_json, conflicts = _merge_manifests([m])
        merged = json.loads(merged_json)
        assert len(merged["files"]) == 1
        assert merged["files"][0]["path"] == "src/main.py"
        assert conflicts == []

    def test_two_manifests_no_overlap(self):
        m1 = _manifest(("src/a.py", "a"))
        m2 = _manifest(("src/b.py", "b"))
        merged_json, conflicts = _merge_manifests([m1, m2])
        merged = json.loads(merged_json)
        paths = [f["path"] for f in merged["files"]]
        assert sorted(paths) == ["src/a.py", "src/b.py"]
        assert conflicts == []

    def test_empty_manifests(self):
        merged_json, conflicts = _merge_manifests([])
        merged = json.loads(merged_json)
        assert merged["files"] == []
        assert conflicts == []

    def test_three_manifests_no_overlap(self):
        m1 = _manifest(("a.py", "a"))
        m2 = _manifest(("b.py", "b"))
        m3 = _manifest(("c.py", "c"))
        merged_json, conflicts = _merge_manifests([m1, m2, m3])
        merged = json.loads(merged_json)
        assert len(merged["files"]) == 3
        assert conflicts == []


# ── Duplicate detection ──────────────────────────────────────────────────────


class TestDuplicateDetection:
    def test_detects_single_duplicate(self):
        m1 = _manifest(("src/types/index.ts", "export interface Foo {}"))
        m2 = _manifest(("src/types/index.ts", "export interface Bar {}"))
        merged_json, conflicts = _merge_manifests([m1, m2])
        assert len(conflicts) == 1
        assert conflicts[0]["path"] == "src/types/index.ts"

    def test_last_writer_wins(self):
        m1 = _manifest(("config.ts", "version 1"))
        m2 = _manifest(("config.ts", "version 2"))
        merged_json, conflicts = _merge_manifests([m1, m2])
        merged = json.loads(merged_json)
        assert len(merged["files"]) == 1
        assert merged["files"][0]["content"] == "version 2"

    def test_conflict_records_both_chunks(self):
        m1 = _manifest(("shared.ts", "v1"))
        m2 = _manifest(("shared.ts", "v2"))
        _, conflicts = _merge_manifests([m1, m2], component_names=["Core Types", "Database"])
        assert conflicts[0]["chunks"] == ["Core Types", "Database"]
        assert conflicts[0]["winner"] == "Database"

    def test_three_way_conflict(self):
        m1 = _manifest(("utils.ts", "v1"))
        m2 = _manifest(("utils.ts", "v2"))
        m3 = _manifest(("utils.ts", "v3"))
        merged_json, conflicts = _merge_manifests([m1, m2, m3], component_names=["A", "B", "C"])
        assert len(conflicts) == 1
        assert conflicts[0]["chunks"] == ["A", "B", "C"]
        assert conflicts[0]["winner"] == "C"
        merged = json.loads(merged_json)
        assert merged["files"][0]["content"] == "v3"

    def test_multiple_different_duplicates(self):
        m1 = _manifest(("a.ts", "v1"), ("b.ts", "v1"))
        m2 = _manifest(("a.ts", "v2"), ("c.ts", "v1"))
        m3 = _manifest(("b.ts", "v2"), ("d.ts", "v1"))
        _, conflicts = _merge_manifests([m1, m2, m3])
        conflict_paths = sorted(c["path"] for c in conflicts)
        assert conflict_paths == ["a.ts", "b.ts"]

    def test_non_conflicting_files_preserved(self):
        m1 = _manifest(("shared.ts", "v1"), ("unique_a.ts", "a"))
        m2 = _manifest(("shared.ts", "v2"), ("unique_b.ts", "b"))
        merged_json, conflicts = _merge_manifests([m1, m2])
        merged = json.loads(merged_json)
        paths = sorted(f["path"] for f in merged["files"])
        assert paths == ["shared.ts", "unique_a.ts", "unique_b.ts"]
        assert len(conflicts) == 1


# ── Component names ──────────────────────────────────────────────────────────


class TestComponentNames:
    def test_default_names_when_none_provided(self):
        m1 = _manifest(("x.ts", "v1"))
        m2 = _manifest(("x.ts", "v2"))
        _, conflicts = _merge_manifests([m1, m2])
        assert conflicts[0]["chunks"] == ["chunk_0", "chunk_1"]

    def test_custom_names_in_conflict(self):
        m1 = _manifest(("x.ts", "v1"))
        m2 = _manifest(("x.ts", "v2"))
        _, conflicts = _merge_manifests([m1, m2], component_names=["Auth Module", "API Layer"])
        assert conflicts[0]["chunks"] == ["Auth Module", "API Layer"]


# ── Output format ────────────────────────────────────────────────────────────


class TestOutputFormat:
    def test_merged_json_is_valid(self):
        m1 = _manifest(("a.py", "a"))
        m2 = _manifest(("b.py", "b"))
        merged_json, _ = _merge_manifests([m1, m2])
        data = json.loads(merged_json)
        assert "files" in data
        assert isinstance(data["files"], list)

    def test_conflicts_is_list_of_dicts(self):
        m1 = _manifest(("x.ts", "v1"))
        m2 = _manifest(("x.ts", "v2"))
        _, conflicts = _merge_manifests([m1, m2])
        assert isinstance(conflicts, list)
        assert all(isinstance(c, dict) for c in conflicts)
        assert all("path" in c and "chunks" in c and "winner" in c for c in conflicts)


# ── Version enrichment (P1-3) ───────────────────────────────────────────────
# Each conflict now carries a `versions` list so the dashboard can show the
# human exactly what they are choosing between.


class TestVersionEnrichment:
    def test_conflict_has_versions_list(self):
        m1 = _manifest(("shared.ts", "v1 content"))
        m2 = _manifest(("shared.ts", "v2 content"))
        _, conflicts = _merge_manifests([m1, m2], component_names=["A", "B"])
        assert "versions" in conflicts[0]
        assert len(conflicts[0]["versions"]) == 2

    def test_version_entry_has_required_fields(self):
        m1 = _manifest(("shared.ts", "v1 content"))
        m2 = _manifest(("shared.ts", "v2 content"))
        _, conflicts = _merge_manifests([m1, m2], component_names=["A", "B"])
        v = conflicts[0]["versions"][0]
        assert set(v.keys()) == {"chunk", "sha256", "size", "content_preview"}
        assert v["chunk"] == "A"
        assert v["size"] == len("v1 content")
        assert v["content_preview"] == "v1 content"

    def test_version_sha256_differs_across_chunks(self):
        m1 = _manifest(("shared.ts", "v1 content"))
        m2 = _manifest(("shared.ts", "v2 different content"))
        _, conflicts = _merge_manifests([m1, m2])
        hashes = {v["sha256"] for v in conflicts[0]["versions"]}
        assert len(hashes) == 2

    def test_content_preview_truncated_at_200_chars(self):
        long_content = "x" * 500
        m1 = _manifest(("shared.ts", long_content))
        m2 = _manifest(("shared.ts", "short"))
        _, conflicts = _merge_manifests([m1, m2])
        preview = conflicts[0]["versions"][0]["content_preview"]
        assert preview.startswith("x" * 200)
        assert preview.endswith("…")
        assert len(preview) == 201  # 200 chars + ellipsis

    def test_non_conflicting_files_still_single_version(self):
        """Files that don't conflict should NOT appear in the conflicts list."""
        m1 = _manifest(("a.ts", "a"), ("shared.ts", "v1"))
        m2 = _manifest(("b.ts", "b"), ("shared.ts", "v2"))
        _, conflicts = _merge_manifests([m1, m2])
        conflict_paths = [c["path"] for c in conflicts]
        assert "a.ts" not in conflict_paths
        assert "b.ts" not in conflict_paths
        assert "shared.ts" in conflict_paths


# ── winner_policy parameter (P1-3) ──────────────────────────────────────────


class TestWinnerPolicy:
    def test_default_is_last_writer(self):
        """Regression lock on the historical default."""
        m1 = _manifest(("config.ts", "first"))
        m2 = _manifest(("config.ts", "second"))
        merged_json, conflicts = _merge_manifests([m1, m2])
        merged = json.loads(merged_json)
        assert merged["files"][0]["content"] == "second"
        assert conflicts[0]["winner"] == "chunk_1"

    def test_explicit_last_matches_default(self):
        m1 = _manifest(("config.ts", "first"))
        m2 = _manifest(("config.ts", "second"))
        merged_last, _ = _merge_manifests([m1, m2], winner_policy="last")
        merged_default, _ = _merge_manifests([m1, m2])
        assert merged_last == merged_default

    def test_first_writer_picks_first_chunk(self):
        m1 = _manifest(("config.ts", "first"))
        m2 = _manifest(("config.ts", "second"))
        merged_json, conflicts = _merge_manifests(
            [m1, m2], component_names=["A", "B"], winner_policy="first"
        )
        merged = json.loads(merged_json)
        assert merged["files"][0]["content"] == "first"
        assert conflicts[0]["winner"] == "A"

    def test_first_writer_three_way_conflict(self):
        m1 = _manifest(("utils.ts", "v1"))
        m2 = _manifest(("utils.ts", "v2"))
        m3 = _manifest(("utils.ts", "v3"))
        merged_json, _ = _merge_manifests([m1, m2, m3], winner_policy="first")
        merged = json.loads(merged_json)
        assert merged["files"][0]["content"] == "v1"

    def test_policy_does_not_affect_non_conflicting_files(self):
        """Files with only one source should be present regardless of policy."""
        m1 = _manifest(("a.ts", "only-in-a"), ("shared.ts", "v1"))
        m2 = _manifest(("b.ts", "only-in-b"), ("shared.ts", "v2"))
        merged_json, _ = _merge_manifests([m1, m2], winner_policy="first")
        merged = json.loads(merged_json)
        paths = {f["path"]: f["content"] for f in merged["files"]}
        assert paths["a.ts"] == "only-in-a"
        assert paths["b.ts"] == "only-in-b"
        assert paths["shared.ts"] == "v1"  # first policy on the conflict
