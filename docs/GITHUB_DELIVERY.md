# GitHub delivery workflow

## Publishing through the Git Data API

A local `git push` is not required when the authorized GitHub integration exposes
Git tree, commit, and reference write operations.

1. Read the target branch head and its tree SHA immediately before preparing a change.
2. Create a tree using that existing tree as `base_tree`, changing only reviewed paths.
3. Create one commit for one review task, with the observed branch head as its parent.
4. Update the existing branch reference with `force=false` (fast-forward only).
5. Read the remote branch again and verify the published commit and changed-file diff.

Creating a blob, tree, or unattached commit alone does **not** prove that a change
was published to a branch. If a concurrent change prevents a fast-forward,
read and review the new head before rebuilding the change; never force an overwrite.

## Review safeguards

Use `release/5.0.0rc6` for the RC6 work. Keep `main` and historical release branches
unchanged during publication smoke checks. A documentation-only smoke commit
checks the delivery path, not runtime correctness, test coverage, or Pine parity.
Keep source changes and their regression tests together in the same task commit.
Do not mark a review task complete merely because its commit was published.

## Authenticated CLI alternative

An authorized developer environment can use `gh auth login --web --git-protocol https`,
`gh auth setup-git`, and ordinary `git push`. Authentication must be configured in
that environment; connector credentials are not assumed to be available to a shell.
Do not place tokens or private keys in repository files, patches, or chat messages.

## References

- https://docs.github.com/en/rest/git/trees
- https://docs.github.com/en/rest/git/refs
- https://cli.github.com/manual/gh_auth_login
- https://cli.github.com/manual/gh_auth_setup-git
