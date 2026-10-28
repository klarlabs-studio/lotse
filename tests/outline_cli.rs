use graf::{Index, canonical_root};
use std::{fs, process::Command};
use tiktoken_rs::cl100k_base;

#[test]
fn cli_outline_pages_are_bounded_and_legacy_output_is_preserved() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("sample.ts"),
        "function alpha() {}\nfunction beta() {}\nfunction gamma() {}\n",
    )
    .unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, false).unwrap();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_graf"))
            .arg("--root")
            .arg(&root)
            .args(args)
            .output()
            .unwrap()
    };
    let legacy = run(&["outline", "sample.ts"]);
    assert!(legacy.status.success());
    let legacy: serde_json::Value = serde_json::from_slice(&legacy.stdout).unwrap();
    assert_eq!(legacy["functions"].as_array().unwrap().len(), 3);
    assert!(legacy.get("budget").is_none());
    let page = run(&["outline", "sample.ts", "--budget", "100", "--limit", "1"]);
    assert!(page.status.success());
    let page: serde_json::Value = serde_json::from_slice(&page.stdout).unwrap();
    assert_eq!(page["next_offset"], 1);
    assert_eq!(page["functions"].as_array().unwrap().len(), 1);
    assert!(page["context_tokens"].as_u64().unwrap() <= 100);
    let next = run(&[
        "outline",
        "sample.ts",
        "--budget",
        "100",
        "--limit",
        "1",
        "--offset",
        "1",
        "--text",
    ]);
    assert!(next.status.success());
    let next = String::from_utf8(next.stdout).unwrap();
    assert!(next.contains("beta"));
    assert!(next.contains("next_offset=2"));
    assert!(cl100k_base().unwrap().encode_ordinary(&next).len() <= 100);
    assert!(
        !run(&["outline", "sample.ts", "--limit", "0"])
            .status
            .success()
    );
}
