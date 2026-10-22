//! Experimental issue-text rewriting: keep the title plus code-like tokens, drop prose.
//!
//! A port of `rewrite_query` in `benchmark/localization.py` (the `graf_floor_ids`
//! and `ast_graph_ids` screening arms, after BLIZZARD, Rahman and Roy 2018). It must
//! produce the same query as the prototype; `benchmark/check_rewrite_equivalence.py`
//! verifies this on every catalogued issue. Not an accepted improvement.
use regex::Regex;
use std::collections::HashSet;
use std::sync::LazyLock;

static CODE_SPANS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)```.*?```|`[^`\n]+`").expect("valid pattern"));
// The prototype's last alternative is `name(?=\()`; the regex crate has no
// lookahead, so it matches the parenthesis and drops it. No token can start
// at `(`, so the matches are the same.
// Python's `\w` is letters, numbers, and `_` (no combining marks), so `\w` is
// spelled out as `[\p{L}\p{N}_]`.
static CODE_TOKENS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&python_word(concat!(
        r"[\w./-]+\.(?:go|py|js|mjs|cjs|ts)\b",  // file paths
        r"|\b[A-Za-z_]\w+(?:\.[A-Za-z_]\w+)+\b", // dotted names
        r"|\b[a-z]+[A-Z]\w*\b",                  // camelCase
        r"|\b[A-Z][a-z0-9]+[A-Z]\w*\b",          // PascalCase with an inner capital
        r"|\b\w*[A-Za-z]_\w+\b",                 // snake_case and CONSTANTS
        r"|\b[A-Za-z_]\w*\(",                    // called names
    )))
    .expect("valid pattern")
});
static IDENTIFIER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&python_word(r"[A-Za-z_]\w+")).expect("valid pattern"));

fn python_word(pattern: &str) -> String {
    pattern
        .replace(r"[\w./-]", r"[\p{L}\p{N}_./-]")
        .replace(r"\w", r"[\p{L}\p{N}_]")
}

/// The issue title, a newline, and the body's code-like tokens in first-seen order;
/// the issue unchanged when the body has none.
pub fn rewrite_query(issue: &str) -> String {
    let (title, body) = issue.split_once('\n').unwrap_or((issue, ""));
    let mut found: Vec<&str> = CODE_TOKENS
        .find_iter(body)
        .map(|m| m.as_str().strip_suffix('(').unwrap_or(m.as_str()))
        .collect();
    for span in CODE_SPANS.find_iter(body) {
        found.extend(IDENTIFIER.find_iter(span.as_str()).map(|m| m.as_str()));
    }
    let mut seen = HashSet::new();
    let tokens: Vec<&str> = found.into_iter().filter(|t| seen.insert(*t)).collect();
    if tokens.is_empty() {
        issue.to_string()
    } else {
        format!("{title}\n{}", tokens.join(" "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_title_and_code_like_tokens() {
        let issue = "reply.send fails\n\nWhen I call `reply.code(204)` the onSend hook in lib/reply.js \
                     throws FST_ERR_REP_ALREADY_SENT and the docs say nothing.\n```js\nfastify.addHook('onSend', fn)\n```";
        let query = rewrite_query(issue);
        let (title, tokens) = query.split_once('\n').unwrap();
        assert_eq!(title, "reply.send fails");
        for token in [
            "reply.code",
            "onSend",
            "lib/reply.js",
            "FST_ERR_REP_ALREADY_SENT",
            "addHook",
        ] {
            assert!(
                tokens.split(' ').any(|t| t == token),
                "{token} missing from {tokens}"
            );
        }
        assert!(!tokens.contains("docs"));
    }

    #[test]
    fn prose_only_issues_are_unchanged() {
        assert_eq!(rewrite_query("plain words only"), "plain words only");
        assert_eq!(rewrite_query("title\nno code here"), "title\nno code here");
    }

    #[test]
    fn called_names_drop_the_parenthesis_and_tokens_are_unique() {
        assert_eq!(rewrite_query("t\nrun(x) then run(y)"), "t\nrun");
    }
}
