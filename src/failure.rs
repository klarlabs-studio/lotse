//! Experimental: from a failed test run's output to the functions it exercises.
//!
//! Most failures in the t14/t15 transcripts are assertion failures that name only the failing
//! test, often one the agent cannot see. `failure` reads three kinds of evidence from the
//! output: source lines it names (`path:line` or a Python frame), functions the printed test
//! code calls or the error names, and failing test names that exist in the index. It answers
//! with those functions first, then the functions they call, within one budget. Not an
//! accepted improvement.
use crate::Index;
use crate::source::kind;
use crate::triage::{CitedUnit, IndexedUnits, indexed_units};
use crate::units::{Unit, pack_units, unit_scores, unit_text_for_path};
use anyhow::Result;
use regex::Regex;
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;
use tiktoken_rs::CoreBPE;

/// Definitions an identifier may resolve to before it is too ambiguous to follow (as call edges).
const MAX_DEFINITIONS: usize = 3;
/// Distinct identifiers taken from the output, in order of first appearance.
const MAX_IDENTIFIERS: usize = 60;

static MENTION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"([\w./@-]+\.(?:py|go|js|jsx|mjs|cjs|ts|tsx|rs))(?:", line |:)(\d+)"#).unwrap()
});
static FAILED_TEST: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^(?:FAILED|ERROR) \S+?::(?:\w+::)*(\w+)|--- FAIL: (\w+)").unwrap()
});
static IDENTIFIER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(|\.([A-Za-z_][A-Za-z0-9_]*)").unwrap()
});

#[derive(Debug, Serialize)]
pub struct Failure {
    /// Failing test functions found in the index; their callees are candidates.
    pub tests: Vec<CitedUnit>,
    pub units: Vec<CitedUnit>,
    pub budget: usize,
    pub context_tokens: usize,
    pub context: String,
    pub experimental: &'static str,
}

/// [`failure_with`] over the current function index.
pub fn failure(bpe: &CoreBPE, index: &Index, output: &str, budget: usize) -> Result<Failure> {
    failure_with(bpe, index, &indexed_units(index)?, output, budget)
}

/// Functions a failed test run's output points to, in tiers: source lines it names, then
/// functions it names or its failing tests call, then their callees; by BM25 against the output
/// within a tier.
pub fn failure_with(
    bpe: &CoreBPE,
    index: &Index,
    indexed: &IndexedUnits,
    output: &str,
    budget: usize,
) -> Result<Failure> {
    let units = &indexed.units;
    let is_code = |i: usize| units[i].kind == "function" && kind(&units[i].path) == "code";
    let mut tier: BTreeMap<usize, u8> = BTreeMap::new();
    let mut tests = BTreeSet::new();
    for (path, line) in mentions(output) {
        if let Some(i) = enclosing(index, units, &path, line) {
            if is_code(i) {
                tier.entry(i).or_insert(0);
            } else if kind(&units[i].path) == "test" {
                tests.insert(i);
            }
        }
    }
    for name in failed_tests(output) {
        tests.extend(defined(units, &name, |i| kind(&units[i].path) == "test"));
    }
    for name in identifiers(output) {
        for i in defined(units, &name, is_code) {
            tier.entry(i).or_insert(1);
        }
    }
    for &(caller, callee) in &indexed.edges {
        if tests.contains(&caller) && is_code(callee) {
            tier.entry(callee).or_insert(1);
        }
    }
    let direct: Vec<usize> = tier.keys().copied().collect();
    for &(caller, callee) in &indexed.edges {
        if direct.binary_search(&caller).is_ok() && is_code(callee) {
            tier.entry(callee).or_insert(2);
        }
    }
    let scores = unit_scores(&indexed.bm25, &indexed.edges, output);
    let mut order: Vec<usize> = tier.keys().copied().collect();
    order.sort_by(|&a, &b| {
        tier[&a]
            .cmp(&tier[&b])
            .then(scores[b].total_cmp(&scores[a]))
            .then(a.cmp(&b))
    });
    let mut texts: BTreeMap<&str, String> = BTreeMap::new();
    let mut candidates = Vec::new();
    for &i in &order {
        let unit = &units[i];
        if !texts.contains_key(unit.path.as_str()) {
            texts.insert(&unit.path, index.current_text(&unit.path)?);
        }
        candidates.push(Unit {
            text: unit_text_for_path(&unit.path, &texts[unit.path.as_str()], unit.start, unit.end),
            ..unit.clone()
        });
    }
    // Descending stand-in scores keep this order through the shared packer; no floor applies.
    let ranks: Vec<f64> = (0..candidates.len())
        .map(|i| (candidates.len() - i) as f64)
        .collect();
    let (picked, context) = pack_units(bpe, &candidates, &ranks, budget, 0.0);
    Ok(Failure {
        tests: tests.iter().map(|&i| CitedUnit::from(&units[i])).collect(),
        units: picked.iter().map(CitedUnit::from).collect(),
        budget,
        context_tokens: bpe.encode_ordinary(&context).len(),
        context,
        experimental: "failure: functions a failed test run's output names or calls, then their callees",
    })
}

/// (path, line) for every source line the output names, in order.
fn mentions(output: &str) -> Vec<(String, usize)> {
    MENTION
        .captures_iter(output)
        .filter_map(|c| Some((c[1].to_string(), c[2].parse().ok()?)))
        .collect()
}

/// Names of failing tests: pytest's `FAILED path::[Class::]name[params]`, Go's `--- FAIL: Name`.
fn failed_tests(output: &str) -> BTreeSet<String> {
    FAILED_TEST
        .captures_iter(output)
        .filter_map(|c| {
            c.get(1)
                .or_else(|| c.get(2))
                .map(|m| m.as_str().to_string())
        })
        .collect()
}

/// Called or accessed identifiers, distinct, in order of first appearance.
fn identifiers(output: &str) -> Vec<String> {
    let mut seen = BTreeSet::new();
    IDENTIFIER
        .captures_iter(output)
        .filter_map(|c| {
            c.get(1)
                .or_else(|| c.get(2))
                .map(|m| m.as_str().to_string())
        })
        .filter(|name| seen.insert(name.clone()))
        .take(MAX_IDENTIFIERS)
        .collect()
}

/// Function units named `name` that pass `keep`, unless more than [`MAX_DEFINITIONS`] do.
fn defined(units: &[Unit], name: &str, keep: impl Fn(usize) -> bool) -> Vec<usize> {
    let found: Vec<usize> = (0..units.len())
        .filter(|&i| {
            units[i].kind == "function" && units[i].name.as_deref() == Some(name) && keep(i)
        })
        .collect();
    if found.len() <= MAX_DEFINITIONS {
        found
    } else {
        Vec::new()
    }
}

/// The innermost function unit of an indexed file that holds `line`; an absolute or
/// differently rooted path matches the indexed path it ends with.
fn enclosing(index: &Index, units: &[Unit], path: &str, line: usize) -> Option<usize> {
    let path = path.trim_start_matches("./");
    let file = index
        .files
        .keys()
        .filter(|f| path == f.as_str() || path.ends_with(&format!("/{f}")))
        .max_by_key(|f| f.len())?;
    (0..units.len())
        .filter(|&i| {
            units[i].kind == "function"
                && &units[i].path == file
                && units[i].start <= line
                && line <= units[i].end
        })
        .min_by_key(|&i| units[i].end - units[i].start)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_failing_test_names_from_pytest_and_go() {
        let output = "FAILED test/plugins/test_deezer.py::TestVA::test_compilation[1] - x\n--- FAIL: TestQueryNodes (0.00s)\n";
        assert_eq!(
            failed_tests(output),
            BTreeSet::from(["test_compilation".to_string(), "TestQueryNodes".to_string()])
        );
    }

    #[test]
    fn reads_source_lines_from_frames_and_path_line_mentions() {
        let output = "  File \"/w/src/app.py\", line 12, in run\nsqlgraph/graph_test.go:2501: \n";
        assert_eq!(
            mentions(output),
            vec![
                ("/w/src/app.py".to_string(), 12),
                ("sqlgraph/graph_test.go".to_string(), 2501)
            ]
        );
    }

    #[test]
    fn reads_called_and_accessed_identifiers_once_in_order() {
        let output = "album_info = plugin.album_for_id(\"1\")\n> assert album_info.va is True\nplugin.album_for_id";
        assert_eq!(identifiers(output), vec!["album_for_id", "va"]);
    }
}
