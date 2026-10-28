use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

fn setup(source: &str) -> (tempfile::TempDir, Index) {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("entry.rs"), "fn needle() { bridge(); }\n").unwrap();
    fs::write(dir.path().join("helper.rs"), source).unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    (dir, index)
}

#[test]
fn bundles_deliver_called_guard_bodies_with_exact_expand_handles() {
    let (dir, index) = setup(
        "fn bridge() { guard(); guard(); }\r\nfn guard() { deep(); }\r\nfn deep() { work(); }\r\n",
    );
    let baseline = index
        .evidence_units_with("needle", 2000, 1, None, None, Policy::default())
        .unwrap();
    let bundle = index
        .evidence_bundles_with("needle", 2000, 1, None, None, Policy::default())
        .unwrap();
    assert!(bundle.context.starts_with(&baseline.context));
    assert_eq!(bundle.excerpts.len(), 3);
    assert_eq!((bundle.excerpts[1].start, bundle.excerpts[1].end), (1, 1));
    assert_eq!((bundle.excerpts[2].start, bundle.excerpts[2].end), (2, 2));
    assert!(!bundle.context.contains("fn deep"));
    for e in &bundle.excerpts {
        let expanded = index.expand(&e.id, 0, 0, 2000).unwrap();
        assert_eq!(expanded.excerpts[0].source_hash, e.source_hash);
        assert_eq!(
            (expanded.excerpts[0].start, expanded.excerpts[0].end),
            (e.start, e.end)
        );
        assert!(bundle.context.contains(&expanded.context));
    }
    assert_eq!(
        bundle.context_tokens,
        cl100k_base()
            .unwrap()
            .encode_ordinary(&bundle.context)
            .len()
    );
    for (scope, kind) in [(Some("entry"), None), (None, Some("docs"))] {
        let baseline = index
            .evidence_units_with("needle", 2000, 1, scope, kind, Policy::default())
            .unwrap();
        let result = index
            .evidence_bundles_with("needle", 2000, 1, scope, kind, Policy::default())
            .unwrap();
        assert_eq!(result.context, baseline.context);
    }
    let out = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(dir.path())
        .args([
            "evidence-bundles",
            "needle",
            "--limit",
            "1",
            "--budget",
            "2000",
            "--text",
        ])
        .output()
        .unwrap();
    assert!(out.status.success());
    assert_eq!(
        String::from_utf8(out.stdout).unwrap().trim_end(),
        bundle.context.trim_end()
    );
    fs::write(dir.path().join("helper.rs"), "stale\n").unwrap();
    assert!(
        index
            .evidence_bundles_with("needle", 2000, 1, None, None, Policy::default())
            .is_err()
    );
}

#[test]
fn an_unfittable_bundle_never_leaks_a_partial_seed_or_helper() {
    let (_dir, index) = setup(&format!(
        "fn bridge() {{ guard(); }}\nfn guard() {{\n{}\n}}\n",
        "    // context café\u{2028}physical\n".repeat(50)
    ));
    // The linked seed alone fits, but its full guard body does not.
    let linked = index
        .evidence_linked_with("needle", 150, 1, None, None, Policy::default())
        .unwrap();
    assert!(linked.context.contains("fn bridge"));
    for budget in [0, 1, 20, 150] {
        let base = index
            .evidence_units_with("needle", budget, 1, None, None, Policy::default())
            .unwrap();
        let bundle = index
            .evidence_bundles_with("needle", budget, 1, None, None, Policy::default())
            .unwrap();
        assert_eq!(bundle.context, base.context);
        assert!(bundle.context_tokens <= budget);
    }
    assert!(
        index
            .evidence_bundles_with("needle", 2000, 0, None, None, Policy::default())
            .is_err()
    );
}

#[test]
fn bundles_bound_helpers_and_files_and_do_not_follow_cycles() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("entry.rs"),
        "fn needle() { a(); b(); c(); d(); e(); }\n",
    )
    .unwrap();
    for name in ["a", "b", "c", "d", "e"] {
        fs::write(dir.path().join(format!("{name}.rs")), format!("fn {name}() {{ {name}1(); {name}2(); {name}3(); }}\nfn {name}1() {{ {name}(); }}\nfn {name}2() {{ work(); }}\nfn {name}3() {{ work(); }}\n")).unwrap();
    }
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let result = index
        .evidence_bundles_with("needle", 4000, 1, None, None, Policy::default())
        .unwrap();
    assert_eq!(result.excerpts.len(), 13); // baseline + four bundles of three
    for path in ["a.rs", "b.rs", "c.rs", "d.rs"] {
        let ranges: Vec<_> = result
            .excerpts
            .iter()
            .filter(|e| e.path == path)
            .map(|e| e.start)
            .collect();
        assert_eq!(ranges, [1, 2, 3]);
    }
    assert!(!result.excerpts.iter().any(|e| e.path == "e.rs"));
    assert_eq!(
        result.context,
        index
            .evidence_bundles_with("needle", 4000, 1, None, None, Policy::default())
            .unwrap()
            .context
    );
}
