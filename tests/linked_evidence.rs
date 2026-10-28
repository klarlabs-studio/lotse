use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

#[test]
fn linked_tail_preserves_prefix_recovers_one_hop_and_roundtrips_ranges() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("entry.rs"), "fn needle() { bridge(); }\n").unwrap();
    fs::write(dir.path().join("helper.rs"), "fn bridge() { distant(); }\n").unwrap();
    fs::write(dir.path().join("other.rs"), "fn distant() { work(); }\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let baseline = index
        .evidence_units_with("needle", 2000, 1, None, None, Policy::default())
        .unwrap();
    let linked = index
        .evidence_linked_with("needle", 2000, 1, None, None, Policy::default())
        .unwrap();
    assert!(linked.context.starts_with(&baseline.context));
    assert_eq!(linked.excerpts.len(), 2);
    assert_eq!(linked.excerpts[0].id, baseline.excerpts[0].id);
    let extra = &linked.excerpts[1];
    assert_eq!(extra.path, "helper.rs");
    let expanded = index.expand(&extra.id, 0, 0, 2000).unwrap();
    assert_eq!(
        expanded.context,
        "\"helper.rs\":1-1\nfn bridge() { distant(); }"
    );
    assert_eq!(extra.source_hash, expanded.excerpts[0].source_hash);
    assert_eq!(
        linked.context_tokens,
        cl100k_base()
            .unwrap()
            .encode_ordinary(&linked.context)
            .len()
    );
    assert!(linked.context_tokens <= 2000);
    for (scope, kind) in [(Some("entry"), None), (None, Some("docs"))] {
        let base = index
            .evidence_units_with("needle", 2000, 1, scope, kind, Policy::default())
            .unwrap();
        let result = index
            .evidence_linked_with("needle", 2000, 1, scope, kind, Policy::default())
            .unwrap();
        assert_eq!(result.context, base.context);
    }
    let output = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(&root)
        .args([
            "evidence-linked",
            "needle",
            "--budget",
            "2000",
            "--limit",
            "1",
            "--text",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().trim_end(),
        linked.context.trim_end()
    );
    fs::write(root.join("helper.rs"), "stale\n").unwrap();
    assert!(
        index
            .evidence_linked_with("needle", 2000, 1, None, None, Policy::default())
            .is_err()
    );
}

#[test]
fn linked_tail_obeys_small_budgets_and_four_file_cap() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("entry.rs"),
        "fn needle() { a(); b(); c(); d(); e(); }\n",
    )
    .unwrap();
    for name in ["a", "b", "c", "d", "e"] {
        fs::write(
            dir.path().join(format!("{name}.rs")),
            format!("fn {name}() {{ work(); }}\n"),
        )
        .unwrap();
    }
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let large = index
        .evidence_linked_with("needle", 2000, 1, None, None, Policy::default())
        .unwrap();
    assert_eq!(large.excerpts.len(), 5);
    let repeat = index
        .evidence_linked_with("needle", 2000, 1, None, None, Policy::default())
        .unwrap();
    assert_eq!(large.context, repeat.context);
    for budget in [0, 1, 20, 40, large.context_tokens - 1] {
        let baseline = index
            .evidence_units_with("needle", budget, 1, None, None, Policy::default())
            .unwrap();
        let linked = index
            .evidence_linked_with("needle", budget, 1, None, None, Policy::default())
            .unwrap();
        assert!(linked.context.starts_with(&baseline.context));
        assert!(linked.context_tokens <= budget);
        assert!(linked.excerpts.len() <= 5);
    }
    assert!(
        index
            .evidence_linked_with("needle", 2000, 0, None, None, Policy::default())
            .is_err()
    );
}
