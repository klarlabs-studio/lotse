//! Experimental physical-line passage selection. Paths and metadata do not score.
use crate::terms::Analyzer;
use std::collections::BTreeMap;

const WINDOW: usize = 24;
const SATURATION: f64 = 1.2;

/// Return zero-based [start,end) and strongest matching line within that range.
/// Select relevance before fitting: an unrelated short unit never replaces it.
pub(crate) fn best_passage(
    lines: &[&str],
    weights: &BTreeMap<String, f64>,
    analyzer: &Analyzer,
) -> Option<(usize, usize, usize)> {
    if lines.is_empty() {
        return None;
    }
    let ids: BTreeMap<_, _> = weights
        .keys()
        .enumerate()
        .map(|(i, s)| (s.as_str(), i))
        .collect();
    let idf: Vec<_> = weights.values().copied().collect();
    let total: f64 = idf.iter().sum();
    let rows: Vec<Vec<(usize, usize)>> = lines
        .iter()
        .map(|line| {
            let mut counts = BTreeMap::<usize, usize>::new();
            for term in analyzer.terms(line) {
                if let Some(&id) = ids.get(term.as_str()) {
                    *counts.entry(id).or_default() += 1;
                }
            }
            counts.into_iter().collect()
        })
        .collect();
    let width = WINDOW.min(lines.len());
    let mut active = BTreeMap::<usize, usize>::new();
    for row in &rows[..width] {
        for &(id, count) in row {
            *active.entry(id).or_default() += count;
        }
    }
    let score = |counts: &BTreeMap<usize, usize>| {
        if total == 0.0 {
            return 0.0;
        }
        let coverage: f64 = counts.keys().map(|&id| idf[id]).sum();
        let relevance: f64 = counts
            .iter()
            .map(|(&id, &count)| {
                let count = count as f64;
                idf[id] * count * (SATURATION + 1.0) / (count + SATURATION)
            })
            .sum();
        relevance * (coverage / total).powi(2)
    };
    let mut best = (0, score(&active));
    for start in 1..=lines.len() - width {
        for &(id, count) in &rows[start - 1] {
            let remaining = active.get_mut(&id).expect("active outgoing term");
            *remaining -= count;
            if *remaining == 0 {
                active.remove(&id);
            }
        }
        for &(id, count) in &rows[start + width - 1] {
            *active.entry(id).or_default() += count;
        }
        let value = score(&active);
        if value > best.1 {
            best = (start, value);
        }
    }
    let end = best.0 + width;
    let mut anchor = (best.0, -1.0f64);
    for (i, row) in rows.iter().enumerate().take(end).skip(best.0) {
        let value: f64 = row.iter().map(|&(id, _)| idf[id]).sum();
        if value > anchor.1 {
            anchor = (i, value);
        }
    }
    Some((best.0, end, anchor.0))
}

/// Search only uncovered contiguous gaps; zero novel query coverage adds nothing.
pub(crate) fn best_complement(
    lines: &[&str],
    weights: &BTreeMap<String, f64>,
    excluded: (usize, usize),
    analyzer: &Analyzer,
) -> Option<(usize, usize, usize)> {
    if weights.is_empty() {
        return None;
    }
    let mut best = None;
    let mut best_score = 0.0;
    let total: f64 = weights.values().sum();
    for (lo, hi) in [(0, excluded.0), (excluded.1, lines.len())] {
        let Some((start, end, anchor)) = best_passage(&lines[lo..hi], weights, analyzer) else {
            continue;
        };
        let mut counts = BTreeMap::<String, usize>::new();
        for line in &lines[lo + start..lo + end] {
            for term in analyzer.terms(line) {
                if weights.contains_key(&term) {
                    *counts.entry(term).or_default() += 1;
                }
            }
        }
        let coverage: f64 = counts.keys().map(|term| weights[term]).sum();
        let value: f64 = counts
            .iter()
            .map(|(term, &count)| {
                let tf = count as f64;
                weights[term] * tf * (SATURATION + 1.0) / (tf + SATURATION)
            })
            .sum();
        let score = value * (coverage / total).powi(2);
        if score > best_score {
            best_score = score;
            best = Some((lo + start, lo + end, lo + anchor));
        }
    }
    best
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn complements_exclude_delivered_lines_and_require_novel_matches() {
        let lines = ["beta", "alpha", "alpha", "gap", "beta beta"];
        let weights = BTreeMap::from([("beta".into(), 1.0)]);
        assert_eq!(
            best_complement(&lines, &weights, (1, 3), &Analyzer::default()),
            Some((3, 5, 4))
        );
        assert_eq!(
            best_complement(&lines, &weights, (0, 5), &Analyzer::default()),
            None
        );
        assert_eq!(
            best_complement(&lines, &BTreeMap::new(), (1, 3), &Analyzer::default()),
            None
        );
        assert_eq!(
            best_complement(&["unrelated"], &weights, (0, 0), &Analyzer::default()),
            None
        );
    }

    #[test]
    fn full_file_scan_prefers_breadth_over_repetition_and_ties_keep_source_order() {
        let mut lines = vec!["alpha alpha alpha"; 30];
        lines.extend(vec!["unrelated"; 30]);
        lines.extend(["alpha beta", "unrelated"]);
        let weights = BTreeMap::from([("alpha".into(), 1.0), ("beta".into(), 1.0)]);
        let result = best_passage(&lines, &weights, &Analyzer::default()).unwrap();
        assert_eq!(result, (37, 61, 60));
        assert_eq!(
            best_passage(&["alpha beta"; 30], &weights, &Analyzer::default()),
            Some((0, 24, 0))
        );
    }

    #[test]
    fn rare_terms_empty_inputs_and_unicode_keep_physical_coordinates() {
        let weights = BTreeMap::from([("alpha".into(), 0.1), ("café".into(), 3.0)]);
        let mut lines = vec!["alpha"; 30];
        lines.extend(vec!["unrelated"; 30]);
        lines.push("café\u{2028}samephysical");
        assert_eq!(
            best_passage(&lines, &weights, &Analyzer::default()),
            Some((37, 61, 60))
        );
        assert_eq!(best_passage(&[], &weights, &Analyzer::default()), None);
        assert_eq!(
            best_passage(&["unrelated"; 30], &weights, &Analyzer::default()),
            Some((0, 24, 0))
        );
        assert_eq!(
            best_passage(&["alpha"], &BTreeMap::new(), &Analyzer::default()),
            Some((0, 1, 0))
        );
    }
}
