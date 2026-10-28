use graf::{Index, canonical_root};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;

fn assert_exact_line_coverage(path: &str, text: &str, units: &[graf::units::Unit]) {
    let physical_lines = text.lines().count();
    let mut owners = vec![0usize; physical_lines];
    for unit in units {
        assert!(unit.start >= 1, "invalid start in {unit:?}");
        assert!(unit.end >= unit.start, "invalid range in {unit:?}");
        assert!(unit.end <= physical_lines, "range past EOF in {unit:?}");
        for line in unit.start..=unit.end {
            owners[line - 1] += 1;
        }
        assert_eq!(
            unit.text,
            graf::units::unit_text_for_path(path, text, unit.start, unit.end),
            "unit text must match its physical source range: {unit:?}"
        );
    }
    assert!(
        owners.iter().all(|count| *count == 1),
        "line owners: {owners:?}"
    );
}

fn function_names(units: &[graf::units::Unit]) -> BTreeMap<String, (usize, usize)> {
    units
        .iter()
        .filter(|unit| unit.kind == "function")
        .map(|unit| {
            (
                unit.name.clone().unwrap_or_default(),
                (unit.start, unit.end),
            )
        })
        .collect()
}

#[test]
fn rust_items_cover_lines_and_extract_outer_functions_once() {
    let source = r#"#![allow(dead_code)]
/// Free function docs.
#[inline]
pub async fn free<'a, T: AsRef<str>>(input: &'a T) -> String {
    fn nested() { let local = 1; }
    let value = crate::module::qualified::<T>(input);
    Worker::new().run(value)
}

trait Service {
    /// Default implementation docs.
    fn serve(&self) { self.helper(); }
}

struct Worker;
impl Worker {
    #[track_caller]
    fn helper(&self) {}

    pub fn run<T>(&self, value: T) { self.helper(); }
}

macro_rules! generate {
    () => { fn fabricated() { hidden_call(); } };
}
generate!();
"#;
    let units = graf::units::code_units("src/lib.rs", source);
    assert_exact_line_coverage("src/lib.rs", source, &units);
    let names = function_names(&units);
    for name in ["free", "serve", "helper", "run"] {
        assert!(
            names.contains_key(name),
            "missing Rust item {name}: {names:?}"
        );
    }
    assert!(
        !names.contains_key("nested"),
        "nested function should belong to free"
    );
    assert!(
        !names.contains_key("fabricated"),
        "macro bodies are not expanded"
    );

    let free = units
        .iter()
        .find(|unit| unit.kind == "function" && unit.name.as_deref() == Some("free"))
        .unwrap();
    assert!(
        free.start <= 2,
        "doc/attribute rows must stay with the item"
    );
    let calls: Vec<_> = free.calls.iter().map(String::as_str).collect();
    assert!(
        calls.iter().any(|name| name.ends_with("qualified")),
        "{calls:?}"
    );
    assert!(calls.iter().any(|name| name.ends_with("run")), "{calls:?}");
    assert!(!calls.iter().any(|name| name.contains("hidden_call")));
    assert!(
        units
            .iter()
            .flat_map(|unit| &unit.calls)
            .all(|name| !name.contains("hidden_call"))
    );
    assert!(free.text.contains("/// Free function docs."));
    assert!(free.text.contains("#[inline]"));

    let serve = units
        .iter()
        .find(|unit| unit.kind == "function" && unit.name.as_deref() == Some("serve"))
        .unwrap();
    assert!(serve.text.contains("/// Default implementation docs."));
}

#[test]
fn rust_physical_ranges_preserve_crlf_and_unicode_separator_literals() {
    let separator = '\u{2028}';
    let source = format!(
        "pub fn physical() {{\r\n    let value = \"before{separator}after\";\r\n}}\r\npub fn following() {{}}\r\n"
    );
    let units = graf::units::code_units("physical.rs", &source);
    assert_exact_line_coverage("physical.rs", &source, &units);
    let functions: Vec<_> = units
        .iter()
        .filter(|unit| unit.kind == "function")
        .collect();
    assert_eq!(functions.len(), 2);
    assert_eq!(functions[0].start, 1);
    assert_eq!(functions[0].end, 3);
    assert_eq!(functions[1].start, 4);
    for unit in functions {
        let expected = source
            .lines()
            .skip(unit.start - 1)
            .take(unit.end - unit.start + 1)
            .collect::<Vec<_>>()
            .join("\n");
        assert_eq!(
            graf::units::unit_text_for_path("physical.rs", &source, unit.start, unit.end),
            expected
        );
    }
    assert!(units.iter().any(|unit| unit.text.contains(separator)));
}

#[test]
fn malformed_rust_keeps_line_coverage_without_panicking() {
    let source = "/// unfinished docs\n#[inline]\npub async fn broken<T( {\n    let café = \"unterminated\n    if true {\n        call::<T>();\n";
    let units = graf::units::code_units("broken.rs", source);
    assert!(!units.is_empty());
    assert_exact_line_coverage("broken.rs", source, &units);
}

#[test]
fn rust_outline_and_unit_cache_refresh_after_source_edits() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("lib.rs"),
        "/// First docs.\n#[inline]\npub fn first() {}\n\npub fn second() {}\n",
    )
    .unwrap();
    fs::write(dir.path().join("README.md"), "# docs\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, true).unwrap();

    let index = Index::load(&root).unwrap();
    let outline = graf::triage::outline(&index, "lib.rs").unwrap();
    assert_eq!(outline.functions.len(), 2);
    assert!(outline.context.contains("first"));
    let bpe = tiktoken_rs::cl100k_base().unwrap();
    let page = graf::triage::outline_budgeted(&bpe, &index, "lib.rs", 1000, 0, 1).unwrap();
    assert_eq!(page.functions.len(), 1);
    assert_eq!(page.next_offset, Some(1));
    assert!(page.context.contains("Continuation: next_offset=1"));

    let old_units = graf::triage::indexed_units(&index).unwrap();
    assert_eq!(
        old_units
            .units
            .iter()
            .filter(|unit| unit.path == "lib.rs" && unit.kind == "function")
            .count(),
        2
    );

    // Simulate a persisted pre-Rust cache. Keeping the same binary shape lets the
    // test exercise the production version check rather than a corrupt-file path.
    #[derive(Serialize, Deserialize)]
    struct CachedUnits {
        version: String,
        sources: String,
        units: Vec<graf::units::Unit>,
        bm25: graf::units::Bm25,
        edges: Vec<(usize, usize)>,
    }
    let cache_path = root.join(".graf/unit-index.bin");
    let mut stale: CachedUnits = bincode::deserialize(&fs::read(&cache_path).unwrap()).unwrap();
    stale.version = "0.1.0/units-4".to_string();
    stale.units = vec![graf::units::Unit {
        path: "lib.rs".into(),
        kind: "text".into(),
        name: Some("stale-text-window-marker".into()),
        start: 1,
        end: 1,
        text: "stale-text-window-marker".into(),
        calls: Vec::new(),
    }];
    fs::write(&cache_path, bincode::serialize(&stale).unwrap()).unwrap();
    drop(index);
    let stale_index = Index::load(&root).unwrap();
    let rebuilt = graf::triage::indexed_units(&stale_index).unwrap();
    assert!(
        rebuilt
            .units
            .iter()
            .any(|unit| unit.name.as_deref() == Some("first"))
    );
    assert!(
        !rebuilt
            .units
            .iter()
            .any(|unit| unit.name.as_deref() == Some("stale-text-window-marker"))
    );

    fs::write(
        root.join("lib.rs"),
        "/// Edited docs.\n#[inline]\npub fn replacement() {}\n\npub fn second() {}\n",
    )
    .unwrap();
    Index::build(&root, false).unwrap();
    let refreshed = Index::load(&root).unwrap();
    let new_units = graf::triage::indexed_units(&refreshed).unwrap();
    let names: Vec<_> = new_units
        .units
        .iter()
        .filter(|unit| unit.path == "lib.rs" && unit.kind == "function")
        .filter_map(|unit| unit.name.as_deref())
        .collect();
    assert_eq!(names, ["replacement", "second"]);
    assert!(graf::triage::outline(&refreshed, "README.md").is_err());
}
