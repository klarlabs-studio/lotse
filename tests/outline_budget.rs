use graf::{Index, canonical_root};
use std::fs;
use tempfile::TempDir;
use tiktoken_rs::cl100k_base;

fn indexed(source: &str) -> (TempDir, Index) {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("sample.ts"), source).unwrap();
    fs::write(dir.path().join("README.md"), "# docs\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    (dir, index)
}

#[test]
fn pages_have_no_gaps_and_keep_unicode_signatures_whole() {
    let (_dir, index) = indexed(
        "export function café東京() { return 'é'; }\n\
         export function second() { return 2; }\n\
         export function third() { return 3; }\n",
    );
    let bpe = cl100k_base().unwrap();
    let first = graf::triage::outline_budgeted(&bpe, &index, "sample.ts", 100, 0, 1).unwrap();
    assert_eq!(first.functions.len(), 1);
    assert!(first.functions[0].signature.contains("café東京"));
    assert_eq!(first.next_entry, Some(1));
    let second = graf::triage::outline_budgeted(&bpe, &index, "sample.ts", 100, 1, 1).unwrap();
    let third = graf::triage::outline_budgeted(&bpe, &index, "sample.ts", 100, 2, 1).unwrap();
    let names: Vec<_> = [&first, &second, &third]
        .into_iter()
        .flat_map(|page| {
            page.functions
                .iter()
                .map(|entry| entry.name.as_deref().unwrap())
        })
        .collect();
    assert_eq!(names, ["café東京", "second", "third"]);
    assert_eq!(third.next_entry, None);
    assert!(!third.truncated);
    for page in [&first, &second, &third] {
        assert_eq!(
            bpe.encode_ordinary(&page.context).len(),
            page.context_tokens
        );
        assert!(page.context_tokens <= page.budget);
    }
}

#[test]
fn tiny_zero_and_oversized_inputs_report_continuation() {
    let source = format!(
        "{}{}",
        "// filler\n".repeat(400),
        "function large() {\n".to_owned()
            + &"  const x = 1;\n".repeat(400)
            + "}\nfunction next() {}\n"
    );
    let (_dir, index) = indexed(&source);
    let bpe = cl100k_base().unwrap();
    let tiny = graf::triage::outline_budgeted(&bpe, &index, "sample.ts", 1, 0, 10).unwrap();
    assert!(tiny.functions.is_empty());
    assert!(tiny.truncated);
    assert_eq!(tiny.next_entry, Some(0));
    let zero = graf::triage::outline_budgeted(&bpe, &index, "sample.ts", 0, 0, 10).unwrap();
    assert!(zero.context.is_empty());
    assert_eq!(zero.next_entry, Some(0));
    assert!(zero.truncated);
    let complete = graf::triage::outline_budgeted(&bpe, &index, "sample.ts", 100, 0, 10).unwrap();
    assert_eq!(complete.functions.len(), 2);
    assert_eq!(complete.total_functions, 2);
}

#[test]
fn continuation_footer_is_budgeted_and_points_to_first_undisplayed_entry() {
    let source: String = (0..30)
        .map(|i| format!("export function item{i}() {{ return {i}; }}\n"))
        .collect();
    let (_dir, index) = indexed(&source);
    let bpe = cl100k_base().unwrap();
    let page = graf::triage::outline_budgeted(&bpe, &index, "sample.ts", 80, 0, 30).unwrap();
    assert!(page.truncated);
    assert_eq!(page.next_offset, Some(page.functions.len()));
    assert_eq!(page.next_entry, page.next_offset);
    assert!(page.context.contains(&format!(
        "Continuation: next_offset={} (increase budget)",
        page.next_offset.unwrap()
    )));
    assert_eq!(
        bpe.encode_ordinary(&page.context).len(),
        page.context_tokens
    );
    assert!(page.context_tokens <= page.budget);
    assert_eq!(page.total_functions, 30);
}

#[test]
fn legacy_outline_keeps_its_context_and_errors() {
    let (_dir, index) = indexed("function alpha() {}\nfunction beta() {}\n");
    let legacy = graf::triage::outline(&index, "sample.ts").unwrap();
    assert_eq!(
        legacy.context,
        "sample.ts (2 lines, 2 functions)\nL1-1: function alpha() {}\nL2-2: function beta() {}\n"
    );
    assert!(
        graf::triage::outline_budgeted(&cl100k_base().unwrap(), &index, "missing.ts", 100, 0, 1)
            .is_err()
    );
    let err =
        graf::triage::outline_budgeted(&cl100k_base().unwrap(), &index, "README.md", 100, 0, 1)
            .unwrap_err();
    let message = err.to_string();
    assert!(message.contains("outline supports"));
    for language in ["Rust", "Go", "Python", "JavaScript", "TypeScript"] {
        assert!(message.contains(language), "{message}");
    }
}
