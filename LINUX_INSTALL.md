# mini_agent — Linux Installation Guide

Tested on **Ubuntu 24.04**, **Ubuntu 22.04**, **Debian 12**, **Fedora 40**, and **Omarchy 3.8** (Arch).
Also works on Arch, openSUSE, and other modern distros (adjust package names as needed).

## Quick Install (Automated)

If you have the prerequisites installed, just run:

```bash
git clone https://github.com/YOUR_USERNAME/mini_agent.git
cd mini_agent
bash setup.sh
```

The script checks for Python 3, Node.js, npm, and ripgrep, creates a virtual environment,
installs all Python and Node dependencies, and builds the Electron renderer.
Expect ~5–10 minutes on first run (downloading ~2 GB of packages).

See [Manual Setup](#manual-setup) below if you prefer step-by-step.

---

## Prerequisites

| Tool | Required | Version | Ubuntu/Debian | Fedora | Omarchy / Arch |
|------|----------|---------|---------------|--------|----------------|
| **Python 3** | Required | 3.10–3.13 | `apt install python3 python3-venv python3-pip` | `dnf install python3 python3-pip` | `pacman -S python python-pip`¹ |
| **Node.js** | Required | 22+ (LTS) | [nodejs.org](https://nodejs.org/) or `snap install node --classic` | `dnf install nodejs` | `pacman -S nodejs npm` |
| **npm** | Required | 9+ | Bundled with Node.js | Bundled with Node.js | Bundled with Node.js |
| **git** | Recommended | any | `apt install git` | `dnf install git` | `pacman -S git`¹ |
| **ripgrep (rg)** | Recommended | any | `apt install ripgrep` | `dnf install ripgrep` | `pacman -S ripgrep` |
| **xclip / xsel** | Optional | any | `apt install xclip` | `dnf install xclip` | `pacman -S xclip` |

> ¹ Omarchy ships with Python and git pre-installed. Verify with `python3 --version` and `git --version`.  
> On Omarchy, you can also use the GUI: `Super + Alt + Space` → "Install → Package", or the CLI:
> ```bash
> omarchy pkg add nodejs npm ripgrep xclip
> ```

### Python 3

Most Linux distros ship Python 3 by default, but the **venv module** often requires a separate package:

```bash
# Ubuntu/Debian
sudo apt install python3 python3-venv python3-pip

# Fedora
sudo dnf install python3 python3-pip

# Arch / Omarchy — python ships pip bundled, no separate venv package needed
sudo pacman -S python python-pip
```

Check your version:
```bash
python3 --version   # must be 3.10 or later
```

> **Note:** On Linux, the command is `python3` (not `python`). If `python3` isn't found,
> your distro may still ship Python 2 as `python`. Install Python 3 explicitly:
> ```bash
> sudo apt install python3
> ```

### Node.js 22+ (LTS)

The easiest cross-distro method is [nvm](https://github.com/nvm-sh/nvm):

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc   # or: source ~/.zshrc
nvm install 22
nvm use 22
```

Or via package manager:

```bash
# Ubuntu/Debian — NodeSource PPA (recommended over the old snap)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# Fedora
sudo dnf install nodejs

# Arch / Omarchy
sudo pacman -S nodejs npm
# or via Omarchy CLI:
# omarchy pkg add nodejs npm
```

Verify:
```bash
node --version   # v22.x.x
npm --version    # 10.x.x
```

### ripgrep (rg)

Strongly recommended — file search falls back to slower `grep` without it.

```bash
# Ubuntu/Debian
sudo apt install ripgrep

# Fedora
sudo dnf install ripgrep

# Arch / Omarchy
sudo pacman -S ripgrep
```

### git

```bash
sudo apt install git    # Ubuntu/Debian
sudo dnf install git    # Fedora
sudo pacman -S git      # Arch / Omarchy (likely already installed)
```

### xclip or xsel (clipboard support)

Only needed if you use the `pyperclip` optional package for clipboard access:

```bash
sudo apt install xclip    # Ubuntu/Debian
sudo dnf install xclip    # Fedora
sudo pacman -S xclip      # Arch / Omarchy
```

### Electron runtime dependencies

The Electron desktop app requires these system libraries (usually already present on desktop installs, but minimal/server installs may need them):

```bash
# Ubuntu/Debian
sudo apt install libgtk-3-0 libnotify4 libnss3 libxss1 libxtst6 \
  xdg-utils libatspi2.0-0 libdrm2 libgbm1 libasound2 libxcb-cursor0

# Fedora
sudo dnf install gtk3 libnotify nss libXScrnSaver libXtst \
  xdg-utils at-spi2-core libdrm mesa-libgbm alsa-lib xcb-util-cursor

# Arch / Omarchy
sudo pacman -S gtk3 libnotify nss libxss libxtst xdg-utils \
  at-spi2-core libdrm mesa libxcb xcb-util-cursor
```

If these are missing, the Electron window will fail silently or show a blank screen.

### Omarchy / Hyprland notes

Omarchy uses **Hyprland** (Wayland), not X11. Electron works on Wayland but needs the Ozone
platform hint set. Add this to `~/.bashrc` (or `~/.zshrc`):

```bash
export ELECTRON_OZONE_PLATFORM_HINT=auto
```

Then reload: `source ~/.bashrc`

Without this, Electron may fall back to XWayland (works, but laggy) or fail to render.

> Omarchy ships with most Electron deps (gtk3, nss, mesa) pre-installed. If npm install
> succeeds but the Electron window is blank, check:
> ```bash
> ldd mini_agent_electron/node_modules/electron/dist/electron 2>&1 | grep "not found"
> ```
> Then install any missing libraries with `sudo pacman -S <package>`.

---

## Manual Setup (Step by Step)

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/mini_agent.git
cd mini_agent
```

### 2. Create a Python virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

> **`python3-venv` not found?**
> ```bash
> sudo apt install python3-venv   # Ubuntu/Debian
> ```

### 3. Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This installs ~7 packages (~2.5 GB on disk) including:
- **requests, numpy, tomli** — core runtime
- **sentence-transformers** — pulls PyTorch (~2 GB); 30–120 seconds on first install
- **exa-py** — web search client
- **pytest, pytest-timeout** — test runner

> **Low disk space?** Install PyTorch CPU-only to save ~1 GB:
> ```bash
> pip install torch --index-url https://download.pytorch.org/whl/cpu
> pip install -r requirements.txt
> ```

### 4. Install Playwright browser (optional)

```bash
python -m playwright install chromium
```

Downloads Chromium (~150 MB). Needed for browser automation tools (`open_url`, `browser_snapshot`, etc.).

> **Playwright missing system deps?** Run:
> ```bash
> python -m playwright install-deps chromium
> ```
> This auto-installs required libraries via apt/dnf (may need sudo).

### 5. Install Node.js dependencies

```bash
cd mini_agent_electron
npm install
```

Downloads Electron (~100 MB) and all renderer packages. Expect 2–5 minutes on first run.

> **Electron download fails behind a proxy?**
> ```bash
> export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
> npm install
> ```

### 6. Build the renderer

```bash
npm run build
```

Builds the React frontend to `mini_agent_electron/renderer/dist/`.

### 7. Configure API keys

Create a `.env` file in the repo root:

```env
# Required — at least one:
DEEPSEEK_API_KEY=sk-your-key-here
# ANTHROPIC_API_KEY=sk-ant-...
# XAI_API_KEY=xai-...
# OLLAMA_API_KEY=ollama

# Optional:
OPENAI_API_KEY=sk-...       # GPT-4o vision
EXA_API_KEY=...              # Exa web search
ELEVENLABS_API_KEY=...       # Discord bot TTS
```

Get keys from:
- **DeepSeek**: https://platform.deepseek.com/api_keys
- **Claude**: https://console.anthropic.com/
- **xAI/Grok**: https://x.ai/api

Alternatively, the Electron app has an in-app settings panel (persisted to `~/.mini_agent_env`).

> **Security tip:** `chmod 600 .env` to keep your API keys private.

---

## Launch

```bash
# Desktop app
cd mini_agent_electron
npm start

# Or CLI mode (no Electron)
cd ..
source venv/bin/activate
python3 -m mini_agent
```

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Submit message |
| `Shift+Enter` | New line |
| `Escape` | Cancel streaming response |
| `Ctrl+L` | Clear chat |

---

## Running Tests

```bash
cd mini_agent
source venv/bin/activate
python -m pytest
```

Runs 1,000+ tests. Add `-q` for quieter output, `-v` for verbose, or `--timeout=60` for hanging tests.

---

## Troubleshooting

### "python3-venv" package not found

```bash
sudo apt install python3-venv python3.12-venv   # Ubuntu/Debian
```
Replace `3.12` with your Python version (`python3 --version`).

> **Arch / Omarchy:** The `venv` module is included with `python` — no separate package.
> If you get a venv error, reinstall Python: `sudo pacman -S python`.

### Electron window shows a blank/white screen

The renderer wasn't built, or system libraries are missing:

```bash
# Rebuild the renderer
cd mini_agent_electron
npm run build

# Check for missing Electron libraries
ldd node_modules/electron/dist/electron 2>&1 | grep "not found"

# Install missing libs (Ubuntu/Debian)
sudo apt install libgtk-3-0 libnotify4 libnss3 libxss1 libxtst6 \
  xdg-utils libatspi2.0-0 libdrm2 libgbm1 libasound2 libxcb-cursor0

# Or (Arch / Omarchy):
sudo pacman -S gtk3 libnotify nss libxss libxtst xdg-utils \
  at-spi2-core libdrm mesa libxcb xcb-util-cursor
```

### "node: command not found" after nvm install

nvm needs to be sourced in your shell. Add this to `~/.bashrc` or `~/.zshrc`:

> **Omarchy uses Zsh by default.** Add nvm to `~/.zshrc`, not `~/.bashrc`.

```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
```

Then restart your terminal or run `source ~/.bashrc`.

### "pip: command not found"

On some distros, pip isn't installed by default:

```bash
sudo apt install python3-pip    # Ubuntu/Debian
sudo dnf install python3-pip    # Fedora
```

Or use the bundled installer:
```bash
python3 -m ensurepip --upgrade
```

### npm install fails with EACCES / permission errors

**Do NOT use `sudo npm install`.** Fix your npm permissions:

```bash
# If you installed Node via nvm, this should never happen.
# If you used apt/dnf, configure a user-level npm prefix:
mkdir -p ~/.npm-global
npm config set prefix ~/.npm-global
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
```

### Playwright Chromium crashes with "error while loading shared libraries"

Install the missing system dependencies:

```bash
python -m playwright install-deps chromium
```

This uses `apt` or `dnf` automatically (may prompt for sudo password).

### sentence-transformers / torch install is very slow

PyTorch wheels are large (~800 MB). Use a mirror:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Or use a PyPI mirror closer to your region (e.g., Tsinghua for Asia):
```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### "Cannot mix incompatible Qt library" or Wayland warnings

Electron on Wayland (including Hyprland via Omarchy) may need extra flags:

```bash
# Launch with Wayland support
cd mini_agent_electron
ELECTRON_OZONE_PLATFORM_HINT=wayland npm start

# Or force X11 (XWayland)
ELECTRON_OZONE_PLATFORM_HINT=x11 npm start
```

To make it permanent, add to `~/.bashrc`:
```bash
export ELECTRON_OZONE_PLATFORM_HINT=auto
```

### clipboard operations fail (pyperclip)

Install xclip or xsel:

```bash
sudo apt install xclip
```

Then verify:
```bash
echo "test" | xclip -selection clipboard
```

### Python imports fail with "No module named '_ctypes'"

Install libffi development headers:

```bash
sudo apt install libffi-dev    # Ubuntu/Debian
sudo dnf install libffi-devel  # Fedora
```

Then re-create your venv (it won't fix an existing one).

---

## Uninstall

```bash
# Deactivate virtual environment
deactivate

# Delete the repo folder
cd ..
rm -rf mini_agent

# Optionally remove Electron cache
rm -rf ~/.electron

# Optionally remove pip cache
rm -rf ~/.cache/pip

# Optionally remove npm cache
npm cache clean --force
rm -rf ~/.npm
```
