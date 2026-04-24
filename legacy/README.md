# Legacy Code

This folder contains old app/demo/pipeline code kept for reference after the `src/vs_app` modular refactor.

Runtime code should prefer canonical imports from:

- `vs_app.api`
- `vs_app.modules`
- `vs_app.integrations`
- `vs_app.shared`

Do not add new features here.

Legacy files are moved here only after imports are replaced or compatibility shims are added.
