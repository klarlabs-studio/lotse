use graf::units::{Unit, linked_file_candidates};
use std::{fs, process::Command};

fn unit(path: &str) -> Unit {
    Unit {
        path: path.into(),
        start: 1,
        end: 1,
        name: None,
        kind: "function".into(),
        text: String::new(),
        calls: vec![],
    }
}

#[test]
fn directed_expansion_is_bounded_same_file_deduplicated_and_nonrecursive() {
    let units = vec![
        unit("a"),
        unit("a"),
        unit("a"),
        unit("a"),
        unit("b"),
        unit("a"),
        unit("a"),
    ];
    let edges = [
        (0, 1),
        (0, 2),
        (0, 3),
        (0, 4),
        (1, 5),
        (6, 0),
        (0, 0),
        (0, 1),
    ];
    let body = [10., 3., 4., 1., 20., 6., 8.];
    let scores = [10., 3., 4., 1., 9., 6., 8.];
    // Lower-scoring eligible repeats follow first seeds and their bounded evidence.
    assert_eq!(
        linked_file_candidates(&units, &edges, &body, &scores, &[0, 4, 2]),
        [0, 2, 1, 4]
    );
    // Zero query evidence excludes a helper even when a call edge exists.
    assert_eq!(
        linked_file_candidates(&units, &edges, &[0.; 7], &scores, &[0, 4]),
        [0, 4]
    );
    assert!(linked_file_candidates(&units, &edges, &body, &scores, &[]).is_empty());
}

#[test]
fn cli_linked_returns_called_evidence_with_exact_source_budget_and_freshness() {
    let dir = tempfile::tempdir().unwrap();
    let source = "function dispatchNeedle () { checkValue(); return 'needle needle needle needle needle' }\nfunction checkValue () { return 'needle' }\n";
    fs::write(dir.path().join("a.js"), source).unwrap();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_graf"))
            .arg("--root")
            .arg(dir.path())
            .args(args)
            .output()
            .unwrap()
    };
    assert!(run(&["index"]).status.success());
    assert!(
        !run(&["units", "dispatchNeedle needle", "--linked"])
            .status
            .success()
    );
    let args = [
        "units",
        "dispatchNeedle needle",
        "--focus",
        "--symbols",
        "--retain-body",
        "--file-diversity",
        "--linked",
        "--budget",
        "200",
    ];
    let output = run(&args);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["linked"], true);
    let units = value["units"].as_array().unwrap();
    assert_eq!(units.len(), 2);
    assert_eq!(units[1]["name"], "checkValue");
    let text = value["context"].as_str().unwrap();
    assert!(text.contains("\"a.js\":2-2\nfunction checkValue () { return 'needle' }"));
    assert!(
        tiktoken_rs::cl100k_base()
            .unwrap()
            .encode_ordinary(text)
            .len()
            <= 200
    );
    for budget in ["0", "1", "20"] {
        let mut small = args;
        small[8] = budget;
        let result = run(&small);
        assert!(result.status.success());
        let value: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
        assert!(
            tiktoken_rs::cl100k_base()
                .unwrap()
                .encode_ordinary(value["context"].as_str().unwrap())
                .len()
                <= budget.parse::<usize>().unwrap()
        );
    }
    fs::write(dir.path().join("a.js"), "function replaced () {}\n").unwrap();
    assert!(!run(&args).status.success());
}
