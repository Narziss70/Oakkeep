# Changelog

## 1.2.4 — 2026-10-14

### Added
- Incomplete-folder detection during Copy from Thunderbird. At the end of the
  copy, a dialog lists folders whose local mbox looks incomplete relative to
  the `.msf` index and asks you to Repair Folder in Thunderbird, then run
  Copy again. Healthy folders are still imported.
- Fallback date: when a message has no `Date:` header, the date from the
  first `Received:` header is used for the filename, the index, and the
  display.

### Fixed
- Messages that lack a `Date:` header no longer show an empty date in the
  list or the preview. Existing empty-date index rows are filled on the next
  open (the `.eml` files are not rewritten).

## 1.2.3

### Added
- Special messages. Right-click one or more messages and choose Mark as
  special or Unmark as special. A star appears in the list and marked
  messages stay at the top. Marks are stored in the index and survive
  Rebuild; they are not written into the `.eml` files.
- Version number shown at the bottom of the Help text.

## 1.2.2

### Changed
- Message times are shown in the computer's current timezone (the same
  instant a mailbox UI shows when the account timezone matches the laptop).
  The index sort field is rewritten to UTC the first time an archive is
  opened after this update. The `.eml` files are not modified.

## Earlier

The September 2026 published version is the baseline for the notes above.
