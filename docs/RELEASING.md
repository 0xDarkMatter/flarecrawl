# Releasing Flarecrawl

How to cut a release without the failure modes that bit v0.30.0 and v0.30.1.

## One-time PyPI setup (do this once, before the first release)

1. **Confirm PyPI account.** You need a PyPI account with permission to create
   the `flarecrawl` project. New accounts only — Trusted Publishing won't
   work with classic tokens once configured.

2. **Add the pending publisher.**
   - Go to <https://pypi.org/manage/account/publishing/>
   - Click **"Add a new pending publisher"**
   - Fill in:

     | Field | Value |
     |---|---|
     | **PyPI Project Name** | `flarecrawl` |
     | **Owner** | `0xDarkMatter` |
     | **Repository name** | `flarecrawl` |
     | **Workflow name** | `publish.yml` |
     | **Environment name** | `pypi` |

3. **Create the `pypi` environment in GitHub.**
   - Repo Settings → Environments → New environment → name it `pypi`
   - Add **required reviewers** (yourself) — this is the human-in-the-loop
     gate that protects against compromised CI

4. **Verify the configuration.** Cut a throwaway tag (`v0.0.0-test`) and
   watch the workflow. If `Publish to PyPI` job reaches the publish step
   and waits for environment approval, you're configured correctly. Cancel
   the run; delete the tag.

## Per-release checklist

### Pre-tag (local)

- [ ] All chips for this release are committed on `main`
- [ ] Working tree clean (`git status` returns only known cruft)
- [ ] CHANGELOG `[X.Y.Z]` section drafted
- [ ] README "Recent Updates" row drafted, oldest row trimmed
- [ ] `__version__` and `pyproject.toml` `version` bumped in lockstep
- [ ] **`just release-check`** runs green — local wheel build + smoke install
- [ ] Full test suite passes (`uv run pytest tests/ --ignore=tests/live`)
- [ ] (Optional) Live tests pass against your CF account
- [ ] E2E sanity check on the marquee feature for this release

### Tag + push

```bash
git commit -m "release: vX.Y.Z"
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

### Post-push (watch CI)

- [ ] `Publish to PyPI` workflow triggers on the tag push
- [ ] **Build + audit (locked)** job passes (uv sync, pip-audit, build, twine check)
- [ ] **Publish to PyPI (trusted publishing)** job requests environment approval
- [ ] You approve the deployment
- [ ] Publish step succeeds
- [ ] **Verify on PyPI** step confirms the version is live
- [ ] If anything fails, the workflow auto-opens a GitHub issue tagged `release-failure`

### GitHub release (after PyPI is live)

Per the `release-review` rule: surface the proposed `gh release create`
for explicit approval before running. Don't auto-publish releases.

```bash
gh release create vX.Y.Z \
  --repo 0xDarkMatter/flarecrawl \
  --title "vX.Y.Z — headline" \
  --notes-file <(awk '/## \[X.Y.Z\]/,/## \[/{print}' CHANGELOG.md | sed '$d')
```

## Common failure modes (post-mortem catalogue)

### Wheel build fails: "A second file is being added at the same path"

Cause: `pyproject.toml` lists a directory both under `packages = [...]`
*and* `[tool.hatch.build.targets.wheel.force-include]`. Hatchling rejects
the duplicate.

Prevention: `just release-check` runs `uv build --wheel` locally so this
fails before the tag, not after.

### CI dies on `git submodule foreach --recursive`

Cause: `.claude/worktrees/agent-*` paths committed as gitlinks (mode
160000) with no `.gitmodules` entry. Past `git add -A` sweeps in repos
with `.claude/` directories.

Prevention:
- `.claude/worktrees/` is in `.gitignore`
- Pre-commit hook in `.githooks/pre-commit` rejects mode-160000 entries
  with no matching `.gitmodules` row
- Never run `git add -A` in this repo — see `~/.claude/rules/worktree-boundaries.md`

### `invalid-publisher` on PyPI upload

Cause: Trusted publisher not configured on PyPI's side, or the workflow's
OIDC claims don't match what PyPI expects (wrong workflow filename,
wrong environment name, etc.).

Prevention: One-time setup above. The claims in the workflow are:
- `workflow_ref`: `0xDarkMatter/flarecrawl/.github/workflows/publish.yml`
- `environment`: `pypi`

These must match the PyPI publisher configuration exactly.

### Silent publish failure (you don't notice for hours)

Cause: GitHub Actions failure notifications drown in the noise; the
release page on GitHub looks fine even if PyPI rejected the upload.

Prevention: The `publish.yml` workflow now:
- Verifies the version is live on PyPI after upload
- Auto-opens a GitHub issue tagged `release-failure` if any job fails

## Yank a bad release

If a release is published but broken:

1. **PyPI**: <https://pypi.org/manage/project/flarecrawl/releases/> → Options → Yank release
   - Yanking is reversible. Deletion is permanent and the version can never
     be re-uploaded. Prefer yank.
2. **GitHub**: <https://github.com/0xDarkMatter/flarecrawl/releases> → Edit → "Mark as pre-release" or delete
3. **Cut a patch** (vX.Y.Z+1) with the fix. Never re-tag the same version
   — PyPI permanently blocks re-upload of yanked or deleted versions.

## Why each guard exists

| Guard | Catches |
|---|---|
| `uv sync --locked` in CI | Build-time dep injection / lock drift |
| `pip-audit` in CI | Fresh CVE in transitive deps |
| `twine check dist/*` in CI | sdist/wheel metadata corruption |
| Trusted Publishing (OIDC) | Long-lived PyPI token theft / phishing |
| PEP 740 attestations | Tampered artifacts in the GH→PyPI hop |
| Action SHA pins (not `@vN` tags) | Mutable-tag hijack (tj-actions pattern) |
| `environment: pypi` reviewer | Compromised CI publishing without human OK |
| `just release-check` (local) | Wheel build / install breakage pre-tag |
| Pre-commit gitlink hook | `.claude/worktrees/` leaking into commits |
| Post-publish PyPI verify | Silent upload rejection by PyPI |
| `release-failure` issue | Workflow failure going unnoticed |
