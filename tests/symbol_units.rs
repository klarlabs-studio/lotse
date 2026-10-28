use graf::units::{
    Unit, pack_units_focused, packing_candidates, retained_body_candidates, symbol_scores,
};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

fn unit(path: &str, name: &str) -> Unit {
    Unit {
        path: path.into(),
        name: Some(name.into()),
        kind: "function".into(),
        start: 1,
        end: 1,
        text: String::new(),
        calls: vec![],
    }
}

#[test]
fn explicit_symbols_can_recover_a_definition_with_no_body_score() {
    let units = [unit("a.js", "forward"), unit("b.js", "extractBody")];
    let scores = symbol_scores(&units, &[10.0, 0.0], "extractBody()");
    assert!(scores[1] > scores[0]);
    assert_eq!(scores, symbol_scores(&units, &[10.0, 0.0], "extractBody()"));
    assert_eq!(symbol_scores(&units, &[10.0, 0.0], ""), [0.0, 0.0]);
    assert_eq!(symbol_scores(&units, &[10.0, 0.0], "the and"), [0.0, 0.0]);
}

#[test]
fn body_eligibility_survives_symbol_bonuses_and_packing_keeps_the_union() {
    let body = [10.0, 6.0, 0.0, 1.0];
    let symbols = [1.0, 0.6, 3.0, 0.1];
    assert_eq!(packing_candidates(&symbols), [2]);
    let candidates = retained_body_candidates(&body, &symbols);
    assert_eq!(candidates, [0, 1, 2]);
    assert!(retained_body_candidates(&[0.0; 4], &[0.0; 4]).is_empty());
    let all: Vec<_> = ["a.js", "b.js", "c.js", "d.js"]
        .iter()
        .map(|p| {
            let mut u = unit(p, "f");
            u.text = "function f() {}".into();
            u
        })
        .collect();
    let units: Vec<_> = candidates.iter().map(|&i| all[i].clone()).collect();
    let scores: Vec<_> = candidates.iter().map(|&i| symbols[i]).collect();
    let bpe = cl100k_base().unwrap();
    let (picked, context) = pack_units_focused(&bpe, &units, &scores, "f", 200, 0.0);
    assert_eq!(
        picked.iter().map(|u| u.path.as_str()).collect::<Vec<_>>(),
        ["c.js", "a.js", "b.js"]
    );
    assert!(bpe.encode_ordinary(&context).len() <= 200);
    assert!(!context.contains("d.js"));
    let (_, context) = pack_units_focused(&bpe, &units, &scores, "f", 20, 0.0);
    assert!(bpe.encode_ordinary(&context).len() <= 20);
}

#[test]
fn paths_match_complete_components_and_accept_source_line_suffixes() {
    let units = [
        unit("src/notrequest.js", "send"),
        unit("lib/core/request.js", "send"),
    ];
    for query in [
        "request.js:20",
        "`core/request.js`",
        "lib\\core\\request.js",
    ] {
        let scores = symbol_scores(&units, &[10.0, 0.0], query);
        assert!(scores[1] > scores[0], "{query}: {scores:?}");
    }
    let scores = symbol_scores(&units, &[0.0, 0.0], "other/request.js");
    assert!(
        scores.iter().all(|&s| s <= 0.5),
        "no exact path bonus: {scores:?}"
    );
}

#[test]
fn cli_symbols_use_cached_metadata_and_keep_source_and_budget_checks() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("request.js"),
        "function constructRequest () {\n  return 'needle';\n}\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("helper.js"),
        "function helper () {\n  return 'needle needle needle';\n}\n",
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
    let result = run(&[
        "units",
        "request.js constructRequest()",
        "--symbols",
        "--focus",
        "--budget",
        "100",
    ]);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let result: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(result["units"][0]["path"], "request.js");
    assert_eq!(result["symbols"], true);
    let context = result["context"].as_str().unwrap();
    assert!(cl100k_base().unwrap().encode_ordinary(context).len() <= 100);
    assert!(context.contains("\"request.js\":1-3\nfunction constructRequest"));
    let retained = run(&[
        "units",
        "request.js constructRequest()",
        "--symbols",
        "--focus",
        "--retain-body",
        "--budget",
        "100",
    ]);
    assert!(retained.status.success());
    let retained: serde_json::Value = serde_json::from_slice(&retained.stdout).unwrap();
    assert_eq!(retained["retain_body"], true);
    assert!(
        cl100k_base()
            .unwrap()
            .encode_ordinary(retained["context"].as_str().unwrap())
            .len()
            <= 100
    );
    assert!(!run(&["units", "needle", "--retain-body"]).status.success());
    let default = run(&["units", "needle"]);
    let default: serde_json::Value = serde_json::from_slice(&default.stdout).unwrap();
    assert!(default.get("symbols").is_none());
    fs::write(dir.path().join("request.js"), "function changed () {}\n").unwrap();
    assert!(
        !run(&[
            "units",
            "request.js constructRequest()",
            "--symbols",
            "--focus"
        ])
        .status
        .success()
    );
}
