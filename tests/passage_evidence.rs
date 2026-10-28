use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

#[test]
fn late_passage_is_selected_before_fit_and_exact_range_expands() {
    let dir = tempfile::tempdir().unwrap();
    let mut lines = vec!["fn alpha() {".to_string()];
    lines.extend((0..65).map(|n| format!("    // unrelated filler {n}")));
    lines.push("    // alpha beta café\u{2028}samephysical".into());
    lines.push("}".into());
    lines.push("fn tiny_note() {}".into());
    fs::write(dir.path().join("a.rs"), lines.join("\r\n")).unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    for budget in [100, 2000] {
        let result = index
            .evidence_passages_with("alpha beta", budget, 1, None, None, Policy::default())
            .unwrap();
        assert_eq!(result.excerpts.len(), 1);
        let range = &result.excerpts[0];
        assert!(range.start > 24);
        assert!(range.start <= 67 && range.end >= 67);
        assert!(
            result
                .context
                .contains("alpha beta café\u{2028}samephysical")
        );
        assert!(!result.context.contains("fn tiny_note"));
        let expanded = index.expand(&range.id, 0, 0, 4000).unwrap();
        assert_eq!(result.context.trim_end(), expanded.context.trim_end());
        let repeat = index
            .evidence_passages_with(
                "alpha alpha beta beta",
                budget,
                1,
                None,
                None,
                Policy::default(),
            )
            .unwrap();
        assert_eq!(result.context, repeat.context);
        assert_eq!(
            result.context_tokens,
            cl100k_base()
                .unwrap()
                .encode_ordinary(&result.context)
                .len()
        );
        assert!(result.context_tokens <= budget);
    }
    let result = index
        .evidence_passages_with("alpha beta", 2000, 1, None, None, Policy::default())
        .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(&root)
        .args([
            "evidence-passages",
            "alpha beta",
            "--budget",
            "2000",
            "--limit",
            "1",
            "--text",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(String::from_utf8(output.stdout).unwrap(), result.context);
}

#[test]
fn filters_budgets_and_freshness_apply_to_physical_passages() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("source.rs"),
        "fn alpha() {\n // café\u{2028}physical\n}\n",
    )
    .unwrap();
    fs::write(dir.path().join("guide.md"), "# alpha\nalpha guide\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    for budget in [0, 1, 20, 100, 2000] {
        let a = index
            .evidence_passages_with("alpha", budget, 20, None, None, Policy::default())
            .unwrap();
        assert_eq!(
            a.context_tokens,
            cl100k_base().unwrap().encode_ordinary(&a.context).len()
        );
        assert!(a.context_tokens <= budget);
    }
    let only_docs = index
        .evidence_passages_with(
            "alpha guide",
            2000,
            20,
            Some("guide"),
            Some("docs"),
            Policy::default(),
        )
        .unwrap();
    assert_eq!(only_docs.excerpts.len(), 1);
    assert_eq!(only_docs.excerpts[0].path, "guide.md");
    assert!(
        index
            .evidence_passages_with("alpha", 2000, 0, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_passages_with(" ", 2000, 20, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_passages_with("absentword", 2000, 20, None, None, Policy::default())
            .unwrap()
            .excerpts
            .is_empty()
    );
    fs::write(root.join("source.rs"), "changed").unwrap();
    assert!(
        index
            .evidence_passages_with("alpha", 2000, 20, None, Some("code"), Policy::default())
            .is_err()
    );
}
