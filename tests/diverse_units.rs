use graf::units::{Unit, pack_units_file_diverse, pack_units_focused};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

fn unit(path: &str, line: usize) -> Unit {
    Unit {
        path: path.into(),
        start: line,
        end: line,
        name: Some("f".into()),
        kind: "function".into(),
        text: "function f () { return 'needle' }".into(),
        calls: vec![],
    }
}

#[test]
fn diversity_delivers_another_file_before_repeated_units_without_exceeding_budget() {
    let bpe = cl100k_base().unwrap();
    let units = [
        unit("a.js", 1),
        unit("a.js", 2),
        unit("a.js", 3),
        unit("b\"file.js", 1),
    ];
    let scores = [4.0, 3.0, 2.0, 1.0];
    let (_, first_two) = pack_units_file_diverse(
        &bpe,
        &[units[0].clone(), units[3].clone()],
        &[4.0, 1.0],
        "needle",
        1000,
    );
    let budget = bpe.encode_ordinary(&first_two).len();
    let (picked, context) = pack_units_file_diverse(&bpe, &units, &scores, "needle", budget);
    assert_eq!(
        picked.iter().map(|u| u.path.as_str()).collect::<Vec<_>>(),
        ["a.js", "b\"file.js"]
    );
    assert_eq!(context, first_two);
    assert!(context.contains("\"b\\\"file.js\":1-1\nfunction f ()"));
    let (legacy, _) = pack_units_focused(&bpe, &units, &scores, "needle", budget, 0.0);
    assert!(legacy.iter().all(|u| u.path == "a.js"));
    let (all, _) = pack_units_file_diverse(&bpe, &units, &scores, "needle", 1000);
    assert_eq!(
        all.iter()
            .map(|u| (u.path.as_str(), u.start))
            .collect::<Vec<_>>(),
        [("a.js", 1), ("b\"file.js", 1), ("a.js", 2), ("a.js", 3)]
    );
    assert!(
        pack_units_file_diverse(&bpe, &units, &[0.0; 4], "needle", 1000)
            .0
            .is_empty()
    );
    for budget in [0, 1, 10] {
        let (_, text) = pack_units_file_diverse(&bpe, &units, &scores, "needle", budget);
        assert!(bpe.encode_ordinary(&text).len() <= budget);
    }
}

#[test]
fn cli_diversity_preserves_source_checks_and_requires_its_preregistered_options() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("a.js"), "function first () { return 'needle' }\nfunction second () { return 'needle' }\nfunction third () { return 'needle' }\n").unwrap();
    fs::write(
        dir.path().join("b.js"),
        "function fourth () { return 'needle' }\n",
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
    let args = [
        "units",
        "needle",
        "--symbols",
        "--focus",
        "--retain-body",
        "--file-diversity",
        "--budget",
        "90",
    ];
    let output = run(&args);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["file_diversity"], true);
    assert_eq!(result["units"][0]["path"], "a.js");
    assert_eq!(result["units"][1]["path"], "b.js");
    let text = result["context"].as_str().unwrap();
    assert!(text.contains("\"b.js\":1-1\nfunction fourth ()"));
    assert!(cl100k_base().unwrap().encode_ordinary(text).len() <= 90);
    assert!(
        !run(&["units", "needle", "--file-diversity"])
            .status
            .success()
    );
    fs::write(dir.path().join("b.js"), "function changed () {}\n").unwrap();
    assert!(!run(&args).status.success());
}
