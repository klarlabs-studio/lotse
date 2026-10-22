//! Experimental `units` retrieval: tree-sitter function units, BM25, and one-hop call propagation.
//!
//! A port of the `ast_graph` arm in `benchmark/localization.py` (preregistration
//! v15-v20). It must return the same payload as that prototype, including its
//! Python-specific line splitting and term rules, so evidence about the prototype
//! carries over; `benchmark/check_units_equivalence.py` verifies this. Not an
//! accepted improvement.
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use tiktoken_rs::CoreBPE;
use tree_sitter::{Language, Node, Parser};

/// Lines per module or text window.
pub const WINDOW: usize = 24;
/// Stop below this share of the best score.
pub const FLOOR: f64 = 0.5;
/// Share of the best neighbour's score added through call edges.
pub const WEIGHT: f64 = 0.5;
/// A called name defined by more units than this is too ambiguous to link.
const MAX_DEFINITIONS: usize = 3;

const FUNCTIONS: &[&str] = &[
    "function_declaration",
    "method_declaration",
    "function_definition",
    "decorated_definition",
    "generator_function_declaration",
    "method_definition",
    "function_expression",
    "arrow_function",
    "function",
];
const BINDINGS: &[&str] = &[
    "variable_declarator",
    "lexical_declaration",
    "variable_declaration",
    "assignment_expression",
    "expression_statement",
    "pair",
    "export_statement",
];
/// The prototype's stopwords (`benchmark/retrieval.py`), equal to graf's request list.
const STOP: &[&str] = &[
    "a",
    "an",
    "the",
    "is",
    "are",
    "be",
    "to",
    "of",
    "for",
    "and",
    "or",
    "in",
    "on",
    "with",
    "its",
    "it",
    "this",
    "that",
    "which",
    "what",
    "where",
    "how",
    "why",
    "can",
    "as",
    "by",
    "from",
    "if",
    "should",
    "does",
    "do",
    "need",
    "find",
    "locate",
    "explain",
    "check",
    "claim",
    "against",
    "changes",
    "change",
    "implementation",
    "test",
    "tests",
];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Unit {
    pub path: String,
    /// `function`, `module`, or `text`.
    pub kind: String,
    pub name: Option<String>,
    pub start: usize,
    pub end: usize,
    pub text: String,
    pub calls: Vec<String>,
}

fn is_python_line_break(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r'
            | '\x0b'
            | '\x0c'
            | '\x1c'
            | '\x1d'
            | '\x1e'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

/// Python's `str.splitlines(keepends)`: every Unicode line boundary, `\r\n` as one.
pub fn split_lines(text: &str, keep_ends: bool) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if !is_python_line_break(c) {
            continue;
        }
        let mut end = i + c.len_utf8();
        if c == '\r' && chars.peek().is_some_and(|&(_, n)| n == '\n') {
            chars.next();
            end += 1;
        }
        lines.push(&text[start..if keep_ends { end } else { i }]);
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// The prototype's terms: split lowerUpper and digitUpper pairs, keep ASCII
/// alphanumeric runs of two or more characters, drop stopwords.
pub fn terms(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut split = String::with_capacity(text.len());
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        split.push(c);
        if (c.is_ascii_lowercase() || c.is_ascii_digit())
            && chars.get(i + 1).is_some_and(char::is_ascii_uppercase)
        {
            split.push(' ');
            split.push(chars[i + 1]);
            i += 2;
        } else {
            i += 1;
        }
    }
    split
        .to_lowercase()
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|w| w.len() > 1 && !STOP.contains(w))
        .map(str::to_string)
        .collect()
}

fn language(path: &str) -> Option<Language> {
    let extension = path.rsplit_once('.').map(|(_, e)| e)?;
    match extension {
        "go" => Some(tree_sitter_go::LANGUAGE.into()),
        "py" => Some(tree_sitter_python::LANGUAGE.into()),
        "js" | "mjs" | "cjs" | "jsx" => Some(tree_sitter_javascript::LANGUAGE.into()),
        "ts" => Some(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()),
        "tsx" => Some(tree_sitter_typescript::LANGUAGE_TSX.into()),
        _ => None,
    }
}

fn node_text<'a>(node: Node, source: &'a str) -> &'a str {
    &source[node.byte_range()]
}

fn name_of(node: Node, source: &str) -> Option<String> {
    let mut field = node.child_by_field_name("name");
    if field.is_none() && node.kind() == "decorated_definition" {
        field = node
            .child_by_field_name("definition")
            .and_then(|d| d.child_by_field_name("name"));
    }
    if let Some(field) = field {
        return Some(node_text(field, source).to_string());
    }
    let mut parent = node.parent();
    while let Some(p) = parent.filter(|p| BINDINGS.contains(&p.kind())) {
        for key in ["name", "left", "key"] {
            if let Some(target) = p.child_by_field_name(key) {
                let named = target.child_by_field_name("property").unwrap_or(target);
                return Some(
                    node_text(named, source)
                        .trim_matches(|c| c == '\'' || c == '"')
                        .to_string(),
                );
            }
        }
        parent = p.parent();
    }
    None
}

/// Rows of the function, extended to the statement that binds it.
fn span(node: Node) -> (usize, usize) {
    let mut top = node;
    while let Some(parent) = top.parent().filter(|p| BINDINGS.contains(&p.kind())) {
        top = parent;
    }
    (top.start_position().row + 1, top.end_position().row + 1)
}

fn function_nodes(root: Node) -> Vec<Node> {
    let mut found = Vec::new();
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        let decorated = node.kind() == "function_definition"
            && node
                .parent()
                .is_some_and(|p| p.kind() == "decorated_definition");
        if FUNCTIONS.contains(&node.kind()) && !decorated {
            found.push(node); // Nested functions belong to the outermost one.
            continue;
        }
        let mut cursor = node.walk();
        let children: Vec<Node> = node.children(&mut cursor).collect();
        stack.extend(children.into_iter().rev());
    }
    found.sort_by_key(Node::start_byte);
    found
}

fn calls(node: Node, source: &str) -> Vec<String> {
    let mut names = Vec::new();
    let mut stack = vec![node];
    while let Some(current) = stack.pop() {
        let field = match current.kind() {
            "call_expression" | "call" => Some("function"),
            "new_expression" => Some("constructor"),
            _ => None,
        };
        if let Some(mut target) = field.and_then(|f| current.child_by_field_name(f)) {
            for key in ["field", "attribute", "property"] {
                if let Some(inner) = target.child_by_field_name(key) {
                    target = inner;
                    break;
                }
            }
            names.push(node_text(target, source).to_string());
        }
        let mut cursor = current.walk();
        stack.extend(current.children(&mut cursor));
    }
    names
}

fn join(lines: &[&str], start: usize, end: usize) -> String {
    let from = start.saturating_sub(1).min(lines.len());
    let to = end.min(lines.len()).max(from);
    lines[from..to].join("\n")
}

fn windows(path: &str, lines: &[&str], start: usize, end: usize, kind: &'static str) -> Vec<Unit> {
    (start..=end)
        .step_by(WINDOW)
        .map(|s| {
            let e = (s + WINDOW - 1).min(end);
            Unit {
                path: path.into(),
                kind: kind.into(),
                name: None,
                start: s,
                end: e,
                text: join(lines, s, e),
                calls: Vec::new(),
            }
        })
        .collect()
}

/// Partition a file into function units plus module (or text) windows covering every other line.
pub fn code_units(path: &str, text: &str) -> Vec<Unit> {
    let lines = split_lines(text, false);
    let Some(language) = language(path) else {
        return windows(path, &lines, 1, lines.len(), "text");
    };
    let mut parser = Parser::new();
    parser
        .set_language(&language)
        .expect("bundled grammar matches the tree-sitter runtime");
    let tree = parser.parse(text, None).expect("parsing without a timeout");
    let mut units = Vec::new();
    let mut covered = 0;
    for node in function_nodes(tree.root_node()) {
        let (start, end) = span(node);
        if start <= covered {
            continue; // Two definitions on one line: the first owns it.
        }
        if start > covered + 1 {
            units.extend(windows(path, &lines, covered + 1, start - 1, "module"));
        }
        units.push(Unit {
            path: path.into(),
            kind: "function".into(),
            name: name_of(node, text),
            start,
            end,
            text: join(&lines, start, end),
            calls: calls(node, text),
        });
        covered = end;
    }
    if covered < lines.len() {
        units.extend(windows(path, &lines, covered + 1, lines.len(), "module"));
    }
    units
}

/// Directed caller -> callee edges between function units, linked by defined name.
pub fn call_edges(units: &[Unit]) -> Vec<(usize, usize)> {
    let mut defined: HashMap<&str, Vec<usize>> = HashMap::new();
    for (i, unit) in units.iter().enumerate() {
        if let Some(name) = unit
            .name
            .as_deref()
            .filter(|n| unit.kind == "function" && !n.is_empty())
        {
            defined.entry(name).or_default().push(i);
        }
    }
    let mut edges = BTreeSet::new();
    for (i, unit) in units.iter().enumerate() {
        let called: BTreeSet<&str> = unit.calls.iter().map(String::as_str).collect();
        for name in called {
            let targets = defined.get(name).map_or(&[][..], Vec::as_slice);
            if targets.len() <= MAX_DEFINITIONS {
                edges.extend(targets.iter().filter(|&&j| j != i).map(|&j| (i, j)));
            }
        }
    }
    edges.into_iter().collect()
}

/// Add a share of the best neighbour's own score, treating calls as undirected.
pub fn propagate(scores: &[f64], edges: &[(usize, usize)], weight: f64) -> Vec<f64> {
    let mut best = vec![0.0f64; scores.len()];
    for &(a, b) in edges {
        best[a] = best[a].max(scores[b]);
        best[b] = best[b].max(scores[a]);
    }
    scores
        .iter()
        .zip(best)
        .map(|(s, n)| s + weight * n)
        .collect()
}

/// Files ranked by their score plus `weight` times the best score of a file linked to
/// them by a cross-file call (the screened `file_graph_ids` arm); unscored files are dropped.
pub fn file_propagate(
    file_scores: &BTreeMap<String, f64>,
    units: &[Unit],
    edges: &[(usize, usize)],
    weight: f64,
) -> Vec<String> {
    let mut neighbours: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    for &(a, b) in edges {
        let (fa, fb) = (units[a].path.as_str(), units[b].path.as_str());
        if fa != fb {
            neighbours.entry(fa).or_default().insert(fb);
            neighbours.entry(fb).or_default().insert(fa);
        }
    }
    let mut candidates: BTreeSet<&str> = file_scores.keys().map(String::as_str).collect();
    for file in file_scores.keys() {
        if let Some(linked) = neighbours.get(file.as_str()) {
            candidates.extend(linked);
        }
    }
    let score = |f: &str| file_scores.get(f).copied().unwrap_or(0.0);
    let mut raised: Vec<(&str, f64)> = candidates
        .into_iter()
        .map(|f| {
            let best = neighbours
                .get(f)
                .map_or(0.0, |n| n.iter().map(|&x| score(x)).fold(0.0, f64::max));
            (f, score(f) + weight * best)
        })
        .filter(|&(_, s)| s > 0.0)
        .collect();
    raised.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(b.0)));
    raised.into_iter().map(|(f, _)| f.to_string()).collect()
}

/// A unit's term counts, sorted by term: computed once per unit and cached.
pub fn term_counts(text: &str) -> Vec<(String, u32)> {
    let mut counts: BTreeMap<String, u32> = BTreeMap::new();
    for term in terms(text) {
        *counts.entry(term).or_default() += 1;
    }
    counts.into_iter().collect()
}

/// Final unit scores: BM25, then one-hop call propagation over precomputed edges.
pub fn unit_scores(bm25: &Bm25, edges: &[(usize, usize)], query: &str) -> Vec<f64> {
    propagate(&bm25.score(query), edges, WEIGHT)
}

/// Indexes of the units packing can consider: positive scores at or above the floor.
pub fn packing_candidates(scores: &[f64]) -> Vec<usize> {
    let best = scores.iter().copied().fold(0.0f64, f64::max);
    (0..scores.len())
        .filter(|&i| scores[i] > 0.0 && scores[i] >= FLOOR * best)
        .collect()
}

/// A unit's text from its file's text, exactly as [`code_units`] extracts it.
pub fn unit_text(file_text: &str, start: usize, end: usize) -> String {
    join(&split_lines(file_text, false), start, end)
}

/// The prototype's BM25 (k1 1.2, b 0.75) over unit texts.
#[derive(Debug, PartialEq, Serialize, Deserialize)]
pub struct Bm25 {
    postings: BTreeMap<String, Vec<(usize, usize)>>,
    lengths: Vec<usize>,
    average: f64,
}

impl Bm25 {
    /// The same index from each unit's cached, term-sorted counts.
    pub fn from_counts<'a>(counts: impl IntoIterator<Item = &'a [(String, u32)]>) -> Self {
        let mut postings: BTreeMap<String, Vec<(usize, usize)>> = BTreeMap::new();
        let mut lengths = Vec::new();
        for (i, unit) in counts.into_iter().enumerate() {
            lengths.push(unit.iter().map(|&(_, k)| k as usize).sum());
            for (term, count) in unit {
                postings
                    .entry(term.clone())
                    .or_default()
                    .push((i, *count as usize));
            }
        }
        let average = lengths.iter().sum::<usize>() as f64 / lengths.len().max(1) as f64;
        Self {
            postings,
            lengths,
            average,
        }
    }

    pub fn new<'a>(texts: impl IntoIterator<Item = &'a str>) -> Self {
        let mut postings: BTreeMap<String, Vec<(usize, usize)>> = BTreeMap::new();
        let mut lengths = Vec::new();
        for (i, text) in texts.into_iter().enumerate() {
            let mut counts: BTreeMap<String, usize> = BTreeMap::new();
            for term in terms(text) {
                *counts.entry(term).or_default() += 1;
            }
            lengths.push(counts.values().sum());
            for (term, count) in counts {
                postings.entry(term).or_default().push((i, count));
            }
        }
        let average = lengths.iter().sum::<usize>() as f64 / lengths.len().max(1) as f64;
        Self {
            postings,
            lengths,
            average,
        }
    }

    pub fn score(&self, query: &str) -> Vec<f64> {
        let n = self.lengths.len() as f64;
        let mut scores = vec![0.0f64; self.lengths.len()];
        let unique: BTreeSet<String> = terms(query).into_iter().collect();
        for term in unique {
            let Some(hits) = self.postings.get(&term) else {
                continue;
            };
            let df = hits.len() as f64;
            let idf = (1.0 + (n - df + 0.5) / (df + 0.5)).ln();
            for &(i, count) in hits {
                let norm = 1.2 * (0.25 + 0.75 * self.lengths[i] as f64 / self.average.max(1.0));
                scores[i] += idf * count as f64 * 2.2 / (count as f64 + norm);
            }
        }
        scores
    }
}

fn render(unit: &Unit) -> String {
    format!(
        "{}:{}-{}\n{}\n\n",
        unit.path, unit.start, unit.end, unit.text
    )
}

fn bounded(bpe: &CoreBPE, text: &str, budget: usize) -> String {
    let mut result = String::new();
    for line in split_lines(text, true) {
        let candidate = format!("{result}{line}");
        if bpe.encode_ordinary(&candidate).len() > budget {
            break;
        }
        result = candidate;
    }
    result
}

/// Whole units in score order, stopping below `floor` times the best score or
/// when the next unit does not fit; an over-long best unit keeps its first lines.
pub fn pack_units(
    bpe: &CoreBPE,
    units: &[Unit],
    scores: &[f64],
    budget: usize,
    floor: f64,
) -> (Vec<Unit>, String) {
    let mut order: Vec<usize> = (0..scores.len()).filter(|&i| scores[i] > 0.0).collect();
    order.sort_by(|&a, &b| scores[b].total_cmp(&scores[a]).then(a.cmp(&b)));
    let mut picked = Vec::new();
    let mut text = String::new();
    let Some(&first) = order.first() else {
        return (picked, text);
    };
    for i in order {
        if scores[i] < floor * scores[first] {
            break;
        }
        let candidate = format!("{text}{}", render(&units[i]));
        if bpe.encode_ordinary(&candidate).len() > budget {
            if picked.is_empty() {
                text = bounded(bpe, &candidate, budget);
                let shown = units[i].start + text.matches('\n').count().saturating_sub(2);
                picked.push(Unit {
                    end: shown.min(units[i].end),
                    ..units[i].clone()
                });
            }
            break;
        }
        picked.push(units[i].clone());
        text = candidate;
    }
    (picked, text)
}

/// Units of every file (in path order), their call edges, and a payload for `query`.
pub fn retrieve(
    bpe: &CoreBPE,
    files: &BTreeMap<String, String>,
    query: &str,
    budget: usize,
) -> (Vec<Unit>, String) {
    let units: Vec<Unit> = files
        .iter()
        .flat_map(|(path, text)| code_units(path, text))
        .collect();
    rank(bpe, &units, query, budget)
}

/// Score `units` (every file's units, in path order) for `query` and pack the payload.
pub fn rank(bpe: &CoreBPE, units: &[Unit], query: &str, budget: usize) -> (Vec<Unit>, String) {
    let edges = call_edges(units);
    let lexical = Bm25::new(units.iter().map(|u| u.text.as_str())).score(query);
    let scores = propagate(&lexical, &edges, WEIGHT);
    pack_units(bpe, units, &scores, budget, FLOOR)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tiktoken_rs::cl100k_base;

    const GO: &str = "package x\n\nfunc helper(a int) int {\n\treturn a + 1\n}\n\nfunc Run() int {\n\treturn helper(2)\n}\n";
    const PY: &str = "import os\n\n\n@decorator\ndef first():\n    return second()\n\n\nclass Box:\n    size = 1\n\n    def second(self):\n        return 2\n";
    const JS: &str = "'use strict'\nfunction onSend (reply) {\n  return wrap(reply)\n}\nconst wrap = (r) => r\nmodule.exports = { onSend }\n";

    fn functions(units: &[Unit]) -> Vec<(String, usize, usize)> {
        units
            .iter()
            .filter(|u| u.kind == "function")
            .map(|u| (u.name.clone().unwrap_or_default(), u.start, u.end))
            .collect()
    }

    #[test]
    fn splits_lines_like_python() {
        assert_eq!(split_lines("a\r\nb\x0cc\n", false), ["a", "b", "c"]);
        assert_eq!(split_lines("a\nb", true), ["a\n", "b"]);
        assert!(split_lines("", false).is_empty());
    }

    #[test]
    fn terms_follow_the_prototype() {
        assert_eq!(
            terms("onSend HTTPServer v2Api x_y"),
            ["send", "httpserver", "v2", "api"]
        );
        assert_eq!(terms("Find the tests for aBcD"), ["bc"]);
    }

    #[test]
    fn go_functions_and_module_remainder() {
        let units = code_units("x.go", GO);
        assert_eq!(
            functions(&units),
            [("helper".into(), 3, 5), ("Run".into(), 7, 9)]
        );
        assert!(units.iter().any(|u| u.kind == "module" && u.start == 1));
    }

    #[test]
    fn python_decorators_and_methods() {
        let units = code_units("x.py", PY);
        assert_eq!(
            functions(&units),
            [("first".into(), 4, 6), ("second".into(), 12, 13)]
        );
    }

    #[test]
    fn javascript_declarations_and_arrow_bindings() {
        let units = code_units("x.js", JS);
        assert_eq!(
            functions(&units),
            [("onSend".into(), 2, 4), ("wrap".into(), 5, 5)]
        );
    }

    const TS: &str = "export function a(x: number): number {\n  return b(x)\n}\nexport const b = (y: number): number => y\ninterface I {\n  f(): void\n}\nclass C {\n  m(z: string): void {\n    a(1)\n  }\n}\n";
    const TSX: &str = "const App = () => <div onClick={() => go()} />\nfunction go(): void {}\n";

    #[test]
    fn typescript_tsx_and_jsx_functions() {
        let units = code_units("x.ts", TS);
        assert_eq!(
            functions(&units),
            [("a".into(), 1, 3), ("b".into(), 4, 4), ("m".into(), 9, 11)]
        );
        assert!(units.iter().any(|u| u.kind == "module" && u.start == 5));
        let named: Vec<_> = call_edges(&units)
            .into_iter()
            .map(|(x, y)| {
                (
                    units[x].name.clone().unwrap(),
                    units[y].name.clone().unwrap(),
                )
            })
            .collect();
        assert!(
            named.contains(&("a".into(), "b".into())) && named.contains(&("m".into(), "a".into()))
        );
        assert_eq!(
            functions(&code_units("x.tsx", TSX)),
            [("App".into(), 1, 1), ("go".into(), 2, 2)]
        );
        assert_eq!(
            functions(&code_units("x.jsx", TSX.replace(": void", "").as_str())),
            [("App".into(), 1, 1), ("go".into(), 2, 2)]
        );
    }

    #[test]
    fn every_line_belongs_to_exactly_one_unit() {
        let text = "a\n".repeat(50);
        for (path, text) in [
            ("x.go", GO),
            ("x.py", PY),
            ("x.js", JS),
            ("README.md", text.as_str()),
        ] {
            let mut covered: Vec<usize> = code_units(path, text)
                .iter()
                .flat_map(|u| u.start..=u.end)
                .collect();
            covered.sort_unstable();
            assert_eq!(
                covered,
                (1..=split_lines(text, false).len()).collect::<Vec<_>>(),
                "{path}"
            );
        }
    }

    #[test]
    fn calls_link_units_by_defined_name() {
        let mut units = code_units("x.go", GO);
        units.extend(code_units("x.js", JS));
        let named: Vec<(String, String)> = call_edges(&units)
            .into_iter()
            .map(|(a, b)| {
                (
                    units[a].name.clone().unwrap_or_default(),
                    units[b].name.clone().unwrap_or_default(),
                )
            })
            .collect();
        assert!(named.contains(&("Run".into(), "helper".into())));
        assert!(named.contains(&("onSend".into(), "wrap".into())));
    }

    #[test]
    fn file_scores_rise_by_half_the_best_linked_file() {
        let mut units = code_units("a.go", GO);
        units.extend(code_units("b.js", JS));
        units[1].calls = vec!["onSend".into()];
        let edges = call_edges(&units);
        let scores = BTreeMap::from([("a.go".to_string(), 2.0)]);
        assert_eq!(
            file_propagate(&scores, &units, &edges, 0.5),
            ["a.go", "b.js"]
        );
        let scores = BTreeMap::from([("a.go".to_string(), 2.0), ("c.py".to_string(), 3.0)]);
        assert_eq!(file_propagate(&scores, &units, &[], 0.5), ["c.py", "a.go"]);
    }

    #[test]
    fn bm25_from_cached_counts_equals_bm25_over_texts() {
        let mut units = code_units("x.go", GO);
        units.extend(code_units("x.py", PY));
        units.extend(code_units("x.ts", TS));
        let counts: Vec<_> = units.iter().map(|u| term_counts(&u.text)).collect();
        assert_eq!(
            Bm25::new(units.iter().map(|u| u.text.as_str())),
            Bm25::from_counts(counts.iter().map(Vec::as_slice))
        );
        for query in [
            "helper Run",
            "second decorator box",
            "number a b",
            "missing",
            "Box Box size",
        ] {
            let direct = Bm25::new(units.iter().map(|u| u.text.as_str())).score(query);
            let cached = Bm25::from_counts(counts.iter().map(Vec::as_slice)).score(query);
            assert_eq!(
                direct.iter().map(|s| s.to_bits()).collect::<Vec<_>>(),
                cached.iter().map(|s| s.to_bits()).collect::<Vec<_>>(),
                "{query}"
            );
        }
    }

    #[test]
    fn propagation_adds_half_the_best_neighbour() {
        assert_eq!(
            propagate(&[1.0, 4.0, 0.0], &[(0, 1), (1, 2)], 0.5),
            [3.0, 4.5, 2.0]
        );
    }

    #[test]
    fn packing_stops_at_the_floor_and_the_budget() {
        let bpe = cl100k_base().unwrap();
        let unit = |path: &str, text: &str| Unit {
            path: path.into(),
            kind: "function".into(),
            name: None,
            start: 1,
            end: 1,
            text: text.into(),
            calls: Vec::new(),
        };
        let units = [unit("a.go", "x"), unit("b.go", "y"), unit("c.go", "z")];
        let (picked, _) = pack_units(&bpe, &units, &[3.0, 2.0, 1.0], 2000, 0.5);
        assert_eq!(
            picked.iter().map(|u| u.path.as_str()).collect::<Vec<_>>(),
            ["a.go", "b.go"]
        );
        let (picked, _) = pack_units(&bpe, &units, &[3.0, 2.9, 2.8], 8, 0.5);
        assert_eq!(picked.len(), 1);
    }
}
