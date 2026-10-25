# Node.js LTS on macOS (Homebrew, nvm, and GSD)

**This repository** expects Node for the dashboard; `ARCHITECTURE.md` documents **nvm** and `./start.sh`, which loads nvm when present. You do **not** need Homebrew for Node if you already use nvm — use an **even-numbered LTS** line (e.g. 22 or 24), not odd “Current” releases.

**GSD** (`gsd-pi`) wants **Node v22 or later** and is tested most against **LTS** (even major versions).

---

## Check your current version

```bash
node --version
```

If this shows an **odd** major (e.g. `v23.x`, `v25.x`), you are on a non-LTS “Current” release.

---

## Option A — nvm (matches this repo)

```bash
nvm install 24
nvm use 24
# optional: default for new shells
nvm alias default 24
```

Per-project pin: add a `.nvmrc` file containing `24` (or `24.15.0`) in the repo root or `dashboard/`.

---

## Option B — Pin Node 24 LTS with Homebrew

If you installed Node with `brew install node`, you are following Homebrew’s **default `node` formula**, which can track **non-LTS** majors. To stay on **Node 24 LTS**:

```bash
# Unlink the current (possibly non-LTS) version
brew unlink node

# Install Node 24 LTS
brew install node@24

# Link it as the default (if Homebrew allows linking for this formula)
brew link --overwrite node@24
```

If `brew` prints that `node@24` is **keg-only**, put it first on your `PATH` instead (Apple Silicon example):

```bash
echo 'export PATH="/opt/homebrew/opt/node@24/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Verify:

```bash
node --version
# Should show v24.x.x
```

### Why pin to LTS?

- **Stability** — LTS lines get fixes and security updates on a long support schedule.
- **Compatibility** — Many npm packages (including GSD) are validated most on LTS.
- **Predictability** — You avoid accidentally tracking odd “Current” majors.

### Prevent accidental upgrades

```bash
brew pin node@24
```

Unpin later:

```bash
brew unpin node@24
```

### Multiple Node versions

For several versions side by side (e.g. 22 and 24), prefer a version manager:

- **[nvm](https://github.com/nvm-sh/nvm)** — `nvm install 24 && nvm use 24`
- **[fnm](https://github.com/Schniz/fnm)** — `fnm install 24 && fnm use 24`
- **[mise](https://mise.jdx.dev/)** — `mise use node@24`

Use `.node-version` or `.nvmrc` in a project directory to pin per tree.

---

## Verify GSD

```bash
node --version   # v24.x.x (or another supported LTS ≥ 22)
npm install -g gsd-pi
gsd --version
```
