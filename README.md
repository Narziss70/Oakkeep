# Oakkeep

Oakkeep is a small desktop program for **Linux** that keeps a local archive of
email as ordinary `.eml` files and lets you search it.

It can copy new messages out of **Mozilla Thunderbird** (mbox folders under
your profile) into that archive without writing a message that is already
there. It does not talk to Gmail, IMAP or any other server. Thunderbird stays
the live mail client; Oakkeep is only the archive.

The archive on disk is the source of truth. A SQLite index next to the files
makes search fast; if the index breaks you rebuild it from the `.eml` files.

Developed with assistance from [Grok](https://grok.x.ai/) (xAI).

## What it does

- Browse and search an existing folder of `.eml` files (subject, from, to,
  attachment names, body).
- Copy new mail from one or more Thunderbird accounts into that folder.
- Skip messages already in the archive (Message-ID / fingerprint).
- Choose, per account, which Thunderbird folders to copy (Inbox, Sent, …)
  and persist that choice. Drafts, Junk and Trash can be left unchecked.
- Index extra archive folders that have **no** Thunderbird pair (old
  exports). Those folders are searchable; Copy does not touch them.
- Stop a long index or copy and close the database cleanly.
- Desktop notification when index or copy finishes.
- Open a message in the default app, show it in the file manager, move it
  to Trash, or delete it (the index is updated).

It does **not** send mail, change Thunderbird settings, or upload anything.

## Requirements

- Linux with GTK 3 (developed on Linux Mint / Cinnamon; Ubuntu and similar
  should work).
- Python 3.10 or newer.
- Packages: `python3`, `python3-gi`, `gir1.2-gtk-3.0`.
  Optional: `gir1.2-notify-0.7` (desktop notification), `libnotify-bin`.

```bash
sudo apt install python3 python3-gi gir1.2-gtk-3.0 gir1.2-notify-0.7
```

No pip packages are required.

## Install

1. Clone or download this repository.
2. Keep `oakkeep.py` next to the `oakkeep_app/` directory. Do not move the
   launcher out of the folder.
3. Start it:

```bash
python3 oakkeep.py
```

or run the `oakkeep` wrapper in the same folder.

On first start Oakkeep writes a menu entry
`~/.local/share/applications/oakkeep.desktop` and icons under
`~/.local/share/icons/hicolor/`. If the menu still shows a generic Python
icon, unpin the launcher, restart Cinnamon (`Ctrl+Alt+Esc` on Mint), and pin
**Oakkeep** from the menu.

Settings live in `~/.config/oakkeep/config.json` (window layout and
Thunderbird → archive pairs). That file is created automatically and is
**not** part of this repository.

## First use

1. **Open archive** — choose the folder that should hold `.eml` files
   (empty is fine; Oakkeep creates subfolders).
2. **Settings → Add pair** for each Thunderbird account:
   - Thunderbird folder: one server directory, for example
     `~/.thunderbird/<profile>/ImapMail/imap.example.com`  
     Do **not** pick the parent `ImapMail` folder.
   - Archive subfolder: name under the archive, for example
     `me@example.com`.
   - Tick the folders to copy. Default is all selected.
3. **Copy from Thunderbird** — new messages are written as `.eml`.
4. Old `.eml` trees with no pair are still indexed: put them inside the
   archive folder and use **Settings → Rebuild index** once.

Daily use is Open archive (remembered) then Copy. Rebuild only if the index
is empty or damaged. The index file is `.oakkeep-index.sqlite` inside the
archive folder.

## Layout of an archive

```
Archive/
  .oakkeep-index.sqlite
  me@example.com/
    Inbox/
    Sent/
  old-export@example.com/     # no Thunderbird pair; search only
    INBOX/
      Saved/
```

## License

MIT. See [LICENSE](LICENSE). Credits: [CREDITS.md](CREDITS.md).

## Disclaimer

Oakkeep is provided as-is. Keep backups of important mail. Closing the
window during an index stops work and commits the database; the `.eml`
files are not rewritten by a rebuild.
