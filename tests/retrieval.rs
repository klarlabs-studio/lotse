use graf::{Index, canonical_root, terms};
use std::fs;
use std::process::Command;
use tempfile::TempDir;
use tiktoken_rs::cl100k_base;

fn fixture() -> TempDir {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir(dir.path().join("docs")).unwrap();
    fs::create_dir(dir.path().join("tests")).unwrap();
    fs::write(
        dir.path().join("cache.rs"),
        "pub fn refreshCache() {\n    let session = \"café 東京\";\n}\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("docs/cache.md"),
        "# Cache policy\nRefresh the session cache before rotation.\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("tests/cache.rs"),
        "fn rotation_check() { refreshCache(); }\n",
    )
    .unwrap();
    dir
}

#[test]
fn persisted_search_is_deterministic_and_scoped() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    let stats = Index::build(&root, false).unwrap();
    assert_eq!(stats.changed_files, 3);
    let index = Index::load(&root).unwrap();
    let a = index.search("refresh cache", 2000, 10, None, None).unwrap();
    let b = index.search("refresh cache", 2000, 10, None, None).unwrap();
    assert_eq!(a.status, "ok");
    assert_eq!(a.context, b.context);
    assert_eq!(a.excerpts.len(), 3);
    let docs = index
        .search("refresh cache", 2000, 10, Some("docs/"), Some("docs"))
        .unwrap();
    assert_eq!(docs.excerpts.len(), 1);
    assert_eq!(docs.excerpts[0].path, "docs/cache.md");
    assert_eq!(
        terms("HTTPServer refreshCache cache_key"),
        ["http", "server", "refresh", "cache", "cache", "key"]
    );
}

#[test]
fn budgets_include_headers_unicode_and_empty_controls() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let bpe = cl100k_base().unwrap();
    let full = index.search("session cache", 2000, 10, None, None).unwrap();
    for budget in [
        0,
        1,
        8,
        25,
        40,
        full.context_tokens - 1,
        full.context_tokens,
    ] {
        let result = index
            .search("session cache", budget, 10, None, None)
            .unwrap();
        assert_eq!(
            bpe.encode_ordinary(&result.context).len(),
            result.context_tokens
        );
        assert!(result.context_tokens <= budget);
        assert_eq!(result.context.is_empty(), result.excerpts.is_empty());
    }
    let none = index.search("giraffezebra", 2000, 10, None, None).unwrap();
    assert_eq!(none.status, "no_lexical_match");
    assert!(none.context.is_empty());
    assert_eq!(
        index.search("cache", 0, 10, None, None).unwrap().status,
        "budget_exhausted"
    );
    assert!(index.search("  ", 20, 10, None, None).is_err());
}

#[test]
fn expansion_checks_hashes_and_returns_exact_source_lines() {
    let dir = fixture();
    let path = dir.path().join("long.rs");
    let text: String = (1..=80).map(|i| format!("// marker_{i}\n")).collect();
    fs::write(&path, &text).unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let anchor = &index.files["long.rs"].chunks[1];
    let expanded = index.expand(&anchor.id, 3, 7, 2000).unwrap();
    assert_eq!(expanded.excerpts[0].start, 22);
    assert_eq!(expanded.excerpts[0].end, 55);
    assert!(expanded.context.ends_with("// marker_55"));
    assert!(!expanded.context.contains("// marker_56"));
    assert_eq!(
        index.expand(&anchor.id, 3, 7, 1).unwrap().status,
        "budget_exhausted"
    );
    fs::write(path, text.replace("marker_25", "changed_25")).unwrap();
    assert!(
        index
            .expand(&anchor.id, 0, 0, 2000)
            .unwrap_err()
            .to_string()
            .contains("Stale source")
    );
    assert!(index.search("marker", 2000, 10, None, None).is_err());
}

#[test]
fn updates_reuse_unchanged_sources_and_drop_deleted_postings() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let old = Index::load(&root).unwrap();
    let same = Index::build(&root, false).unwrap();
    assert_eq!(
        (same.changed_files, same.reused_files, same.deleted_files),
        (0, 3, 0)
    );
    fs::write(root.join("cache.rs"), "fn invalidateCache() {}\n").unwrap();
    fs::remove_file(root.join("docs/cache.md")).unwrap();
    let update = Index::build(&root, false).unwrap();
    assert_eq!(
        (
            update.changed_files,
            update.reused_files,
            update.deleted_files
        ),
        (1, 1, 1)
    );
    let index = Index::load(&root).unwrap();
    assert_eq!(
        index.files["tests/cache.rs"].chunks[0].id,
        old.files["tests/cache.rs"].chunks[0].id
    );
    assert_ne!(
        index.files["cache.rs"].chunks[0].id,
        old.files["cache.rs"].chunks[0].id
    );
    assert_eq!(
        index.search("policy", 2000, 10, None, None).unwrap().status,
        "no_lexical_match"
    );
}

#[test]
fn ignores_generated_hidden_binary_and_oversized_files() {
    let dir = fixture();
    fs::write(dir.path().join(".gitignore"), "ignored.rs\n").unwrap();
    fs::write(dir.path().join("ignored.rs"), "secret").unwrap();
    fs::write(dir.path().join(".env.txt"), "secret").unwrap();
    fs::write(dir.path().join("binary.rs"), b"a\0b").unwrap();
    fs::write(dir.path().join("large.rs"), "a".repeat(1_000_001)).unwrap();
    fs::create_dir(dir.path().join("node_modules")).unwrap();
    fs::write(dir.path().join("node_modules/dep.js"), "generated").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    let report = Index::build(&root, false).unwrap();
    assert_eq!(report.stats.files, 3);
    assert_eq!(report.skipped_files, 2);
    assert_eq!(Index::build(&root, false).unwrap().stats.files, 3);
}

#[cfg(unix)]
#[test]
fn symlinks_are_excluded_and_replacements_rejected() {
    use std::os::unix::fs::symlink;
    let dir = fixture();
    let outside = tempfile::tempdir().unwrap();
    fs::write(outside.path().join("escape.rs"), "secret").unwrap();
    symlink(outside.path().join("escape.rs"), dir.path().join("link.rs")).unwrap();
    let root = canonical_root(dir.path()).unwrap();
    assert_eq!(Index::build(&root, false).unwrap().stats.files, 3);
    let index = Index::load(&root).unwrap();
    fs::remove_file(root.join("cache.rs")).unwrap();
    symlink(outside.path().join("escape.rs"), root.join("cache.rs")).unwrap();
    assert!(index.search("refresh", 2000, 10, None, None).is_err());
}

#[test]
fn cli_emits_machine_readable_results_and_actionable_errors() {
    let dir = fixture();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_graf"))
            .arg("--root")
            .arg(dir.path())
            .args(args)
            .output()
            .unwrap()
    };
    let missing = run(&["stats"]);
    assert!(!missing.status.success());
    assert!(
        String::from_utf8(missing.stderr)
            .unwrap()
            .contains("graf index")
    );
    assert!(run(&["index"]).status.success());
    let search = run(&["search", "cache", "--budget", "100"]);
    assert!(search.status.success());
    let value: serde_json::Value = serde_json::from_slice(&search.stdout).unwrap();
    assert_eq!(value["status"], "ok");
    assert!(value["context_tokens"].as_u64().unwrap() <= 100);
    let map: serde_json::Value =
        serde_json::from_slice(&run(&["map", "--limit", "1"]).stdout).unwrap();
    assert_eq!(map["files"].as_array().unwrap().len(), 1);
    assert_eq!(map["next_offset"], 1);
    assert!(!run(&["search", "cache", "--limit", "0"]).status.success());
    let mut child = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(dir.path())
        .arg("map")
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    drop(child.stdout.take());
    assert!(child.wait().unwrap().success());
}

fn rewrite_index(root: &std::path::Path, edit: impl FnOnce(&mut Index)) {
    let path = graf::index_path(root);
    let mut index: Index = bincode::deserialize(&fs::read(&path).unwrap()).unwrap();
    edit(&mut index);
    fs::write(&path, bincode::serialize(&index).unwrap()).unwrap();
}

#[test]
fn incompatible_and_moved_indexes_require_explicit_rebuild() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let original = fs::read(graf::index_path(&root)).unwrap();
    rewrite_index(&root, |index| index.schema = 99);
    assert!(Index::load(&root).is_err());
    assert!(Index::build(&root, false).is_err());
    Index::build(&root, true).unwrap();
    let current = Index::load(&root).unwrap();
    assert_eq!(current.files.len(), 3);
    fs::write(graf::index_path(&root), &original).unwrap();
    rewrite_index(&root, |index| index.root = "/another-checkout".into());
    assert!(Index::load(&root).is_err());
    Index::build(&root, true).unwrap();
    assert!(Index::load(&root).is_ok());
}

#[test]
fn corrupt_postings_and_excerpt_ranges_are_rejected() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let original = fs::read(graf::index_path(&root)).unwrap();
    rewrite_index(&root, |index| {
        index.postings.values_mut().next().unwrap()[0].chunk = 999_999;
    });
    assert!(Index::load(&root).is_err());
    fs::write(graf::index_path(&root), &original).unwrap();
    rewrite_index(&root, |index| {
        let chunk = &mut index.files.get_mut("cache.rs").unwrap().chunks[0];
        chunk.end = 10_000;
    });
    assert!(
        Index::load(&root)
            .unwrap_err()
            .to_string()
            .contains("Invalid source ranges")
    );
}

#[test]
fn authored_build_scripts_are_indexed_and_ignored_outputs_stay_excluded() {
    let dir = fixture();
    fs::create_dir(dir.path().join("build")).unwrap();
    fs::create_dir_all(dir.path().join("test/build")).unwrap();
    fs::write(
        dir.path().join("build/serializer.js"),
        "function makeSerializer() {}\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("test/build/serializer.test.js"),
        "makeSerializer();\n",
    )
    .unwrap();
    fs::write(dir.path().join("build/generated.js"), "generated\n").unwrap();
    fs::write(dir.path().join(".gitignore"), "build/generated.js\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    assert!(index.files.contains_key("build/serializer.js"));
    assert!(index.files.contains_key("test/build/serializer.test.js"));
    assert!(!index.files.contains_key("build/generated.js"));
}

#[test]
fn locate_returns_unique_files_with_expandable_source_anchors() {
    let dir = fixture();
    fs::write(dir.path().join("many.rs"), "// cache refresh\n".repeat(72)).unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let located = index
        .locate("refresh cache", 10_000, 100, None, None)
        .unwrap();
    let located_again = index
        .locate("refresh cache", 10_000, 100, None, None)
        .unwrap();
    assert_eq!(located.context, located_again.context);
    assert_eq!(located.output_kind, "navigation_hints");
    let paths: std::collections::BTreeSet<_> = located.hints.iter().map(|h| &h.path).collect();
    assert_eq!(paths.len(), located.hints.len());
    assert_eq!(paths.len(), 4);
    for hint in &located.hints {
        let source = fs::read_to_string(root.join(&hint.path)).unwrap();
        assert!(
            source
                .lines()
                .nth(hint.line - 1)
                .unwrap()
                .trim()
                .starts_with(&hint.preview)
        );
        assert!(hint.line >= hint.start && hint.line <= hint.end);
        let expanded = index.expand(&hint.id, 0, 0, 10_000).unwrap();
        assert_eq!(hint.id.len(), 12);
        assert!(expanded.excerpts[0].id.starts_with(&hint.id));
        assert_eq!(expanded.excerpts[0].id.len(), 64);
        assert_eq!(expanded.excerpts[0].path, hint.path);
        assert_eq!(expanded.excerpts[0].start, hint.start);
        assert_eq!(expanded.excerpts[0].end, hint.end);
        assert_eq!(expanded.excerpts[0].source_hash, hint.source_hash);
    }
    let docs = index
        .locate("refresh cache", 2000, 10, Some("docs/"), Some("docs"))
        .unwrap();
    assert_eq!(docs.hints.len(), 1);
    assert_eq!(
        docs.hints[0].id,
        located
            .hints
            .iter()
            .find(|h| h.path == docs.hints[0].path)
            .unwrap()
            .id
    );
    assert_eq!(docs.hints[0].line, 2);
    assert_eq!(
        docs.hints[0].preview,
        "Refresh the session cache before rotation."
    );
}

#[test]
fn expand_rejects_short_ambiguous_and_old_prefixes() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let mut index = Index::load(&root).unwrap();
    let located = index.locate("refresh", 2000, 10, None, None).unwrap();
    let hint = &located.hints[0];
    assert!(index.expand(&hint.id[..11], 0, 0, 2000).is_err());
    // An artificial hash-prefix collision must fail closed rather than select a file.
    let other = index
        .files
        .values_mut()
        .flat_map(|s| &mut s.chunks)
        .find(|c| c.path != hint.path)
        .unwrap();
    other.id = format!("{}{}", hint.id, "f".repeat(52));
    assert!(
        index
            .expand(&hint.id, 0, 0, 2000)
            .unwrap_err()
            .to_string()
            .contains("Ambiguous")
    );
    // Rebuilding after an edit invalidates the content-derived prefix.
    fs::write(root.join(&hint.path), "// completely new content\n").unwrap();
    Index::build(&root, false).unwrap();
    let rebuilt = Index::load(&root).unwrap();
    assert!(rebuilt.expand(&hint.id, 0, 0, 2000).is_err());
}

#[test]
fn locate_budgets_cover_handles_and_unicode_previews() {
    let dir = fixture();
    fs::write(
        dir.path().join("unicode.rs"),
        format!("// cache {}\n", "東京".repeat(100)),
    )
    .unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let bpe = cl100k_base().unwrap();
    for budget in [0, 1, 40, 80, 200, 2000] {
        let result = index.locate("cache", budget, 10, None, None).unwrap();
        assert_eq!(
            bpe.encode_ordinary(&result.context).len(),
            result.context_tokens
        );
        assert!(result.context_tokens <= budget);
        for hint in &result.hints {
            assert!(result.context.contains(&format!("id={}", hint.id)));
        }
    }
    let result = index
        .locate("cache", 2000, 1, Some("unicode.rs"), None)
        .unwrap();
    assert_eq!(result.hints[0].preview.chars().count(), 160);
    assert!(result.hints[0].preview_truncated);
    assert!(result.context.contains("[truncated]"));
    assert_eq!(
        index.locate("cache", 0, 10, None, None).unwrap().status,
        "budget_exhausted"
    );
    assert_eq!(
        index
            .locate("giraffezebra", 2000, 10, None, None)
            .unwrap()
            .status,
        "no_lexical_match"
    );
    assert!(index.locate("cache", 2000, 0, None, None).is_err());
    assert!(index.locate("  ", 2000, 10, None, None).is_err());
}

#[test]
fn locate_rejects_stale_sources_and_cli_handles_round_trip() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(&root)
        .args(["locate", "cache", "--kind", "docs"])
        .output()
        .unwrap();
    assert!(output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["output_kind"], "navigation_hints");
    let handle = value["hints"][0]["id"].as_str().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(&root)
        .args(["expand", handle, "--text"])
        .output()
        .unwrap();
    assert!(output.status.success());
    assert!(
        String::from_utf8(output.stdout)
            .unwrap()
            .contains("Refresh the session cache")
    );
    fs::write(root.join("cache.rs"), "edited source\n").unwrap();
    assert!(
        index
            .locate("cache", 2000, 10, Some("cache.rs"), None)
            .unwrap_err()
            .to_string()
            .contains("Stale source")
    );
}

#[test]
fn experimental_policies_are_opt_in_and_only_narrow_delivery() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("rotation.rs"),
        "fn rotate_session_key() { rotate(session, key); rotate(session, key); }\n",
    )
    .unwrap();
    for i in 0..8 {
        fs::write(
            dir.path().join(format!("other{i}.rs")),
            "fn unrelated() { session(); }\n",
        )
        .unwrap();
    }
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_graf"))
            .arg("--root")
            .arg(dir.path())
            .args(args)
            .output()
            .unwrap()
    };
    assert!(run(&["index"]).status.success());
    let hints = |args: &[&str]| -> Vec<String> {
        let output = run(args);
        assert!(output.status.success(), "{args:?}");
        let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        value["hints"]
            .as_array()
            .unwrap()
            .iter()
            .map(|h| h["path"].as_str().unwrap().into())
            .collect()
    };
    let query = "rotate session key";
    let accepted = hints(&["locate", query]);
    assert_eq!(accepted.len(), 9);
    assert_eq!(
        hints(&["locate", query, "--stop", "full", "--file-score", "max"]),
        accepted
    );
    let floor = hints(&["locate", query, "--stop", "floor"]);
    assert_eq!(floor, ["rotation.rs"]);
    let gap = hints(&["locate", query, "--stop", "gap"]);
    // Largest gap follows the first file: raised to the minimum of 3, plus a buffer of 2.
    assert_eq!(gap, accepted[..5]);
    assert_eq!(
        hints(&["locate", query, "--file-score", "damped"])[0],
        "rotation.rs"
    );
    let rejected = run(&["search", query, "--file-score", "damped"]);
    assert!(!rejected.status.success());
    assert!(
        String::from_utf8(rejected.stderr)
            .unwrap()
            .contains("locate only")
    );
}

#[test]
fn experimental_analyzers_persist_with_the_index_and_drive_queries() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("helpers.rs"),
        "fn test_helpers() { refreshCache(); }\n",
    )
    .unwrap();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_graf"))
            .arg("--root")
            .arg(dir.path())
            .args(args)
            .output()
            .unwrap()
    };
    let index_json = || -> serde_json::Value {
        Index::load(&canonical_root(dir.path()).unwrap())
            .unwrap()
            .export_legacy()
            .unwrap()
    };
    let matched = |query: &str| -> u64 {
        let value: serde_json::Value =
            serde_json::from_slice(&run(&["search", query]).stdout).unwrap();
        value["matched_chunks"].as_u64().unwrap()
    };
    assert!(run(&["index"]).status.success());
    assert!(
        index_json().get("analyzer").is_none(),
        "accepted indexes are unchanged"
    );
    assert_eq!(matched("test"), 0, "request phrasing is a stopword");
    assert_eq!(matched("refreshcache"), 0);

    let built = run(&[
        "index",
        "--stopwords",
        "english",
        "--full-identifiers",
        "--path-weight",
        "3",
    ]);
    assert!(built.status.success());
    let analyzer = &index_json()["analyzer"];
    assert_eq!(analyzer["stopwords"], "english");
    assert_eq!(analyzer["path_weight"], 3);
    assert_eq!(matched("test"), 1, "queries use the index's analyzer");
    assert_eq!(matched("refreshcache"), 1);

    assert!(run(&["index"]).status.success());
    assert!(
        index_json().get("analyzer").is_none(),
        "flags apply per build"
    );
    assert!(!run(&["index", "--path-weight", "0"]).status.success());
}

fn agent_fixture() -> TempDir {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir(dir.path().join("lib")).unwrap();
    fs::write(
        dir.path().join("lib/reply.js"),
        "'use strict'\nfunction onSend (reply) {\n  return serialize(reply)\n}\n\nfunction serialize (payload) {\n  return JSON.stringify(payload)\n}\nmodule.exports = { onSend }\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("lib/route.js"),
        "function route (opts) {\n  return opts.url\n}\nmodule.exports = { route }\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("README.md"),
        "# Demo\nRoutes and replies.\n",
    )
    .unwrap();
    dir
}

#[test]
fn triage_lists_files_then_whole_functions_within_budget() {
    let dir = agent_fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let bpe = cl100k_base().unwrap();
    let issue = "onSend drops the payload\n\nCalling `serialize(payload)` from onSend in lib/reply.js returns nothing.";
    let result = graf::triage::triage(&bpe, &index, issue, 2000).unwrap();
    assert_eq!(
        result.files.first().map(String::as_str),
        Some("lib/reply.js")
    );
    assert!(
        result
            .context
            .starts_with("Likely files:\n- lib/reply.js\n")
    );
    assert!(result.context.contains("Likely code:\nlib/reply.js:"));
    assert!(
        result
            .context
            .contains("function serialize (payload) {\n  return JSON.stringify(payload)\n}")
    );
    assert!(result.context_tokens <= 2000);
    let tiny = graf::triage::triage(&bpe, &index, issue, 5).unwrap();
    assert!(tiny.context_tokens <= 5);
}

#[test]
fn outline_lists_functions_with_line_ranges() {
    let dir = agent_fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let outline = graf::triage::outline(&index, "lib/reply.js").unwrap();
    assert_eq!(
        outline.context,
        "lib/reply.js (9 lines, 2 functions)\nL2-4: function onSend (reply) {\nL6-8: function serialize (payload) {\n"
    );
    assert!(graf::triage::outline(&index, "README.md").is_err());
    assert!(graf::triage::outline(&index, "missing.js").is_err());
}

#[test]
fn unit_cache_is_reused_refreshed_and_never_serves_stale_code() {
    let dir = agent_fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let bpe = cl100k_base().unwrap();
    let index = Index::load(&root).unwrap();
    let cold = graf::triage::ranked_units(&bpe, &index, "serialize payload", 2000).unwrap();
    let cache = root.join(".graf/units.bin");
    let query_index = root.join(".graf/unit-index.bin");
    let written = fs::metadata(&cache).unwrap().modified().unwrap();
    let derived = fs::read(&query_index).unwrap();
    let warm = graf::triage::ranked_units(&bpe, &index, "serialize payload", 2000).unwrap();
    assert_eq!(cold.1, warm.1);
    assert_eq!(fs::metadata(&cache).unwrap().modified().unwrap(), written);
    assert_eq!(fs::read(&query_index).unwrap(), derived);
    fs::remove_file(&query_index).unwrap();
    let rebuilt = graf::triage::ranked_units(&bpe, &index, "serialize payload", 2000).unwrap();
    assert_eq!(cold.1, rebuilt.1);
    assert_eq!(fs::read(&query_index).unwrap(), derived);

    let changed = "function serialize (payload) {\n  return String(payload)\n}\nmodule.exports = { serialize }\n";
    fs::write(root.join("lib/reply.js"), changed).unwrap();
    assert!(graf::triage::ranked_units(&bpe, &index, "serialize payload", 2000).is_err());
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let fresh = graf::triage::ranked_units(&bpe, &index, "serialize payload", 2000).unwrap();
    assert!(fresh.1.contains("return String(payload)"));
    assert!(!fresh.1.contains("JSON.stringify"));
    assert_ne!(fs::read(&query_index).unwrap(), derived);
}

#[test]
fn dash_leading_queries_are_text_not_options() {
    let dir = agent_fixture();
    let root = dir.path().to_str().unwrap();
    let graf = env!("CARGO_BIN_EXE_graf");
    assert!(
        Command::new(graf)
            .args(["--root", root, "index"])
            .status()
            .unwrap()
            .success()
    );
    for args in [
        vec!["locate", "--budget", "200", "--text", "--verbose onSend"],
        vec!["units", "--budget", "200", "--text", "-v onSend"],
        vec![
            "triage",
            "--budget",
            "200",
            "--text",
            "--verbose onSend\nbody",
        ],
        vec!["rewrite", "--verbose onSend"],
    ] {
        let output = Command::new(graf)
            .arg("--root")
            .arg(root)
            .args(&args)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{args:?}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    let output = Command::new(graf)
        .args([
            "--root", root, "locate", "--text", "--budget", "100", "onSend",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    assert!(String::from_utf8_lossy(&output.stdout).contains("lib/reply.js"));
}

#[test]
fn compact_index_stores_no_excerpt_text_and_exports_legacy_json() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    assert!(root.join(".graf/index.bin").is_file());
    assert!(!root.join(".graf/index.json").exists());
    let stored = fs::read(root.join(".graf/index.bin")).unwrap();
    assert!(!String::from_utf8_lossy(&stored).contains("refreshCache() {"));
    let index = Index::load(&root).unwrap();
    assert!(index.files["cache.rs"].chunks[0].text.is_empty());
    let found = index.search("refresh cache", 2000, 10, None, None).unwrap();
    assert!(found.context.contains("pub fn refreshCache() {"));
    let exported = index.export_legacy().unwrap();
    assert_eq!(exported["schema"], 1);
    assert_eq!(
        exported["files"]["cache.rs"]["chunks"][0]["text"],
        "pub fn refreshCache() {\n    let session = \"café 東京\";\n}"
    );
    assert!(exported["postings"]["refresh"].as_array().unwrap().len() >= 2);
    let cli = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(&root)
        .arg("export-index")
        .output()
        .unwrap();
    assert!(cli.status.success());
    let printed: serde_json::Value = serde_json::from_slice(&cli.stdout).unwrap();
    assert_eq!(printed, exported);
}

#[test]
fn a_legacy_json_index_asks_for_a_rebuild() {
    let dir = fixture();
    let root = canonical_root(dir.path()).unwrap();
    fs::create_dir(root.join(".graf")).unwrap();
    fs::write(root.join(".graf/index.json"), "{\"schema\":1}").unwrap();
    let error = Index::load(&root).unwrap_err().to_string();
    assert!(error.contains("graf index"), "{error}");
    Index::build(&root, false).unwrap();
    assert!(!root.join(".graf/index.json").exists());
    assert!(Index::load(&root).is_ok());
}

#[test]
fn cli_index_also_builds_the_function_index() {
    let dir = agent_fixture();
    let status = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(dir.path())
        .arg("index")
        .output()
        .unwrap();
    assert!(status.status.success());
    assert!(dir.path().join(".graf/unit-index.bin").is_file());
    assert!(dir.path().join(".graf/units.bin").is_file());
    assert_eq!(
        fs::read_to_string(dir.path().join(".graf/.gitignore")).unwrap(),
        "# Generated by graf\n*\n"
    );
}

fn mcp_call(
    server: &mut graf::mcp::Server,
    id: u64,
    name: &str,
    args: serde_json::Value,
) -> serde_json::Value {
    let request = serde_json::json!({"jsonrpc": "2.0", "id": id, "method": "tools/call",
                                     "params": {"name": name, "arguments": args}});
    server.handle(&request).unwrap()["result"].clone()
}

#[test]
fn mcp_server_indexes_on_demand_and_follows_edits() {
    let dir = agent_fixture();
    let mut server = graf::mcp::Server::new(dir.path()).unwrap();
    let init = server
        .handle(
            &serde_json::json!({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-06-18"}}),
        )
        .unwrap();
    assert_eq!(init["result"]["protocolVersion"], "2025-06-18");
    assert!(
        init["result"]["instructions"]
            .as_str()
            .unwrap()
            .contains("triage")
    );
    assert!(
        server
            .handle(&serde_json::json!({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            .is_none()
    );
    let listed = server
        .handle(&serde_json::json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
        .unwrap();
    let names: Vec<_> = listed["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| t["name"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(
        names,
        [
            "triage", "outline", "units", "locate", "search", "expand", "index"
        ]
    );

    // No index yet: the first call builds it.
    let first = mcp_call(
        &mut server,
        3,
        "triage",
        serde_json::json!({"issue": "serialize payload of reply"}),
    );
    assert_eq!(first["isError"], false);
    let text = first["content"][0]["text"].as_str().unwrap();
    assert!(
        text.contains("lib/reply.js") && text.contains("JSON.stringify(payload)"),
        "{text}"
    );
    assert!(dir.path().join(".graf/index.bin").is_file());

    // An edit after indexing is picked up without a manual reindex.
    fs::write(
        dir.path().join("lib/reply.js"),
        "function serialize (payload) {\n  return String(payload)\n}\nmodule.exports = { serialize }\n",
    )
    .unwrap();
    let edited = mcp_call(
        &mut server,
        4,
        "units",
        serde_json::json!({"query": "serialize payload"}),
    );
    let text = edited["content"][0]["text"].as_str().unwrap();
    assert!(
        text.contains("return String(payload)") && !text.contains("JSON.stringify"),
        "{text}"
    );

    let outline = mcp_call(
        &mut server,
        5,
        "outline",
        serde_json::json!({"path": "lib/reply.js"}),
    );
    assert!(
        outline["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("L1-3: function serialize")
    );
    let bad = mcp_call(&mut server, 6, "outline", serde_json::json!({}));
    assert_eq!(bad["isError"], true);
    let unknown = server
        .handle(&serde_json::json!({"jsonrpc": "2.0", "id": 7, "method": "nope"}))
        .unwrap();
    assert_eq!(unknown["error"]["code"], -32601);
}

#[test]
fn mcp_refreshes_membership_and_new_terms_before_ranking() {
    let dir = agent_fixture();
    let mut server = graf::mcp::Server::new(dir.path()).unwrap();
    server
        .call("triage", &serde_json::json!({"issue": "serialize payload"}))
        .unwrap();

    fs::write(
        dir.path().join("lib/new.js"),
        "function zebrabridge() { return 42 }\n",
    )
    .unwrap();
    for tool in ["locate", "search", "units", "triage"] {
        let args = if tool == "triage" {
            serde_json::json!({"issue": "zebrabridge"})
        } else {
            serde_json::json!({"query": "zebrabridge"})
        };
        let text = server.call(tool, &args).unwrap();
        assert!(text.contains("lib/new.js"), "{tool}: {text}");
    }

    // This term was absent from the postings; no old matching excerpt could trigger an error.
    fs::write(
        dir.path().join("lib/new.js"),
        "function otterbridge() { return 42 }\n",
    )
    .unwrap();
    assert!(
        server
            .call("units", &serde_json::json!({"query": "otterbridge"}))
            .unwrap()
            .contains("otterbridge")
    );
    assert!(
        server
            .call("locate", &serde_json::json!({"query": "zebrabridge"}))
            .unwrap()
            .is_empty()
    );

    fs::rename(
        dir.path().join("lib/new.js"),
        dir.path().join("lib/renamed.js"),
    )
    .unwrap();
    let renamed = server
        .call("locate", &serde_json::json!({"query": "otterbridge"}))
        .unwrap();
    assert!(renamed.contains("lib/renamed.js") && !renamed.contains("lib/new.js"));
    fs::write(dir.path().join(".gitignore"), "lib/renamed.js\n").unwrap();
    assert!(
        server
            .call("locate", &serde_json::json!({"query": "otterbridge"}))
            .unwrap()
            .is_empty()
    );
    fs::write(dir.path().join(".gitignore"), "").unwrap();
    assert!(
        !server
            .call("locate", &serde_json::json!({"query": "otterbridge"}))
            .unwrap()
            .is_empty()
    );
    fs::remove_file(dir.path().join("lib/renamed.js")).unwrap();
    assert!(
        server
            .call("units", &serde_json::json!({"query": "otterbridge"}))
            .unwrap()
            .is_empty()
    );
}

#[test]
fn mcp_search_handles_expand_and_fit_the_budget() {
    let dir = agent_fixture();
    let mut server = graf::mcp::Server::new(dir.path()).unwrap();
    let bpe = tiktoken_rs::cl100k_base().unwrap();
    for budget in [20, 40, 100, 2000] {
        let text = server
            .call(
                "search",
                &serde_json::json!({"query": "serialize payload", "budget": budget}),
            )
            .unwrap();
        assert!(bpe.encode_ordinary(&text).len() <= budget);
        if text.is_empty() {
            continue;
        }
        let handle = text
            .lines()
            .find_map(|line| line.strip_prefix("id="))
            .unwrap();
        let expanded = server
            .call("expand", &serde_json::json!({"id": handle}))
            .unwrap();
        assert!(expanded.contains("serialize"), "{expanded}");
    }
    let text = server
        .call("search", &serde_json::json!({"query": "serialize payload"}))
        .unwrap();
    assert!(text.contains("id="));
}

#[test]
fn mcp_cli_speaks_newline_delimited_json_rpc() {
    use std::io::Write as _;
    let dir = agent_fixture();
    let mut child = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(dir.path())
        .arg("mcp")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    writeln!(
        stdin,
        r#"{{"jsonrpc":"2.0","id":1,"method":"initialize","params":{{}}}}"#
    )
    .unwrap();
    writeln!(stdin, "not json").unwrap();
    writeln!(
        stdin,
        r#"{{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{{"name":"locate","arguments":{{"query":"route url"}}}}}}"#
    )
    .unwrap();
    drop(stdin);
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    let lines: Vec<serde_json::Value> = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(lines.len(), 3);
    assert_eq!(lines[1]["error"]["code"], -32700);
    assert!(
        lines[2]["result"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("lib/route.js")
    );
}

#[test]
fn cli_queries_build_a_missing_index() {
    let dir = agent_fixture();
    let output = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(dir.path())
        .args(["triage", "--text", "--", "route url"])
        .output()
        .unwrap();
    assert!(output.status.success());
    assert!(
        String::from_utf8(output.stdout)
            .unwrap()
            .contains("lib/route.js")
    );
    assert!(
        String::from_utf8(output.stderr)
            .unwrap()
            .contains("no index yet")
    );
    assert!(dir.path().join(".graf/unit-index.bin").is_file());
}
