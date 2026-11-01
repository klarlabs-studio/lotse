//! Experimental: files ranked by a native port of Graphify's `query` traversal over graf's function graph, and
//! that ranking fused into triage's likely files.
//!
//! The graph has a node per indexed file (labelled by its file name) and per named function unit (labelled
//! `name()`), with undirected `contains` (file to function) and cross-unit call edges. Seeds, the breadth-first
//! traversal and the output order follow Graphify 0.9.100's `serve.py`: label-tier scoring with IDF weights and
//! squared term coverage, at most three seeds within a 20% score gap plus one per matched term, a depth-2 walk
//! that does not pass through hubs, then nodes by hop distance and degree. Graphify's class, import and
//! reference nodes, context filters and budget cut are not ported. Not an accepted improvement.
use crate::triage::IndexedUnits;
use std::collections::{BTreeMap, BTreeSet, HashMap};

const EXACT: f64 = 1000.0;
const PREFIX: f64 = 100.0;
const SUBSTRING: f64 = 1.0;
const SOURCE: f64 = 0.5;
const MAX_SEEDS: usize = 3;
const GAP_RATIO: f64 = 0.2;
const DEPTH: usize = 2;
const HUB_FLOOR: usize = 50;
/// Reciprocal rank fusion's customary constant; fixed, not tuned.
pub const FUSION_K: f64 = 60.0;

const STOPWORDS: &[&str] = &[
    "how", "what", "why", "when", "where", "which", "who", "whom", "whose", "does", "did", "is",
    "are", "was", "were", "be", "been", "being", "can", "could", "should", "would", "will",
    "shall", "may", "might", "must", "has", "have", "had", "the", "and", "but", "not", "for",
    "from", "with", "without", "into", "onto", "off", "that", "this", "these", "those", "there",
    "here", "its", "their", "them", "they", "about", "any", "all", "some", "work", "works",
    "working", "get", "gets", "got", "getting",
];
const INTENT: &[&str] = &[
    "call",
    "calls",
    "called",
    "caller",
    "callers",
    "invoke",
    "invokes",
    "invoked",
    "use",
    "uses",
    "used",
    "using",
    "import",
    "imports",
    "imported",
    "export",
    "exports",
    "exported",
    "extend",
    "extends",
    "extended",
    "implement",
    "implements",
    "implemented",
    "depend",
    "depends",
    "reference",
    "references",
    "referenced",
];

struct Node {
    label: String,
    norm: String,
    tokens: String,
    path: String,
}

/// Lowercased runs of letters and digits; `_` separates, as in Graphify's `_search_tokens`.
fn search_tokens(text: &str) -> Vec<String> {
    text.to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|t| !t.is_empty())
        .map(str::to_string)
        .collect()
}

fn searchable(term: &str) -> bool {
    !term.chars().all(|c| c.is_ascii_lowercase()) || term.len() > 2
}

/// Graphify's `_query_terms`, then `_search_tokens` over each, deduplicated in order.
fn query_terms(question: &str) -> Vec<String> {
    let words: Vec<String> = question
        .split_whitespace()
        .flat_map(|raw| {
            raw.to_lowercase()
                .split(|c: char| !(c.is_alphanumeric() || c == '_'))
                .filter(|t| !t.is_empty() && searchable(t))
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .collect();
    let content: Vec<String> = words
        .iter()
        .filter(|w| !STOPWORDS.contains(&w.as_str()))
        .cloned()
        .collect();
    let chosen = if content.is_empty() { words } else { content };
    let mut seen = BTreeSet::new();
    chosen
        .iter()
        .flat_map(|t| search_tokens(t))
        .filter(|t| seen.insert(t.clone()))
        .collect()
}

struct Graph {
    nodes: Vec<Node>,
    adjacent: Vec<BTreeSet<usize>>,
}

fn graph(indexed: &IndexedUnits) -> Graph {
    let mut nodes = Vec::new();
    let mut file_node: BTreeMap<&str, usize> = BTreeMap::new();
    let mut unit_node: HashMap<usize, usize> = HashMap::new();
    let mut edges = Vec::new();
    for (i, unit) in indexed.units.iter().enumerate() {
        let file = *file_node.entry(unit.path.as_str()).or_insert_with(|| {
            let name = unit
                .path
                .rsplit('/')
                .next()
                .unwrap_or(&unit.path)
                .to_string();
            nodes.push(Node {
                norm: name.to_lowercase(),
                tokens: search_tokens(&name).join(" "),
                label: name,
                path: unit.path.clone(),
            });
            nodes.len() - 1
        });
        if let Some(name) = unit
            .name
            .as_deref()
            .filter(|n| unit.kind == "function" && !n.is_empty())
        {
            let label = format!("{name}()");
            nodes.push(Node {
                norm: label.to_lowercase(),
                tokens: search_tokens(&label).join(" "),
                label,
                path: unit.path.clone(),
            });
            unit_node.insert(i, nodes.len() - 1);
            edges.push((file, nodes.len() - 1));
        }
    }
    for &(a, b) in &indexed.edges {
        if let (Some(&x), Some(&y)) = (unit_node.get(&a), unit_node.get(&b)) {
            edges.push((x, y));
        }
    }
    let mut adjacent = vec![BTreeSet::new(); nodes.len()];
    for (a, b) in edges {
        if a != b {
            adjacent[a].insert(b);
            adjacent[b].insert(a);
        }
    }
    Graph { nodes, adjacent }
}

/// A singleton seed's tie-break key, smallest best: (-score, -degree, label length, label).
type SeedKey<'a> = (f64, isize, usize, &'a str);

struct Scores {
    ranked: Vec<(f64, usize)>,
    /// Each term's best singleton node, strongest first.
    per_term: Vec<(String, usize)>,
}

fn score(g: &Graph, terms: &[String]) -> Scores {
    let n = g.nodes.len().max(1) as f64;
    let idf: Vec<f64> = terms
        .iter()
        .map(|t| {
            (1.0 + n
                / (1.0
                    + g.nodes
                        .iter()
                        .filter(|x| x.norm.contains(t.as_str()))
                        .count() as f64))
                .ln()
        })
        .collect();
    let joined = terms.join(" ");
    let joined_w = idf.iter().copied().fold(1.0f64, f64::max);
    let mut ranked = Vec::new();
    let mut best: BTreeMap<&str, (SeedKey, usize)> = BTreeMap::new();
    for (id, node) in g.nodes.iter().enumerate() {
        let bare = node.norm.trim_end_matches("()");
        let source = node.path.to_lowercase();
        let mut total = 0.0;
        if !joined.is_empty() {
            if joined == node.norm || joined == bare || joined == node.tokens {
                total += EXACT * 10.0 * joined_w;
            } else if node.norm.starts_with(&joined)
                || bare.starts_with(&joined)
                || node.tokens.starts_with(&joined)
            {
                total += PREFIX * 10.0 * joined_w;
            }
        }
        let (mut matched, mut tiered) = (0usize, 0.0);
        for (t, &w) in terms.iter().zip(&idf) {
            let (mut tier, mut substring, mut src) = (0.0, 0.0, 0.0);
            if *t == node.norm || t == bare {
                tier = EXACT * w;
                matched += 1;
            } else if node.norm.starts_with(t.as_str()) || bare.starts_with(t.as_str()) {
                tier = PREFIX * w;
                matched += 1;
            } else if node.norm.contains(t.as_str()) {
                substring = SUBSTRING * w;
                total += substring;
                matched += 1;
            }
            if source.contains(t.as_str()) {
                src = SOURCE * w;
                total += src;
            }
            tiered += tier;
            let mut singleton = if *t == node.norm || t == bare || *t == node.tokens {
                EXACT * 10.0 * w
            } else if node.norm.starts_with(t.as_str())
                || bare.starts_with(t.as_str())
                || node.tokens.starts_with(t.as_str())
            {
                PREFIX * 10.0 * w
            } else {
                0.0
            };
            singleton += tier + substring + src;
            if singleton > 0.0 {
                let key = (
                    -singleton,
                    -(g.adjacent[id].len() as isize),
                    node.label.len(),
                    node.label.as_str(),
                );
                let replace = best
                    .get(t.as_str())
                    .is_none_or(|(k, _)| compare(&key, k).is_lt());
                if replace {
                    best.insert(t.as_str(), (key, id));
                }
            }
        }
        if tiered > 0.0 {
            total += tiered * (matched as f64 / terms.len() as f64).powi(2);
        }
        if total > 0.0 {
            ranked.push((total, id));
        }
    }
    ranked.sort_by(|a, b| {
        b.0.total_cmp(&a.0)
            .then(g.nodes[a.1].label.len().cmp(&g.nodes[b.1].label.len()))
            .then(a.1.cmp(&b.1))
    });
    let mut per_term: Vec<(&str, (SeedKey, usize))> = best.into_iter().collect();
    per_term.sort_by(|a, b| compare(&a.1.0, &b.1.0).then(a.0.cmp(b.0)));
    Scores {
        ranked,
        per_term: per_term
            .into_iter()
            .map(|(t, (_, id))| (t.to_string(), id))
            .collect(),
    }
}

fn compare(a: &SeedKey, b: &SeedKey) -> std::cmp::Ordering {
    a.0.total_cmp(&b.0)
        .then(a.1.cmp(&b.1))
        .then(a.2.cmp(&b.2))
        .then(a.3.cmp(b.3))
}

fn seeds(g: &Graph, scores: &Scores, terms: &[String]) -> Vec<usize> {
    let Some(&(top, _)) = scores.ranked.first() else {
        return Vec::new();
    };
    let mut picked = Vec::new();
    let mut labels = BTreeSet::new();
    for &(s, id) in &scores.ranked {
        if picked.len() >= MAX_SEEDS || (!picked.is_empty() && s < top * GAP_RATIO) {
            break;
        }
        if labels.insert(g.nodes[id].norm.clone()) {
            picked.push(id);
        }
    }
    let only_intent = terms.iter().all(|t| INTENT.contains(&t.as_str()));
    for (term, id) in &scores.per_term {
        if !only_intent && INTENT.contains(&term.as_str()) {
            continue;
        }
        if !picked.contains(id) && labels.insert(g.nodes[*id].norm.clone()) {
            picked.push(*id);
        }
    }
    picked
}

/// Files in the order a Graphify-style traversal from the issue's seeds first reaches them.
pub fn graph_files(indexed: &IndexedUnits, issue: &str) -> Vec<String> {
    let g = graph(indexed);
    let terms = query_terms(issue);
    if terms.is_empty() {
        return Vec::new();
    }
    let start = seeds(&g, &score(&g, &terms), &terms);
    let mut degrees: Vec<usize> = g.adjacent.iter().map(BTreeSet::len).collect();
    degrees.sort_unstable();
    let hub = degrees
        .get(degrees.len() * 99 / 100)
        .copied()
        .unwrap_or(0)
        .max(HUB_FLOOR);
    let seed_set: BTreeSet<usize> = start.iter().copied().collect();
    let mut visited = seed_set.clone();
    let mut frontier = seed_set.clone();
    for _ in 0..DEPTH {
        let mut next = BTreeSet::new();
        for &n in &frontier {
            if !seed_set.contains(&n) && g.adjacent[n].len() >= hub {
                continue;
            }
            next.extend(g.adjacent[n].iter().filter(|m| !visited.contains(m)));
        }
        visited.extend(&next);
        frontier = next;
    }
    // Hop distance over the visited subgraph, then degree, as Graphify's `_subgraph_to_text` orders nodes.
    let mut distance: HashMap<usize, usize> = start.iter().map(|&s| (s, 0)).collect();
    let mut layer = start.clone();
    let mut hop = 0;
    while !layer.is_empty() {
        hop += 1;
        let mut next = Vec::new();
        for n in layer {
            for &m in &g.adjacent[n] {
                if visited.contains(&m) && !distance.contains_key(&m) {
                    distance.insert(m, hop);
                    next.push(m);
                }
            }
        }
        layer = next;
    }
    let mut rest: Vec<usize> = visited.difference(&seed_set).copied().collect();
    rest.sort_by_key(|&n| {
        (
            distance.get(&n).copied().unwrap_or(usize::MAX),
            std::cmp::Reverse(g.adjacent[n].len()),
            n,
        )
    });
    let mut files = Vec::new();
    let mut seen = BTreeSet::new();
    for n in start.into_iter().chain(rest) {
        if seen.insert(g.nodes[n].path.as_str()) {
            files.push(g.nodes[n].path.clone());
        }
    }
    files
}

/// Reciprocal rank fusion of two file rankings; ties keep the first ranking's order.
pub fn fuse(first: &[String], second: &[String]) -> Vec<String> {
    let mut score: HashMap<&str, f64> = HashMap::new();
    for ranking in [first, second] {
        for (rank, path) in ranking.iter().enumerate() {
            *score.entry(path).or_default() += 1.0 / (FUSION_K + rank as f64 + 1.0);
        }
    }
    let mut order: Vec<&str> = Vec::new();
    for path in first.iter().chain(second) {
        if !order.contains(&path.as_str()) {
            order.push(path);
        }
    }
    let position: HashMap<&str, usize> = order.iter().enumerate().map(|(i, p)| (*p, i)).collect();
    order.sort_by(|a, b| {
        score[b]
            .total_cmp(&score[a])
            .then(position[a].cmp(&position[b]))
    });
    order.into_iter().map(str::to_string).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn query_terms_drop_stopwords_and_split_snake_case() {
        assert_eq!(
            query_terms("How does parse_claims fail with the leeway?"),
            ["parse", "claims", "fail", "leeway"]
        );
    }

    #[test]
    fn fusion_rewards_agreement_and_keeps_first_order_on_ties() {
        let a = ["x", "y", "z"].map(String::from);
        let b = ["y", "w"].map(String::from);
        assert_eq!(fuse(&a, &b), ["y", "x", "w", "z"]);
        assert_eq!(fuse(&a, &[]), ["x", "y", "z"]);
    }
}
