use graf::{Index, Policy, canonical_root};
use std::{fs, process::Command};

#[test]
fn evidence_handles_expand_the_delivered_range_and_widen_from_its_boundaries() {
    let dir = tempfile::tempdir().unwrap();
    let name = "a\"file.rs";
    let lines: Vec<_> = (1..=80)
        .map(|n| match n {
            2 => "fn needle() {".to_string(),
            30 => "    // needle café\u{2028}physical".into(),
            68 => "}".into(),
            _ => format!("    // filler {n}"),
        })
        .collect();
    fs::write(dir.path().join(name), lines.join("\r\n")).unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let window = index
        .evidence_with("needle", 4000, 20, None, None, Policy::default())
        .unwrap();
    let units = index
        .evidence_units_with("needle", 4000, 20, None, None, Policy::default())
        .unwrap();
    assert_eq!((units.excerpts[0].start, units.excerpts[0].end), (2, 68));
    assert_ne!(window.excerpts[0].end, units.excerpts[0].end);
    for evidence in [window, units] {
        let excerpt = &evidence.excerpts[0];
        let expanded = index.expand(&excerpt.id, 0, 0, 4000).unwrap();
        assert_eq!(expanded.context.trim_end(), evidence.context.trim_end());
        let returned = &expanded.excerpts[0];
        assert_eq!(returned.id, excerpt.id);
        assert_eq!(returned.source_hash, excerpt.source_hash);
        assert_eq!((returned.start, returned.end), (excerpt.start, excerpt.end));
        let wider = index.expand(&excerpt.id, 1, 2, 4000).unwrap();
        assert_eq!(
            wider.excerpts[0].start,
            excerpt.start.saturating_sub(1).max(1)
        );
        assert_eq!(wider.excerpts[0].end, (excerpt.end + 2).min(80));
        let all = index
            .expand(&excerpt.id, usize::MAX, usize::MAX, 4000)
            .unwrap();
        assert_eq!((all.excerpts[0].start, all.excerpts[0].end), (1, 80));
        let empty = index.expand(&excerpt.id, 0, 0, 0).unwrap();
        assert!(empty.context.is_empty() && empty.excerpts.is_empty());
        assert_eq!(empty.status, "budget_exhausted");
    }
}

#[test]
fn malformed_unknown_out_of_bounds_and_stale_range_handles_are_rejected() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("a.rs"), "fn needle() {}\n// two\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let index = Index::load(&root).unwrap();
    let located = index.locate("needle", 2000, 20, None, None).unwrap();
    let anchor = &located.hints[0].id;
    for suffix in [
        "",
        ":1",
        ":1:2:3",
        ":0:1",
        ":1:0",
        ":2:1",
        ":1:3",
        ":01:2",
        ":+1:2",
        ":1:-2",
        ":1:18446744073709551616",
        ":١:2",
        ":1:2\n",
    ] {
        let id = format!("range:{anchor}{suffix}");
        assert!(index.expand(&id, 0, 0, 2000).is_err(), "{id}");
    }
    for id in [
        "range:abcdef:1:2".to_string(),
        "range:ZZZZZZZZZZZZ:1:2".into(),
        "range:000000000000:1:2".into(),
        format!("range:{}:1:2", "a".repeat(200)),
    ] {
        assert!(index.expand(&id, 0, 0, 2000).is_err(), "{id}");
    }
    let id = format!("range:{anchor}:1:2");
    assert!(index.expand(&id, 0, 0, 2000).is_ok());
    // Original locate/search handles still resolve exactly their chunk.
    assert_eq!(
        index.expand(anchor, 0, 0, 2000).unwrap().context,
        index.expand(&id, 0, 0, 2000).unwrap().context
    );
    fs::write(root.join("a.rs"), "fn changed() {}\n// two\n").unwrap();
    assert!(index.expand(&id, 0, 0, 2000).is_err());
    Index::build(&root, false).unwrap();
    assert!(Index::load(&root).unwrap().expand(&id, 0, 0, 2000).is_err());
}

#[test]
fn cli_evidence_ids_round_trip_through_expand() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("a.rs"), "fn needle() {\n    work();\n}\n").unwrap();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_graf"))
            .arg("--root")
            .arg(dir.path())
            .args(args)
            .output()
            .unwrap()
    };
    assert!(run(&["index"]).status.success());
    for command in ["evidence", "evidence-units"] {
        let out = run(&[command, "needle"]);
        assert!(out.status.success());
        let value: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
        let out = run(&[
            "expand",
            value["excerpts"][0]["id"].as_str().unwrap(),
            "--text",
        ]);
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert_eq!(
            String::from_utf8(out.stdout).unwrap().trim_end(),
            value["context"].as_str().unwrap().trim_end()
        );
    }
}
