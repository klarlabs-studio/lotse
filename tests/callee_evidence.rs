use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

#[test]
fn references_are_deterministic_and_physical_handles_expand() {
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
        .evidence_callees_with("alpha beta", 8000, 6, None, None, Policy::default())
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
        .evidence_callees_with(
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
            .evidence_callees_with("alpha beta", budget, 6, None, None, Policy::default())
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
            "evidence-callees",
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
            .evidence_callees_with("alpha", budget, 20, None, None, Policy::default())
            .unwrap();
        assert_eq!(
            a.context_tokens,
            cl100k_base().unwrap().encode_ordinary(&a.context).len()
        );
        assert!(a.context_tokens <= budget);
    }
    let only_docs = index
        .evidence_callees_with(
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
            .evidence_callees_with("alpha", 2000, 0, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_callees_with(" ", 2000, 20, None, None, Policy::default())
            .is_err()
    );
    assert!(
        index
            .evidence_callees_with("absentword", 2000, 20, None, None, Policy::default())
            .unwrap()
            .excerpts
            .is_empty()
    );
    fs::write(root.join("source.rs"), "changed").unwrap();
    assert!(
        index
            .evidence_callees_with("alpha", 2000, 20, None, Some("code"), Policy::default())
            .is_err()
    );
}

#[test]
fn explicit_reference_recovers_a_source_file_without_a_query_match() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("guide.md"),
        "# quasar\nUse `resolve_hidden` here.\n",
    )
    .unwrap();
    fs::write(dir.path().join("helper.rs"), "fn resolve_hidden() {}\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    assert_eq!(
        index
            .evidence_fused_with("quasar", 2000, 20, None, None, Policy::default())
            .unwrap()
            .matched_files,
        1
    );
    let response = index
        .evidence_callees_with("quasar", 2000, 20, None, None, Policy::default())
        .unwrap();
    assert_eq!(
        response
            .excerpts
            .iter()
            .map(|e| e.path.as_str())
            .collect::<Vec<_>>(),
        ["guide.md", "helper.rs"]
    );
    let code = index
        .evidence_callees_with("quasar", 2000, 20, None, Some("code"), Policy::default())
        .unwrap();
    assert_eq!(code.excerpts.len(), 1);
    assert_eq!(code.excerpts[0].path, "helper.rs");
    assert!(
        index
            .evidence_callees_with("quasar", 2000, 20, Some("helper"), None, Policy::default())
            .unwrap()
            .excerpts
            .is_empty()
    );
    for excerpt in &response.excerpts {
        assert!(
            response.context.contains(
                index
                    .expand(&excerpt.id, 0, 0, 2000)
                    .unwrap()
                    .context
                    .trim_end()
            )
        );
    }
    fs::write(root.join("guide.md"), "changed").unwrap();
    assert!(
        index
            .evidence_callees_with("quasar", 2000, 20, None, Some("code"), Policy::default())
            .is_err()
    );
}

#[test]
fn ambiguous_and_unquoted_prose_names_do_not_introduce_files() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("guide.md"),
        "# quasar\nUse `ambiguous_helper`; prose_helper is mentioned.\n",
    )
    .unwrap();
    for name in ["one.rs", "two.rs"] {
        fs::write(dir.path().join(name), "fn ambiguous_helper() {}\n").unwrap();
    }
    fs::write(dir.path().join("three.rs"), "fn prose_helper() {}\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let response = index
        .evidence_callees_with("quasar", 2000, 20, None, None, Policy::default())
        .unwrap();
    assert_eq!(response.excerpts.len(), 1);
    assert_eq!(response.excerpts[0].path, "guide.md");
}

#[test]
fn rescue_preserves_fused_prefix_and_skips_non_fitting_whole_functions() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("guide.md"),
        "# quasar\nUse `resolve_hidden` here.\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("helper.rs"),
        "fn resolve_hidden() {\n let x = 42;\n}\n",
    )
    .unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    for budget in [0, 1, 20, 40, 100, 2000] {
        let base = index
            .evidence_fused_with("quasar", budget, 1, None, None, Policy::default())
            .unwrap();
        let rescue = index
            .evidence_callees_with("quasar", budget, 1, None, None, Policy::default())
            .unwrap();
        assert!(rescue.context.starts_with(&base.context));
        assert_eq!(
            serde_json::to_value(&rescue.excerpts[..base.excerpts.len()]).unwrap(),
            serde_json::to_value(&base.excerpts).unwrap()
        );
        assert!(rescue.context_tokens <= budget);
        for e in rescue.excerpts.iter().skip(base.excerpts.len()) {
            assert_eq!((e.path.as_str(), e.start, e.end), ("helper.rs", 1, 3));
            assert!(
                rescue
                    .context
                    .contains(index.expand(&e.id, 0, 0, 2000).unwrap().context.trim_end())
            );
        }
        if budget == 2000 {
            assert_eq!(rescue.excerpts.len(), base.excerpts.len() + 1);
        }
    }
}

#[test]
fn rescue_caps_new_files_at_four_in_deterministic_order() {
    let dir = tempfile::tempdir().unwrap();
    let mut doc = String::from("# quasar\n");
    for n in 0..6 {
        doc.push_str(&format!("Use `hidden_target_{n}`.\n"));
        fs::write(
            dir.path().join(format!("helper{n}.rs")),
            format!("fn hidden_target_{n}() {{}}\n"),
        )
        .unwrap();
    }
    fs::write(dir.path().join("guide.md"), doc).unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let base = index
        .evidence_fused_with("quasar", 8000, 1, None, None, Policy::default())
        .unwrap();
    let rescue = index
        .evidence_callees_with("quasar", 8000, 1, None, None, Policy::default())
        .unwrap();
    assert!(rescue.context.starts_with(&base.context));
    assert_eq!(
        rescue
            .excerpts
            .iter()
            .skip(base.excerpts.len())
            .map(|e| e.path.as_str())
            .collect::<Vec<_>>(),
        ["helper0.rs", "helper1.rs", "helper2.rs", "helper3.rs"]
    );
}

#[test]
fn callees_preserve_rescue_prefix_expand_whole_helpers_and_do_not_recurse() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("guide.md"),
        "# quasar\nUse `entry_hidden`.\n",
    )
    .unwrap();
    fs::write(dir.path().join("helper.rs"), "fn entry_hidden() { guard_hidden(); }\n\nfn guard_hidden() { deeper_hidden(); }\n\nfn deeper_hidden() {}\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    for budget in [0, 1, 20, 60, 100, 2000] {
        let base = index
            .evidence_rescue_with("quasar", budget, 1, None, None, Policy::default())
            .unwrap();
        let result = index
            .evidence_callees_with("quasar", budget, 1, None, None, Policy::default())
            .unwrap();
        assert!(result.context.starts_with(&base.context));
        assert_eq!(
            serde_json::to_value(&result.excerpts[..base.excerpts.len()]).unwrap(),
            serde_json::to_value(&base.excerpts).unwrap()
        );
        assert!(result.context_tokens <= budget);
        assert_eq!(
            result.context_tokens,
            cl100k_base()
                .unwrap()
                .encode_ordinary(&result.context)
                .len()
        );
        for e in result.excerpts.iter().skip(base.excerpts.len()) {
            assert_eq!((e.path.as_str(), e.start, e.end), ("helper.rs", 3, 3));
            assert!(
                result
                    .context
                    .contains(index.expand(&e.id, 0, 0, 2000).unwrap().context.trim_end())
            );
        }
        if budget == 2000 {
            assert_eq!(result.excerpts.len(), base.excerpts.len() + 1);
        }
    }
}

#[test]
fn callees_cap_helpers_and_exclude_cross_file_calls() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("guide.md"),
        "# quasar\nUse `entry_hidden`.\n",
    )
    .unwrap();
    let mut source = String::from("fn entry_hidden() {\n");
    for n in 0..6 {
        source.push_str(&format!(" helper_hidden_{n}();\n"));
    }
    source.push_str(" cross_hidden();\n}\n\n");
    for n in 0..6 {
        source.push_str(&format!("fn helper_hidden_{n}() {{}}\n\n"));
    }
    fs::write(dir.path().join("helper.rs"), source).unwrap();
    fs::write(dir.path().join("other.rs"), "fn cross_hidden() {}\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let base = index
        .evidence_rescue_with("quasar", 8000, 1, None, None, Policy::default())
        .unwrap();
    let result = index
        .evidence_callees_with("quasar", 8000, 1, None, None, Policy::default())
        .unwrap();
    assert!(result.context.starts_with(&base.context));
    let extra = &result.excerpts[base.excerpts.len()..];
    assert_eq!(extra.len(), 4);
    assert!(
        extra
            .iter()
            .all(|e| e.path == "helper.rs" && e.start == e.end)
    );
    assert!(extra.windows(2).all(|pair| pair[0].start < pair[1].start));
    assert!(!result.context.contains("fn cross_hidden"));
}
