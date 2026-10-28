//! Exact `cl100k_base` packing of indivisible sections under a token budget.
use tiktoken_rs::CoreBPE;

const SEPARATOR: &str = "\n\n";

/// Appends whole sections, separated by a blank line, while the complete
/// context stays within `budget` tokens. A section that does not fit is
/// skipped, so a smaller later section can still fit.
///
/// Counting is exact and linear in the delivered text. cl100k pre-tokenization
/// never joins text across a newline that is directly followed by
/// non-whitespace, so tokens before the last such boundary are "settled":
/// appending cannot change them, and only the tail after it is re-encoded.
pub(crate) struct Packer<'a> {
    bpe: &'a CoreBPE,
    budget: usize,
    context: String,
    settled: usize,
    settled_tokens: usize,
    tokens: usize,
}

impl<'a> Packer<'a> {
    pub(crate) fn new(bpe: &'a CoreBPE, budget: usize) -> Self {
        Self {
            bpe,
            budget,
            context: String::new(),
            settled: 0,
            settled_tokens: 0,
            tokens: 0,
        }
    }

    /// Append `section` if the whole context still fits; report whether it did.
    pub(crate) fn try_push(&mut self, section: &str) -> bool {
        let unsettled = &self.context[self.settled..];
        let mut tail = String::with_capacity(unsettled.len() + SEPARATOR.len() + section.len());
        tail.push_str(unsettled);
        if !self.context.is_empty() {
            tail.push_str(SEPARATOR);
        }
        tail.push_str(section);
        let split = settle_point(&tail);
        let head_tokens = self.count(&tail[..split]);
        let total = self.settled_tokens + head_tokens + self.count(&tail[split..]);
        if total > self.budget {
            return false;
        }
        self.context.truncate(self.settled);
        self.context.push_str(&tail);
        self.settled += split;
        self.settled_tokens += head_tokens;
        self.tokens = total;
        true
    }

    pub(crate) fn tokens(&self) -> usize {
        self.tokens
    }

    pub(crate) fn into_context(self) -> String {
        self.context
    }

    fn count(&self, text: &str) -> usize {
        if text.is_empty() {
            0
        } else {
            self.bpe.encode_ordinary(text).len()
        }
    }
}

/// Byte offset just after the last newline that is directly followed by
/// non-whitespace, or 0 when there is none.
fn settle_point(text: &str) -> usize {
    let mut point = 0;
    let mut after_newline = false;
    for (i, c) in text.char_indices() {
        if after_newline && !c.is_whitespace() {
            point = i;
        }
        after_newline = c == '\n';
    }
    point
}

#[cfg(test)]
mod tests {
    use super::*;
    use tiktoken_rs::cl100k_base;

    /// The quadratic reference this packer replaces: re-encode the whole candidate.
    struct Naive<'a> {
        bpe: &'a CoreBPE,
        budget: usize,
        context: String,
        tokens: usize,
    }

    impl Naive<'_> {
        fn try_push(&mut self, section: &str) -> bool {
            let candidate = if self.context.is_empty() {
                section.to_string()
            } else {
                format!("{}{SEPARATOR}{section}", self.context)
            };
            let count = self.bpe.encode_ordinary(&candidate).len();
            if count > self.budget {
                return false;
            }
            self.context = candidate;
            self.tokens = count;
            true
        }
    }

    /// Fragments chosen to stress pre-tokenizer boundaries: whitespace runs
    /// around newlines, punctuation that absorbs trailing newlines,
    /// contractions, digit runs, Unicode letters and Unicode whitespace.
    const FRAGMENTS: &[&str] = &[
        "\n", "\n\n", " ", "  ", "\t", "\r\n", "\r", " \n", "\n ", "\u{a0}", "\u{3000}", "a", "Z",
        "foo", "Bar", "é", "日本", "1", "2345", "'s", "'LL", "'", ")", "{", "}\n", "\"", "_", "-",
        "->", ";", "#", "😀", "\u{301}", "x\n", "0x1F",
    ];

    fn random_text(seed: &mut u64, pieces: usize) -> String {
        (0..pieces)
            .map(|_| {
                // xorshift64: deterministic without a dev-dependency.
                *seed ^= *seed << 13;
                *seed ^= *seed >> 7;
                *seed ^= *seed << 17;
                FRAGMENTS[(*seed % FRAGMENTS.len() as u64) as usize]
            })
            .collect()
    }

    #[test]
    fn settle_point_needs_non_whitespace_after_the_newline() {
        assert_eq!(settle_point("a\nb"), 2);
        assert_eq!(settle_point("a\n\n"), 0);
        assert_eq!(settle_point("a\n b\n\n"), 0);
        assert_eq!(settle_point("a\nb\n c\nd"), 7);
        assert_eq!(settle_point("é\n日"), 3);
    }

    #[test]
    fn linear_counts_match_full_reencoding_for_every_decision() {
        let bpe = cl100k_base().unwrap();
        let mut seed = 0x9e37_79b9_7f4a_7c15;
        for round in 0..400 {
            let budget = [0, 3, 40, 120, 400, usize::MAX][round % 6];
            let mut packer = Packer::new(&bpe, budget);
            let mut naive = Naive {
                bpe: &bpe,
                budget,
                context: String::new(),
                tokens: 0,
            };
            for _ in 0..12 {
                let pieces = 1 + (seed % 40) as usize;
                let section = random_text(&mut seed, pieces);
                assert_eq!(packer.try_push(&section), naive.try_push(&section));
                assert_eq!(packer.tokens(), naive.tokens);
                assert_eq!(packer.context, naive.context);
            }
            assert_eq!(packer.tokens(), bpe.encode_ordinary(&packer.context).len());
        }
    }
}
