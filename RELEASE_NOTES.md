# Oakkeep 1.2.4

This is the first tagged release. It includes everything that landed after
the September 2026 source published on `main`.

## Highlights

- **Special messages.** Mark messages with a star; they stay at the top of
  the list. Marks live in the index and survive Rebuild.
- **Local timezone display.** The list shows the Date header converted to
  this computer's timezone. The index sort field is rewritten to UTC on
  first open; `.eml` files are not modified.
- **Missing Date header.** Messages with no `Date:` header now use the date
  from the first `Received:` header for the filename, the index, and the
  display. Existing empty-date rows are filled when the archive is opened.
- **Incomplete Thunderbird folders.** At the end of Copy from Thunderbird,
  Oakkeep lists folders whose local mbox looks incomplete relative to the
  `.msf` index and asks you to Repair Folder, then run Copy again.

## Install

Download `oakkeep_1.2.4_all.deb` and install:

```bash
sudo dpkg -i oakkeep_1.2.4_all.deb
```

Or clone the repository and run `python3 oakkeep.py` (keep `oakkeep.py` next
to the `oakkeep_app/` directory).

## Upgrade notes

Opening an existing archive once fills any blank dates and rewrites the
index sort field. Rebuild is not required. Special marks, if any, are kept.
