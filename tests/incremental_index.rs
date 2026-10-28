use graf::{Analyzer, Index, Stopwords, canonical_root, index_path};
use serde_json::Value;
use std::fs;
use tempfile::TempDir;

fn fixture() -> TempDir {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("keep.rs"),
        "pub fn retainCache() { let cache_alpha = 1; }\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("zeta.rs"),
        "pub fn refreshCache() { let session = 2; }\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("ignored.rs"),
        "pub fn hiddenCache() { let secret = 3; }\n",
    )
    .unwrap();
    dir
}

fn assert_matches_full_rebuild(
    root: &std::path::Path,
    analyzer: Analyzer,
) -> (usize, usize, usize) {
    let report = Index::build_with(root, false, analyzer).unwrap();
    let incremental = Index::load(root).unwrap();
    let incremental_export = incremental.export_legacy().unwrap();
    let incremental_bytes = fs::read(index_path(root)).unwrap();
    let incremental_search: Value = serde_json::to_value(
        incremental
            .search("cache alpha unicode", 4096, 20, None, None)
            .unwrap(),
    )
    .unwrap();
    let incremental_locate: Value = serde_json::to_value(
        incremental
            .locate("refresh cache", 4096, 20, None, None)
            .unwrap(),
    )
    .unwrap();

    Index::build_with(root, true, analyzer).unwrap();
    let rebuilt = Index::load(root).unwrap();
    assert_eq!(incremental_export, rebuilt.export_legacy().unwrap());
    assert_eq!(incremental_bytes, fs::read(index_path(root)).unwrap());
    assert_eq!(
        incremental_search,
        serde_json::to_value(
            rebuilt
                .search("cache alpha unicode", 4096, 20, None, None)
                .unwrap()
        )
        .unwrap()
    );
    assert_eq!(
        incremental_locate,
        serde_json::to_value(
            rebuilt
                .locate("refresh cache", 4096, 20, None, None)
                .unwrap()
        )
        .unwrap()
    );
    (
        report.changed_files,
        report.reused_files,
        report.deleted_files,
    )
}

#[test]
fn incremental_snapshots_match_full_rebuilds_across_source_set_mutations() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    let default = Analyzer::default();

    Index::build_with(&root, true, default).unwrap();
    let before_noop = fs::read(index_path(&root)).unwrap();
    let noop = Index::build_with(&root, false, default).unwrap();
    assert_eq!(
        (noop.changed_files, noop.reused_files, noop.deleted_files),
        (0, 3, 0)
    );
    assert_eq!(before_noop, fs::read(index_path(&root)).unwrap());

    // Insert a lexicographically earlier file, shifting later chunk ordinals.
    fs::create_dir(root.join("a")).unwrap();
    fs::write(
        root.join("a/first.rs"),
        "pub fn unicode_café() { let cache_alpha = \"東京\"; }\n\n",
    )
    .unwrap();
    assert_eq!(assert_matches_full_rebuild(&root, default), (1, 3, 0));

    // Change term counts and create multiple blank/Unicode-bearing windows.
    let revised: String = (0..90)
        .map(|line| match line {
            0 => "pub fn refreshCache() {\n".to_string(),
            40 => "  let café_value = \"東京 cache alpha\";\n\n".to_string(),
            89 => "}\n".to_string(),
            _ => format!("  // cache marker_{line}\n"),
        })
        .collect();
    fs::write(root.join("zeta.rs"), revised).unwrap();
    assert_eq!(assert_matches_full_rebuild(&root, default), (1, 3, 0));

    fs::remove_file(root.join("keep.rs")).unwrap();
    assert_eq!(assert_matches_full_rebuild(&root, default), (0, 3, 1));

    // A rename with identical bytes is still a changed path because path terms
    // contribute to postings and source citations.
    fs::rename(root.join("zeta.rs"), root.join("renamed_cache.rs")).unwrap();
    assert_eq!(assert_matches_full_rebuild(&root, default), (1, 2, 1));

    // Toggle ignore state in both directions and verify the eligible file set.
    fs::write(root.join(".gitignore"), "ignored.rs\n").unwrap();
    assert_eq!(assert_matches_full_rebuild(&root, default), (0, 2, 1));
    assert!(!Index::load(&root).unwrap().files.contains_key("ignored.rs"));
    fs::write(root.join(".gitignore"), "\n").unwrap();
    assert_eq!(assert_matches_full_rebuild(&root, default), (1, 2, 0));
    assert!(Index::load(&root).unwrap().files.contains_key("ignored.rs"));

    // Analyzer switching reuses persisted source snapshots but rebuilds postings.
    let experimental = Analyzer {
        stopwords: Stopwords::None,
        full_identifiers: true,
        path_weight: 3,
    };
    assert_eq!(assert_matches_full_rebuild(&root, experimental), (0, 3, 0));
    assert_eq!(assert_matches_full_rebuild(&root, default), (0, 3, 0));
}

#[test]
fn persistent_mcp_refresh_matches_fresh_server_after_ordinal_shifts() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("z.ts"),
        "export function refreshCache() { const orbitalNeedle = 1; }\n",
    )
    .unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, true).unwrap();
    let mut persistent = graf::mcp::Server::new(&root).unwrap();
    let args = serde_json::json!({"query":"orbitalNeedle", "budget":1000});
    let compare = |server: &mut graf::mcp::Server| {
        let incremental = server.call("search", &args).unwrap();
        let fresh = graf::mcp::Server::new(&root)
            .unwrap()
            .call("search", &args)
            .unwrap();
        assert_eq!(incremental, fresh);
        assert!(incremental.contains("z.ts"));
    };

    compare(&mut persistent);
    fs::write(
        root.join("a.ts"),
        "export function earlier() { const orbitalNeedle = 2; }\n",
    )
    .unwrap();
    compare(&mut persistent);

    fs::write(
        root.join("a.ts"),
        "export function earlier() { const orbitalNeedle = 300; const revised = true; }\n",
    )
    .unwrap();
    compare(&mut persistent);

    fs::remove_file(root.join("a.ts")).unwrap();
    compare(&mut persistent);

    fs::write(
        root.join("a.ts"),
        "export function earlier() { const orbitalNeedle = 4; }\n",
    )
    .unwrap();
    fs::write(root.join(".gitignore"), "a.ts\n").unwrap();
    compare(&mut persistent);
}
