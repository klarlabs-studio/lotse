//! Bounded lexical references in source documentation, without executing content.
use std::collections::BTreeSet;

/// ASCII function identifiers in calls or standalone inline-code spans.
/// Preserve first occurrence; generic short names and oversized scans are ignored.
pub(crate) fn symbols(text: &str) -> Vec<String> {
    let bounded: String = text.chars().take(4096).collect();
    let mut chars = bounded.char_indices().peekable();
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    while let Some((start, first)) = chars.next() {
        if !first.is_alphanumeric() && first != '_' {
            continue;
        }
        let mut end = start + first.len_utf8();
        while let Some(&(i, c)) = chars.peek() {
            if !c.is_alphanumeric() && c != '_' {
                break;
            }
            end = i + c.len_utf8();
            chars.next();
        }
        let word = &bounded[start..end];
        if !(5..=128).contains(&word.len())
            || !word.is_ascii()
            || (!first.is_ascii_alphabetic() && first != '_')
        {
            continue;
        }
        let after = &bounded[end..];
        let quoted = bounded[..start].ends_with('`') && after.starts_with('`');
        if (quoted || after.trim_start().starts_with('(')) && seen.insert(word.to_owned()) {
            result.push(word.to_owned());
            if result.len() == 32 {
                break;
            }
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::symbols;

    #[test]
    fn explicit_names_preserve_case_and_exclude_prose_and_partial_unicode() {
        assert_eq!(
            symbols(
                "proseWord `exactName` api.memberCall () short() x() 123456() éhidden() exactName()"
            ),
            ["exactName", "memberCall", "short"]
        );
    }

    #[test]
    fn scans_and_reference_counts_are_bounded() {
        assert!(symbols(&format!("{} hiddenFunction()", "界".repeat(4096))).is_empty());
        assert_eq!(
            symbols(
                &(0..100)
                    .map(|i| format!("function_{i}() "))
                    .collect::<String>()
            )
            .len(),
            32
        );
        assert_eq!(symbols(&format!("{}()", "a".repeat(129))).len(), 0);
    }
}
