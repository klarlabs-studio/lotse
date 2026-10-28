use graf::units::{Unit, pack_units, pack_units_focused, unit_text_for_path};
use tiktoken_rs::cl100k_base;

fn large_unit() -> Unit {
    let mut lines = vec!["    unrelated setup initialization value"; 120];
    lines[100] = "    reject_foreign_formdata_before_connect();";
    Unit {
        path: "src/request.rs".into(),
        kind: "function".into(),
        name: Some("construct".into()),
        start: 10,
        end: 129,
        text: lines.join("\n"),
        calls: vec![],
    }
}

#[test]
fn oversized_function_delivers_deep_match_with_exact_range_and_budget() {
    let bpe = cl100k_base().unwrap();
    let unit = large_unit();
    let query = "reject foreign formdata before connect";
    let (_, prefix) = pack_units(&bpe, std::slice::from_ref(&unit), &[1.0], 100, 0.5);
    assert!(!prefix.contains("reject_foreign"));
    for budget in [0, 1, 20, 50, 100, 200, 2000] {
        let (picked, context) = pack_units_focused(
            &bpe,
            std::slice::from_ref(&unit),
            &[1.0],
            query,
            budget,
            0.5,
        );
        assert!(bpe.encode_ordinary(&context).len() <= budget);
        if let Some(snippet) = picked.first() {
            assert!(snippet.text.contains("reject_foreign"));
            assert_eq!(
                snippet.text,
                unit_text_for_path(
                    &unit.path,
                    &unit.text,
                    snippet.start - unit.start + 1,
                    snippet.end - unit.start + 1,
                )
            );
            assert!(context.starts_with(&format!(
                "\"src/request.rs\":{}-{}\n",
                snippet.start, snippet.end
            )));
        } else {
            assert!(context.is_empty());
        }
    }
}

#[test]
fn full_units_keep_order_and_empty_or_unmatchable_text_is_safe() {
    let bpe = cl100k_base().unwrap();
    let mut unit = large_unit();
    unit.text = "short\ntext".into();
    unit.end = 11;
    let (picked, context) = pack_units_focused(
        &bpe,
        std::slice::from_ref(&unit),
        &[1.0],
        "absent",
        100,
        0.5,
    );
    assert_eq!(picked, vec![unit.clone()]);
    assert!(context.contains("short\ntext"));
    unit.text.clear();
    assert!(
        pack_units_focused(&bpe, &[unit], &[1.0], "absent", 0, 0.5)
            .0
            .is_empty()
    );
}
