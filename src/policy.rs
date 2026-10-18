//! Experimental delivery policies from research/next-experiments.md.
//!
//! The defaults reproduce the accepted behavior exactly. Every constant is the
//! value frozen before measurement; changing one creates a new experiment.
use crate::source::Chunk;
use std::collections::HashMap;

/// Arm A1: never cut before this many candidates.
const GAP_MIN_RESULTS: usize = 3;
/// Arm A1: candidates kept after the largest gap.
const GAP_BUFFER: usize = 2;
/// Arm A1: search for the largest gap within this leading share of candidates.
const GAP_SEARCH_SHARE: f64 = 0.9;
/// Arm A2: stop at the first candidate scoring below this share of the best.
const FLOOR_RATIO: f64 = 0.5;
/// Arm B1: weights of a file's best, second, and third window scores.
const DAMPING: [f64; 3] = [1.0, 0.5, 0.25];

/// How many ranked candidates are offered to the packer.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum Stop {
    /// Offer every candidate; packing stops only at the budget (accepted behavior).
    #[default]
    Full,
    /// Arm A1: cut after the largest relative score gap, plus a fixed buffer.
    Gap,
    /// Arm A2: cut at a relative score floor.
    Floor,
}

/// How `locate` scores a file from its matching windows.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum FileScore {
    /// The file's best window (accepted behavior).
    #[default]
    Max,
    /// Arm B1: best window plus damped second and third windows.
    Damped,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Policy {
    pub stop: Stop,
    pub file_score: FileScore,
}

impl Stop {
    /// Number of leading candidates to keep from descending `scores`.
    pub(crate) fn keep(self, scores: &[f64]) -> usize {
        let n = scores.len();
        match self {
            Self::Full => n,
            Self::Floor => scores
                .iter()
                .position(|&s| s < FLOOR_RATIO * scores[0])
                .unwrap_or(n),
            Self::Gap if n <= GAP_MIN_RESULTS => n,
            Self::Gap => {
                let last = ((GAP_SEARCH_SHARE * n as f64).ceil() as usize).min(n - 1);
                // Position i (1-based) is the gap between candidates i and i + 1; first wins ties.
                let mut best = (f64::NEG_INFINITY, 1);
                for i in 1..=last {
                    let gap = (scores[i - 1] - scores[i]) / scores[0];
                    if gap > best.0 {
                        best = (gap, i);
                    }
                }
                (best.1.max(GAP_MIN_RESULTS) + GAP_BUFFER).min(n)
            }
        }
    }
}

impl FileScore {
    /// One anchor per file: its best window, ranked by the file score.
    /// Ties keep the order in which files first appear in `ranking`.
    pub(crate) fn files(self, ranking: Vec<(&Chunk, f64)>) -> Vec<(&Chunk, f64)> {
        let mut files: Vec<(&Chunk, f64, usize)> = Vec::new();
        let mut slot = HashMap::new();
        for (chunk, score) in ranking {
            match slot.get(chunk.path.as_str()) {
                None => {
                    slot.insert(chunk.path.as_str(), files.len());
                    files.push((chunk, score, 1));
                }
                Some(&i) => {
                    let (_, total, seen) = &mut files[i];
                    if self == Self::Damped && *seen < DAMPING.len() {
                        *total += DAMPING[*seen] * score;
                    }
                    *seen += 1;
                }
            }
        }
        if self == Self::Damped {
            files.sort_by(|a, b| b.1.total_cmp(&a.1));
        }
        files
            .into_iter()
            .map(|(chunk, score, _)| (chunk, score))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(path: &str, start: usize) -> Chunk {
        Chunk {
            id: format!("{path}{start}"),
            path: path.into(),
            start,
            end: start,
            text: String::new(),
        }
    }

    #[test]
    fn full_keeps_everything_and_floor_cuts_at_half_the_best() {
        let scores = [10.0, 7.0, 5.0, 4.9, 1.0];
        assert_eq!(Stop::Full.keep(&scores), 5);
        assert_eq!(Stop::Floor.keep(&scores), 3);
        assert_eq!(Stop::Floor.keep(&[3.0]), 1);
    }

    #[test]
    fn gap_cuts_after_largest_gap_with_minimum_and_buffer() {
        // Largest gap is after position 4 (8 -> 2); keep 4 + 2.
        let scores = [10.0, 9.5, 9.0, 8.0, 2.0, 1.9, 1.8, 1.7, 1.6, 1.5];
        assert_eq!(Stop::Gap.keep(&scores), 6);
        // Largest gap after position 1 is raised to the minimum of 3, then buffered.
        let scores = [10.0, 2.0, 1.9, 1.8, 1.7, 1.6, 1.5, 1.4];
        assert_eq!(Stop::Gap.keep(&scores), 5);
        // Short lists and the buffer never exceed the candidates.
        assert_eq!(Stop::Gap.keep(&[5.0, 1.0, 0.5]), 3);
        assert_eq!(Stop::Gap.keep(&[5.0, 4.0, 3.0, 0.1]), 4);
    }

    #[test]
    fn gap_ignores_the_trailing_tenth_and_prefers_the_first_tie() {
        // The only big drop is in the last 10%, so it is out of range; the first
        // equal gap (position 1) wins, raised to 3, plus 2.
        let mut scores: Vec<f64> = (0..19).map(|i| 20.0 - i as f64).collect();
        scores.push(0.0);
        assert_eq!(Stop::Gap.keep(&scores), 5);
    }

    #[test]
    fn max_keeps_best_window_order_and_damped_rewards_repeated_evidence() {
        let (a1, b1, b2, b3, b4) = (
            chunk("a", 1),
            chunk("b", 1),
            chunk("b", 25),
            chunk("b", 49),
            chunk("b", 73),
        );
        let ranking = vec![(&a1, 10.0), (&b1, 8.0), (&b2, 6.0), (&b3, 4.0), (&b4, 4.0)];
        let max = FileScore::Max.files(ranking.clone());
        assert_eq!(
            max.iter()
                .map(|(c, s)| (c.path.as_str(), *s))
                .collect::<Vec<_>>(),
            [("a", 10.0), ("b", 8.0)]
        );
        let damped = FileScore::Damped.files(ranking);
        // b: 8 + 0.5 * 6 + 0.25 * 4 = 12; the fourth window adds nothing.
        assert_eq!(
            damped
                .iter()
                .map(|(c, s)| (c.path.as_str(), *s))
                .collect::<Vec<_>>(),
            [("b", 12.0), ("a", 10.0)]
        );
        assert_eq!(damped[0].0.start, 1, "the anchor stays the best window");
    }

    #[test]
    fn damped_ties_keep_first_appearance_order() {
        let (a, b) = (chunk("a", 1), chunk("b", 1));
        let files = FileScore::Damped.files(vec![(&a, 5.0), (&b, 5.0)]);
        assert_eq!(files[0].0.path, "a");
    }
}
