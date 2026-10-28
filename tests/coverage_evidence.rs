use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

#[test]
fn distinct_coverage_rewards_breadth_and_keeps_physical_handles() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("broad.rs"),
        "fn alpha_beta() {\r\n    alpha(); beta();\r\n}\r\n",
    )
    .unwrap();
    for n in 0..20 {
        let term = if n % 2 == 0 { "alpha" } else { "beta" };
        fs::write(
            dir.path().join(format!("narrow{n}.rs")),
            format!("fn single() {{ {} }}", format!("{term}(); ").repeat(30)),
        )
        .unwrap();
    }
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let result = index
        .evidence_coverage_with("alpha beta", 4000, 1, None, None, Policy::default())
        .unwrap();
    assert_eq!(result.excerpts[0].path, "broad.rs");
    let expanded = index.expand(&result.excerpts[0].id, 0, 0, 4000).unwrap();
    assert_eq!(result.context.trim_end(), expanded.context.trim_end());
    let repeated = index
        .evidence_coverage_with(
            "alpha alpha beta beta absentword",
            4000,
            1,
            None,
            None,
            Policy::default(),
        )
        .unwrap();
    assert_eq!(result.context, repeated.context);
    assert_eq!(result.excerpts[0].score, repeated.excerpts[0].score);
    let command = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(&root)
        .args([
            "evidence-coverage",
            "alpha beta",
            "--budget",
            "4000",
            "--limit",
            "1",
            "--text",
        ])
        .output()
        .unwrap();
    assert!(command.status.success());
    assert_eq!(String::from_utf8(command.stdout).unwrap(), result.context);
}

#[test]
fn one_term_is_unchanged_and_filters_budgets_and_freshness_apply() {
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
            .evidence_coverage_with("alpha", budget, 20, None, None, Policy::default())
            .unwrap();
        let b = index
            .evidence_units_with("alpha", budget, 20, None, None, Policy::default())
            .unwrap();
        assert_eq!(a.context, b.context);
        assert_eq!(
            a.context_tokens,
            cl100k_base().unwrap().encode_ordinary(&a.context).len()
        );
        assert!(a.context_tokens <= budget);
    }
    let only_docs = index
        .evidence_coverage_with(
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
            .evidence_coverage_with("alpha", 2000, 0, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_coverage_with(" ", 2000, 20, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_coverage_with("absentword", 2000, 20, None, None, Policy::default())
            .unwrap()
            .excerpts
            .is_empty()
    );
    fs::write(root.join("source.rs"), "changed").unwrap();
    assert!(
        index
            .evidence_coverage_with("alpha", 2000, 20, None, Some("code"), Policy::default())
            .is_err()
    );
}
