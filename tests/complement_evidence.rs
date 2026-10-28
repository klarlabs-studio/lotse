use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

#[test]
fn complement_preserves_structural_prefix_and_appends_only_missing_terms() {
    let dir = tempfile::tempdir().unwrap();
    let mut lines = vec!["fn alpha() {", " // alpha alpha alpha", "}"];
    lines.extend(vec!["// unrelated"; 45]);
    lines.extend(["fn beta() {", " // beta café\u{2028}physical", "}"]);
    fs::write(dir.path().join("a.rs"), lines.join("\r\n")).unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    for budget in [0, 1, 20, 100, 500, 2000, 8000] {
        let base = index
            .evidence_families_with("alpha beta", budget, 1, None, None, Policy::default())
            .unwrap();
        let result = index
            .evidence_complement_with("alpha beta", budget, 1, None, None, Policy::default())
            .unwrap();
        assert!(result.context.starts_with(&base.context));
        assert_eq!(result.status, base.status);
        assert_eq!(result.matched_files, base.matched_files);
        assert!(result.context_tokens <= budget);
        assert_eq!(
            result.context_tokens,
            cl100k_base()
                .unwrap()
                .encode_ordinary(&result.context)
                .len()
        );
        if budget >= 500 {
            assert_eq!(base.excerpts.len(), 1);
            assert_eq!(result.excerpts.len(), 2);
            assert!(result.context.contains("fn alpha"));
            assert!(result.context.contains("fn beta"));
            let first = &result.excerpts[0];
            let second = &result.excerpts[1];
            assert!(first.end < second.start || second.end < first.start);
        }
        for e in &result.excerpts {
            let expanded = index.expand(&e.id, 0, 0, 8000).unwrap();
            assert!(result.context.contains(expanded.context.trim_end()));
        }
    }
    let base = index
        .evidence_families_with("alpha", 2000, 1, None, None, Policy::default())
        .unwrap();
    let result = index
        .evidence_complement_with("alpha", 2000, 1, None, None, Policy::default())
        .unwrap();
    assert_eq!(result.context, base.context);
    let result = index
        .evidence_complement_with("alpha beta", 2000, 1, None, None, Policy::default())
        .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(&root)
        .args([
            "evidence-complement",
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
            .evidence_complement_with("alpha", budget, 20, None, None, Policy::default())
            .unwrap();
        assert_eq!(
            a.context_tokens,
            cl100k_base().unwrap().encode_ordinary(&a.context).len()
        );
        assert!(a.context_tokens <= budget);
    }
    let only_docs = index
        .evidence_complement_with(
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
            .evidence_complement_with("alpha", 2000, 0, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_complement_with(" ", 2000, 20, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_complement_with("absentword", 2000, 20, None, None, Policy::default())
            .unwrap()
            .excerpts
            .is_empty()
    );
    fs::write(root.join("source.rs"), "changed").unwrap();
    assert!(
        index
            .evidence_complement_with("alpha", 2000, 20, None, Some("code"), Policy::default())
            .is_err()
    );
}
