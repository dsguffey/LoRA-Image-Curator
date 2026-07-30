# Contributing

LoRA Image Curator is currently in a focused pre-1.0 stabilization phase.
Bug reports and narrowly scoped fixes are more useful than large feature
expansions until the first real LoRA training trial and v1.0 release candidate
are complete.

## Before proposing a change

1. Check `BUGS.md`, `ROADMAP.md`, and `WISHLIST.md`.
2. Describe the user workflow and failure mode, not only the desired control.
3. Keep source images, catalogs, model files, logs, and private paths out of the
   report and repository.
4. Preserve local-first and non-destructive behavior.

## Code expectations

- Document intent, ownership, assumptions, and non-obvious constraints.
- Do not comment obvious syntax line by line.
- Keep Tk lifecycle and user confirmation in the UI layer.
- Keep SQLite writes transactional and schema changes migratable.
- Keep subprocess calls argument-based with `shell=False`.
- Add a dependency-light regression for every bug fix where practical.
- Update `CHANGELOG.md`, `BUGS.md`, and affected user/developer documentation.

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for validation commands and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module boundaries.

