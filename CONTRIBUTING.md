# Contributing to Oakkeep

Thank you. The useful work is small, reviewable patches — not a rewrite.

## Before you start

- Open an issue first if the change is more than a few lines (new feature,
  index format, UI overhaul).
- Keep the UI in English.
- Do not add network access, telemetry, or cloud accounts.
- Do not commit `~/.config/oakkeep/config.json`, mail, or `.sqlite` files.
- Keep `oakkeep.py` next to the `oakkeep_app/` package.

## How to work

1. Fork the repository.
2. Clone your fork and create a branch:
   `git checkout -b fix/short-name`
3. Run locally: `python3 oakkeep.py` (needs `python3-gi` and GTK 3).
4. Change only what the issue needs. Prefer edits in
   `oakkeep_app/window.py`, `store.py`, `mail.py`, `folders.py`, `icons.py`.
5. Open a pull request against `main` and describe:
   - what problem you saw
   - what you changed
   - how you tested it (distro, Thunderbird yes/no)

## What we will probably accept
- Bug fixes (index, copy, folder list, stop/cancel, trash).
- UI polish that stays on GTK 3 (layout, icons, accessibility, dark theme).
- Translations, if they go through a proper i18n setup (not one-off edits).
- Packaging: .deb (preferred) or distro packages, as long as
  `python3 oakkeep.py` from the repo still works.
- No Flatpak. It pulls a large runtime for a small GTK app that
  should use the system Python and GTK.

## What we will probably reject
- Turning Oakkeep into an IMAP/Gmail client (passwords, OAuth, network
  mail). Thunderbird stays the client; Oakkeep archives local mail.
- A rewrite in another toolkit or in the browser.
- Drive-by dependency dumps.
- Flatpak / Snap / bundled runtimes that duplicate GTK and Python.

## License

By opening a pull request you agree that your contribution is licensed
under the same MIT license as the rest of the project.
