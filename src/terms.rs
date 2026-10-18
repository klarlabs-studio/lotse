//! Lexical terms shared by indexing and queries.
use serde::{Deserialize, Serialize};

/// Fixed English and request-phrasing stopwords (accepted behavior).
///
/// Several entries (`test`, `implementation`, `change`, `claim`, ...) are
/// request phrasing rather than English function words. Changing this list
/// changes ranking, so it needs a preregistered comparison, not a refactor.
const REQUEST_STOP: &[&str] = &[
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

/// Arm C1: Lucene's default English stop set (`EnglishAnalyzer.ENGLISH_STOP_WORDS_SET`).
const ENGLISH_STOP: &[&str] = &[
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into", "is", "it",
    "no", "not", "of", "on", "or", "such", "that", "the", "their", "then", "there", "these",
    "they", "this", "to", "was", "will", "with",
];

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stopwords {
    /// Accepted behavior.
    #[default]
    Request,
    /// Arm C1.
    English,
    /// Arm C2: rely on BM25's IDF alone.
    None,
}

/// How text becomes terms. Indexes record a non-default analyzer, and queries
/// always use the analyzer their index was built with.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Analyzer {
    pub stopwords: Stopwords,
    /// Arm C3: also index each compound identifier whole, lowercased.
    pub full_identifiers: bool,
    /// Arm B2: path terms count this many times (BM25F with an integer field weight).
    pub path_weight: u32,
}

impl Default for Analyzer {
    fn default() -> Self {
        Self {
            stopwords: Stopwords::Request,
            full_identifiers: false,
            path_weight: 1,
        }
    }
}

impl Analyzer {
    pub(crate) fn is_default(&self) -> bool {
        *self == Self::default()
    }

    /// Split identifier boundaries as well as punctuation. No learned query rewriting.
    pub fn terms(&self, text: &str) -> Vec<String> {
        let mut terms: Vec<String> = split_identifiers(text)
            .split(|c: char| !c.is_alphanumeric())
            .filter(|s| !s.is_empty())
            .map(str::to_lowercase)
            .collect();
        if self.full_identifiers {
            terms.extend(compound_identifiers(text));
        }
        let stop = match self.stopwords {
            Stopwords::Request => REQUEST_STOP,
            Stopwords::English => ENGLISH_STOP,
            Stopwords::None => &[],
        };
        terms.retain(|term| !stop.contains(&term.as_str()));
        terms
    }

    /// Terms of one window: its path, weighted by `path_weight`, and its text.
    pub(crate) fn window_terms(&self, path: &str, text: &str) -> Vec<String> {
        if self.path_weight == 1 {
            return self.terms(&format!("{path}\n{text}"));
        }
        let path_terms = self.terms(path);
        let mut terms = Vec::new();
        for _ in 0..self.path_weight {
            terms.extend(path_terms.iter().cloned());
        }
        terms.extend(self.terms(text));
        terms
    }
}

/// Terms under the accepted analyzer.
pub fn terms(text: &str) -> Vec<String> {
    Analyzer::default().terms(text)
}

fn split_identifiers(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut split = String::new();
    for (i, &c) in chars.iter().enumerate() {
        if i > 0 && c.is_uppercase() && starts_word(&chars, i) {
            split.push(' ');
        }
        split.push(c);
    }
    split
}

/// Identifiers (runs of alphanumerics and `_`) that split into two or more parts.
fn compound_identifiers(text: &str) -> impl Iterator<Item = String> + '_ {
    text.split(|c: char| !(c.is_alphanumeric() || c == '_'))
        .filter(|raw| {
            split_identifiers(raw)
                .split(|c: char| !c.is_alphanumeric())
                .filter(|s| !s.is_empty())
                .nth(1)
                .is_some()
        })
        .map(str::to_lowercase)
}

/// An uppercase letter starts a word after lowercase or digits (`fooBar`, `v2Api`),
/// and at the end of an acronym (`HTTPServer` -> `HTTP Server`).
fn starts_word(chars: &[char], i: usize) -> bool {
    let previous = chars[i - 1];
    previous.is_lowercase()
        || previous.is_numeric()
        || (previous.is_uppercase() && chars.get(i + 1).is_some_and(|n| n.is_lowercase()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_camel_case_acronyms_digits_and_punctuation() {
        assert_eq!(terms("HTTPServer"), ["http", "server"]);
        assert_eq!(terms("parseJSONBody"), ["parse", "json", "body"]);
        assert_eq!(terms("v2Api"), ["v2", "api"]);
        assert_eq!(
            terms("snake_case-kebab.dot"),
            ["snake", "case", "kebab", "dot"]
        );
    }

    #[test]
    fn lowercases_keeps_unicode_and_removes_stopwords() {
        assert_eq!(terms("Where is the Größe check?"), ["größe"]);
        assert_eq!(terms("find tests for ExactArgs"), ["exact", "args"]);
        assert!(terms("  \n\t ").is_empty());
    }

    #[test]
    fn english_and_empty_stop_lists_keep_request_phrasing() {
        let english = Analyzer {
            stopwords: Stopwords::English,
            ..Analyzer::default()
        };
        assert_eq!(
            english.terms("find tests for ExactArgs"),
            ["find", "tests", "exact", "args"]
        );
        let none = Analyzer {
            stopwords: Stopwords::None,
            ..Analyzer::default()
        };
        assert_eq!(none.terms("the test"), ["the", "test"]);
    }

    #[test]
    fn full_identifiers_add_compounds_only() {
        let full = Analyzer {
            full_identifiers: true,
            ..Analyzer::default()
        };
        assert_eq!(
            full.terms("refreshCache(cache_key, x)"),
            [
                "refresh",
                "cache",
                "cache",
                "key",
                "x",
                "refreshcache",
                "cache_key"
            ]
        );
        assert_eq!(full.terms("plain words"), ["plain", "words"]);
    }

    #[test]
    fn path_weight_repeats_path_terms_and_weight_one_is_accepted_behavior() {
        let accepted = Analyzer::default();
        assert_eq!(
            accepted.window_terms("src/cacheStore.rs", "fn get()"),
            terms("src/cacheStore.rs\nfn get()")
        );
        let weighted = Analyzer {
            path_weight: 3,
            ..Analyzer::default()
        };
        let window = weighted.window_terms("store.rs", "fn get()");
        assert_eq!(window.iter().filter(|t| *t == "store").count(), 3);
        assert_eq!(window.iter().filter(|t| *t == "get").count(), 1);
    }
}
