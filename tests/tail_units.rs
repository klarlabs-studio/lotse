use graf::units::{Unit, append_units_within_budget, pack_units_file_diverse};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

fn unit(path: &str, start: usize, text: &str) -> Unit {
    Unit {
        path: path.into(),
        start,
        end: start,
        name: None,
        kind: "function".into(),
        text: text.into(),
        calls: vec![],
    }
}

#[test]
fn append_preserves_documentation_prefix_skips_nonfits_and_deduplicates_ranges() {
    let bpe = cl100k_base().unwrap();
    let baseline = [
        unit("a.js", 1, "function first () {}"),
        unit("docs/a.md", 1, "Relevant documentation"),
    ];
    let extra = unit("a\"quoted.js", 2, "function helper () {}");
    let (picked, text) = pack_units_file_diverse(&bpe, &baseline, &[2., 1.], "first", 1000);
    let (all, expected) = append_units_within_budget(
        &bpe,
        picked.clone(),
        text.clone(),
        std::slice::from_ref(&extra),
        1000,
    );
    let budget = bpe.encode_ordinary(&expected).len();
    let huge = unit("a.js", 3, &"oversized ".repeat(1000));
    let extras = [baseline[0].clone(), huge, extra.clone(), extra];
    let (actual, context) =
        append_units_within_budget(&bpe, picked.clone(), text.clone(), &extras, budget);
    assert_eq!(context, expected);
    assert!(context.starts_with(&text));
    assert!(context.contains("\"a\\\"quoted.js\":2-2\nfunction helper () {}"));
    assert_eq!(actual.len(), all.len());
    assert_eq!(actual[1].path, "docs/a.md");
    assert_eq!(actual.len(), 3);
    let (same, unchanged) = append_units_within_budget(
        &bpe,
        picked,
        text.clone(),
        &extras,
        bpe.encode_ordinary(&text).len(),
    );
    assert_eq!(same.len(), 2);
    assert_eq!(unchanged, text);
    let (empty, empty_text) = append_units_within_budget(&bpe, vec![], String::new(), &extras, 0);
    assert!(empty.is_empty() && empty_text.is_empty());
}

#[test]
fn cli_tail_adds_called_helper_without_changing_baseline_and_checks_freshness() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("a.js"), "function dispatchNeedle () { return checkValue() }\nfunction checkValue () { return 42 }\n").unwrap();
    // A second eligible file blocks the baseline packer before a small helper.
    fs::write(
        dir.path().join("b.js"),
        format!(
            "function other () {{ return [{}] }}\n",
            "'dispatchNeedle',".repeat(200)
        ),
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
        "dispatchNeedle",
        "--focus",
        "--symbols",
        "--retain-body",
        "--file-diversity",
        "--budget",
        "200",
    ];
    let baseline = run(&args);
    assert!(baseline.status.success());
    let baseline: serde_json::Value = serde_json::from_slice(&baseline.stdout).unwrap();
    assert_eq!(baseline["units"].as_array().unwrap().len(), 1);
    let mut tail = args.to_vec();
    tail.push("--linked-tail");
    let output = run(&tail);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["linked_tail"], true);
    assert_eq!(value["units"][0], baseline["units"][0]);
    assert_eq!(value["units"][1]["name"], "checkValue");
    let context = value["context"].as_str().unwrap();
    assert!(context.starts_with(baseline["context"].as_str().unwrap()));
    assert!(context.contains("\"a.js\":2-2\nfunction checkValue () { return 42 }"));
    assert!(cl100k_base().unwrap().encode_ordinary(context).len() <= 200);
    assert!(
        !run(&["units", "dispatchNeedle", "--linked-tail"])
            .status
            .success()
    );
    tail.push("--linked");
    assert!(!run(&tail).status.success());
    tail.pop();
    for budget in ["0", "1", "20"] {
        let mut small = tail.clone();
        small[7] = budget;
        let out = run(&small);
        assert!(out.status.success());
        let v: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
        assert!(
            cl100k_base()
                .unwrap()
                .encode_ordinary(v["context"].as_str().unwrap())
                .len()
                <= budget.parse::<usize>().unwrap()
        );
    }
    fs::write(dir.path().join("a.js"), "function changed () {}\n").unwrap();
    assert!(!run(&tail).status.success());
}
