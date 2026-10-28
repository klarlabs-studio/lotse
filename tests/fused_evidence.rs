use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

#[test]
fn fusion_is_deterministic_and_physical_handles_expand() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir(dir.path().join("tests")).unwrap();
    fs::write(
        dir.path().join("source.rs"),
        "fn alpha() {\r\n // café\u{2028}physical\r\n}\r\n",
    )
    .unwrap();
    fs::write(dir.path().join("tests/check.rs"), "fn beta() {}\n").unwrap();
    for n in 0..4 {
        fs::write(
            dir.path().join(format!("guide{n}.md")),
            "# alpha beta\nalpha beta guide\n",
        )
        .unwrap();
    }
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let result = index
        .evidence_fused_with("alpha beta", 8000, 6, None, None, Policy::default())
        .unwrap();
    let paths: Vec<_> = result.excerpts.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(
        paths,
        [
            "source.rs",
            "guide0.md",
            "tests/check.rs",
            "guide1.md",
            "guide2.md",
            "guide3.md"
        ]
    );
    assert_eq!(result.matched_files, 6);
    for (n, e) in result.excerpts.iter().enumerate() {
        assert_eq!(e.score, Some(1.0 / (n + 1) as f64));
        let expanded = index.expand(&e.id, 0, 0, 8000).unwrap();
        assert!(result.context.contains(expanded.context.trim_end()));
    }
    let repeated = index
        .evidence_fused_with(
            "alpha alpha beta beta",
            8000,
            6,
            None,
            None,
            Policy::default(),
        )
        .unwrap();
    assert_eq!(result.context, repeated.context);
    for budget in [0, 1, 20, 100, 2000] {
        let r = index
            .evidence_fused_with("alpha beta", budget, 6, None, None, Policy::default())
            .unwrap();
        assert_eq!(
            r.context_tokens,
            cl100k_base().unwrap().encode_ordinary(&r.context).len()
        );
        assert!(r.context_tokens <= budget);
    }
    let output = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(&root)
        .args([
            "evidence-fused",
            "alpha beta",
            "--budget",
            "8000",
            "--limit",
            "6",
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
            .evidence_fused_with("alpha", budget, 20, None, None, Policy::default())
            .unwrap();
        assert_eq!(
            a.context_tokens,
            cl100k_base().unwrap().encode_ordinary(&a.context).len()
        );
        assert!(a.context_tokens <= budget);
    }
    let only_docs = index
        .evidence_fused_with(
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
            .evidence_fused_with("alpha", 2000, 0, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_fused_with(" ", 2000, 20, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_fused_with("absentword", 2000, 20, None, None, Policy::default())
            .unwrap()
            .excerpts
            .is_empty()
    );
    fs::write(root.join("source.rs"), "changed").unwrap();
    assert!(
        index
            .evidence_fused_with("alpha", 2000, 20, None, Some("code"), Policy::default())
            .is_err()
    );
}

#[test]
fn cached_call_signal_can_recover_a_file_without_a_query_chunk() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("entry.rs"), "fn alpha() { beta(); }\n").unwrap();
    fs::write(dir.path().join("helper.rs"), "fn beta() {}\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let baseline = index
        .evidence_stratified_with("alpha", 2000, 20, None, None, Policy::default())
        .unwrap();
    assert_eq!(baseline.matched_files, 1);
    let fused = index
        .evidence_fused_with("alpha", 2000, 20, None, None, Policy::default())
        .unwrap();
    assert_eq!(fused.matched_files, 2);
    assert_eq!(
        fused
            .excerpts
            .iter()
            .map(|e| e.path.as_str())
            .collect::<Vec<_>>(),
        ["entry.rs", "helper.rs"]
    );
    let filtered = index
        .evidence_fused_with("alpha", 2000, 20, Some("entry"), None, Policy::default())
        .unwrap();
    assert_eq!(filtered.excerpts.len(), 1);
    assert_eq!(filtered.excerpts[0].path, "entry.rs");
    for excerpt in &fused.excerpts {
        assert!(
            fused.context.contains(
                index
                    .expand(&excerpt.id, 0, 0, 2000)
                    .unwrap()
                    .context
                    .trim_end()
            )
        );
    }
}
