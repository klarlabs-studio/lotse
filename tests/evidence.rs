use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

#[test]
fn locate_first_evidence_shares_budget_and_preserves_exact_physical_source_ranges() {
    let dir = tempfile::tempdir().unwrap();
    // A quote must be escaped in the JSON context; Windows file names cannot contain one.
    let name = if cfg!(windows) {
        "a file.rs"
    } else {
        "a\"file.rs"
    };
    let source = (1..=50)
        .map(|n| {
            if n == 25 {
                "// needle café\u{2028}same physical line".into()
            } else {
                format!("// filler {n}")
            }
        })
        .collect::<Vec<String>>()
        .join("\n");
    fs::write(dir.path().join(name), source.replace('\n', "\r\n")).unwrap();
    fs::write(
        dir.path().join("docs.md"),
        "# needle\nDocumentation content\n",
    )
    .unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let bpe = cl100k_base().unwrap();
    for budget in [0, 1, 20, 100, 2000] {
        let located = index.locate("needle", budget, 20, None, None).unwrap();
        let response = index
            .evidence_with("needle", budget, 20, None, None, Policy::default())
            .unwrap();
        assert!(bpe.encode_ordinary(&response.context).len() <= budget);
        assert_eq!(
            response.context_tokens,
            bpe.encode_ordinary(&response.context).len()
        );
        let share = budget / located.hints.len().max(1);
        for excerpt in &response.excerpts {
            let src = fs::read_to_string(root.join(&excerpt.path)).unwrap();
            let lines: Vec<_> = src.lines().collect();
            let rendered = format!(
                "{}:{}-{}\n{}\n\n",
                serde_json::to_string(&excerpt.path).unwrap(),
                excerpt.start,
                excerpt.end,
                lines[excerpt.start - 1..excerpt.end].join("\n")
            );
            assert!(response.context.contains(&rendered));
            assert!(bpe.encode_ordinary(&rendered).len() <= share);
            let hint = located
                .hints
                .iter()
                .find(|h| h.path == excerpt.path)
                .unwrap();
            assert!(excerpt.start <= hint.line && hint.line <= excerpt.end);
            assert!(excerpt.end - excerpt.start < 24);
            assert_eq!(
                excerpt.id,
                format!("range:{}:{}:{}", hint.id, excerpt.start, excerpt.end)
            );
        }
        if budget == 2000 {
            assert_eq!(response.excerpts.len(), 2);
            assert!(
                response
                    .context
                    .contains("needle café\u{2028}same physical line")
            );
            let key = format!("{}:", serde_json::to_string(name).unwrap());
            assert!(response.context.contains(&key));
        }
    }
    let docs = index
        .evidence_with("needle", 2000, 20, None, Some("docs"), Policy::default())
        .unwrap();
    assert_eq!(docs.excerpts.len(), 1);
    assert_eq!(docs.excerpts[0].path, "docs.md");
    assert!(
        index
            .evidence_with("needle", 2000, 0, None, None, Policy::default())
            .is_err()
    );
    fs::write(root.join(name), "changed\n").unwrap();
    assert!(
        index
            .evidence_with("needle", 2000, 20, None, None, Policy::default())
            .is_err()
    );
}

#[test]
fn cli_evidence_reports_excerpts_and_omits_an_unfittable_anchor_line() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("a.rs"),
        format!("// needle {}\n", "payload ".repeat(1000)),
    )
    .unwrap();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_graf"))
            .arg("--root")
            .arg(dir.path())
            .args(args)
            .output()
            .unwrap()
    };
    assert!(run(&["index"]).status.success());
    let out = run(&["evidence", "needle", "--budget", "200"]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let value: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(value["output_kind"], "source_excerpts");
    assert_eq!(value["status"], "budget_exhausted");
    assert_eq!(value["context"], "");
    assert!(!value["experimental"].as_str().unwrap().is_empty());
    assert!(
        !run(&["evidence", "needle", "--limit", "0"])
            .status
            .success()
    );
    let out = run(&["evidence", "zzzzzznomatch", "--text"]);
    assert!(out.status.success() && out.stdout.is_empty());
}
