# Jira Child Issue Debug

Jira Theme issues do not always expose their visible UI children through
`fields.subtasks[]` or through the standard `parent = KEY` JQL. Depending on
the Jira configuration, Theme-to-Epic hierarchy may live in a portfolio field,
an app-provided hierarchy field, an issue link function, or only be discoverable
through summary/title conventions.

Use the debug script to inspect the parent Theme, known child Epics, and several
candidate JQL patterns without changing production code.

```powershell
$env:JIRA_BASE_URL="https://jira.fyiblue.com"
$env:JIRA_TOKEN="..."

py -3 scripts/debug_jira_child_issues.py `
  --parent GROUP-22223 `
  --child GROUP-22805 `
  --child GROUP-22838 `
  --output-dir output/jira_child_debug
```

The script writes:

- `output/jira_child_debug/GROUP-22223_all_fields.json`
- `output/jira_child_debug/GROUP-22805_all_fields.json`
- `output/jira_child_debug/GROUP-22838_all_fields.json`
- `output/jira_child_debug/jql_results.json`

Inspect `jql_results.json` first. If one JQL returns both known child keys,
that JQL is the best candidate to add to `_collect_child_issues`.

If no JQL returns both children, inspect `child_interesting_fields` for rows
whose `matched_by` includes `value_contains_parent`. That means the child issue
payload has a field/customfield containing the parent Theme key, and the next
step is to query by that field.

If neither JQL nor child fields expose the parent relationship, use the summary
search fallback: query Epic issues by the parent Theme summary or a stable stage
suffix such as `Manage UM Operations`, then filter locally.
