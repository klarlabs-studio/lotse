use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

#[test]
fn complete_relevant_function_replaces_short_window_without_changing_file_selection() {
    let dir = tempfile::tempdir().unwrap();
    let mut lines = vec![
        "fn irrelevant() { unrelated(); }".to_string(),
        "fn needle() {".into(),
    ];
    for n in 0..45 {
        lines.push(if n == 25 {
            "    needle_work();".into()
        } else {
            format!("    // context {n}")
        });
    }
    lines.push("}".into());
    fs::write(dir.path().join("a.rs"), lines.join("\r\n")).unwrap();
    fs::write(dir.path().join("docs.md"), "# needle\nDocumentation\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let baseline = index
        .evidence_with("needle", 2000, 20, None, None, Policy::default())
        .unwrap();
    let selected = index
        .evidence_units_with("needle", 2000, 20, None, None, Policy::default())
        .unwrap();
    assert_eq!(
        baseline
            .excerpts
            .iter()
            .map(|e| &e.path)
            .collect::<Vec<_>>(),
        selected
            .excerpts
            .iter()
            .map(|e| &e.path)
            .collect::<Vec<_>>()
    );
    let unit = selected.excerpts.iter().find(|e| e.path == "a.rs").unwrap();
    assert_eq!((unit.start, unit.end), (2, 48));
    let original = baseline.excerpts.iter().find(|e| e.path == "a.rs").unwrap();
    assert!(original.end - original.start < 24);
    let exact = format!("\"a.rs\":2-48\n{}\n\n", lines[1..48].join("\n"));
    assert!(selected.context.contains(&exact));
    let bpe = cl100k_base().unwrap();
    assert!(bpe.encode_ordinary(&exact).len() <= 1000);
    assert_eq!(
        selected.context_tokens,
        bpe.encode_ordinary(&selected.context).len()
    );
    assert!(selected.context_tokens <= 2000);
    let docs = index
        .evidence_units_with("needle", 2000, 20, None, Some("docs"), Policy::default())
        .unwrap();
    assert_eq!(docs.excerpts.len(), 1);
    assert_eq!(docs.excerpts[0].path, "docs.md");
    assert!(
        index
            .evidence_units_with("needle", 2000, 0, None, None, Policy::default())
            .is_err()
    );
    fs::write(root.join("a.rs"), "stale\n").unwrap();
    assert!(
        index
            .evidence_units_with("needle", 2000, 20, None, None, Policy::default())
            .is_err()
    );
}

#[test]
fn unfittable_units_keep_exact_physical_window_fallback_and_cli_limits() {
    let dir = tempfile::tempdir().unwrap();
    let source = format!(
        "fn needle() {{\n    // needle café\u{2028}physical\n{}\n}}",
        (0..70)
            .map(|n| format!("    // filler {n}"))
            .collect::<Vec<_>>()
            .join("\n")
    );
    fs::write(dir.path().join("a.rs"), source).unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    for budget in [0, 1, 20, 100] {
        let baseline = index
            .evidence_with("needle", budget, 20, None, None, Policy::default())
            .unwrap();
        let selected = index
            .evidence_units_with("needle", budget, 20, None, None, Policy::default())
            .unwrap();
        assert_eq!(selected.context, baseline.context);
        assert_eq!(selected.context_tokens, baseline.context_tokens);
    }
    let out = Command::new(env!("CARGO_BIN_EXE_graf"))
        .arg("--root")
        .arg(&root)
        .args(["evidence-units", "needle", "--budget", "100", "--text"])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let expected = index
        .evidence_with("needle", 100, 20, None, None, Policy::default())
        .unwrap();
    assert_eq!(String::from_utf8(out.stdout).unwrap(), expected.context);
    assert!(expected.context.contains("café\u{2028}physical"));
}
