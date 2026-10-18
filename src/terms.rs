//! Lexical terms shared by indexing and queries.

/// Fixed English and request-phrasing stopwords.
///
/// Several entries (`test`, `implementation`, `change`, `claim`, ...) are
/// request phrasing rather than English function words. Changing this list
/// changes ranking, so it needs a preregistered comparison, not a refactor.
const STOP: &str = "a an the is are be to of for and or in on with its it this that which what where how why can as by from if should does do need find locate explain check claim against changes change implementation test tests";

/// Split identifier boundaries as well as punctuation. No learned query rewriting.
pub fn terms(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut split = String::new();
    for (i, &c) in chars.iter().enumerate() {
        if i > 0 && c.is_uppercase() && starts_word(&chars, i) {
            split.push(' ');
        }
        split.push(c);
    }
    split
        .split(|c: char| !c.is_alphanumeric())
        .filter(|s| !s.is_empty())
        .map(str::to_lowercase)
        .filter(|s| !STOP.split_whitespace().any(|stop| stop == s))
        .collect()
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
    use super::terms;

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
}
