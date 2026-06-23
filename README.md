# pstc-scheduler

A keyboard-first terminal UI for finding and scheduling range appointments at the Clackamas County Sheriff's Office Public Safety Training Center (PSTC).

This project uses [Textual](https://textual.textualize.io/) for the TUI and [Playwright](https://playwright.dev/python/) to drive the PSTC scheduler in the background.

> Unofficial project. Not affiliated with or endorsed by Clackamas County or the Sheriff's Office.

## Features

- Browse available appointment months, days, and times from the terminal
- Fast keyboard navigation with vim-style movement
- Pre-fill booking details from environment variables or a local `.env` file
- Submit through the real PSTC scheduler flow
- Optional visible browser mode for demos/debugging

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/eric5rivera/pstc-scheduler/main/install.sh | bash && { rehash 2>/dev/null || hash -r 2>/dev/null || true; }
pstc-scheduler
```

The installer clones/updates the repo under `~/.local/share/pstc-scheduler`, creates a virtual environment, installs Python dependencies, installs Playwright Chromium, and symlinks `pstc-scheduler` into a writable bin directory on your `PATH` when possible.

On Ubuntu/Debian, it also installs missing system packages with `sudo apt-get` and runs Playwright's Chromium dependency installer. On macOS, it checks for Git and Python 3.10+ and tells you how to install them if they are missing.

To watch the browser while the TUI runs:

```bash
pstc-scheduler --show-browser
```

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/eric5rivera/pstc-scheduler/main/install.sh | bash -s -- --uninstall
```

This removes the installed command, the app directory under `~/.local/share/pstc-scheduler`, saved profile data under `~/.pstc-scheduler`, and any now-empty directories the installer created.

## Configuration

The TUI remembers booking form values locally after you type them, even if you go back or quit before submitting. Saved values live at:

```text
~/.pstc-scheduler/profile.yaml
```

The file is written with user-only permissions because it may contain personal contact information.

You can also create a local `.env` file to pre-fill or override the booking form:

```bash
cp .env.example .env
```

Supported variables:

```dotenv
PSTC_FIRST_NAME=
PSTC_LAST_NAME=
PSTC_PHONE=
PSTC_EMAIL=
PSTC_PARTICIPANTS=1
```

`.env` is ignored by git so personal information stays local.

## Usage

Run:

```bash
pstc-scheduler
```

General flow:

1. Wait for availability to load.
2. Pick a month.
3. Pick an available day.
4. Pick an available time.
5. Review/edit booking details.
6. Submit when ready.

Helpful controls are shown in the footer. The interface supports keyboard-driven navigation, including vim-style `h/j/k/l` movement.

## Development

```bash
git clone https://github.com/eric5rivera/pstc-scheduler.git
cd pstc-scheduler
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
python -m py_compile pstc_tui.py pstc_scheduler_classic.py
```

Run directly during development:

```bash
python pstc_tui.py --show-browser
```

## Demo ideas

For LinkedIn/GitHub:

- Record the TUI in a terminal with the browser hidden.
- Use fake/local `.env` values or blur personal information.
- Stop before final submission unless you intend to book an appointment.
- Add a GIF or screenshot to this README after recording.

## License

MIT
