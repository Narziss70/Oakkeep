"""GTK window — search the EML archive and copy new Thunderbird mail."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from .folders import folder_summary, leaf_rows, list_account_folders, merge_folder_map
from .icons import apply_window_icon, install_launcher, set_program_identity
from .mail import best_date, body_text, local_when, parse_message
from .store import Archive, load_config, load_pairs, save_config, save_pairs
from . import __version__

set_program_identity()


def help_text(script: Path) -> str:
    cmd = str(script)
    return f"""Oakkeep copies new Thunderbird mail into a folder of .eml files and searches that archive. The files are the archive. The SQLite file next to them (.oakkeep-index.sqlite) is only an index and can be rebuilt.

Open archive
  Point at the folder that already holds your account folders (for example the parent of me@example.com).

Settings
  Pair each Thunderbird account folder (the one that contains INBOX) with an archive subfolder name.
  Tick the mailboxes to copy. A new pair starts with every folder ticked. Folders that appear later are listed but left unticked until you select them. Unticked folders, including Trash, Drafts, and Spam, are not copied and no archive folder is created for them.

Copy from Thunderbird
  Copies only ticked folders. A message already in the archive (same Message-ID, or the same from/date/subject key) is skipped. Stop cancels the job.

Times
  The list shows the Date header converted to this computer's timezone, the same instant Gmail shows when the account timezone matches the laptop. The first time an archive is opened after this update, the index sort field is rewritten. The .eml files are not modified. Rebuild is not required.

Search
  Type in the box and press Enter. The left list is archive folders. Right-click an empty folder (no .eml files in it or under it) to move it to the desktop Trash. Folders that still contain mail have no delete menu.

Duplicates
  Finds extra copies of the same message so you can trash or delete them.

Command line (cron)
  This does not open the window. It uses ~/.config/oakkeep/config.json (archive path and folder pairs), catches the index up with files already on disk, then copies ticked folders only.

  {cmd}

  Example crontab line, every day at 07:00:

  0 7 * * * {cmd} >> "$HOME/.local/share/oakkeep/sync.log" 2>&1

  Exit 0 means the job finished. Exit 1 means the archive or a pair could not be used. Accounts with no saved folder selection are skipped, not copied in full.

Special messages
  Select one or more messages, then right-click and choose Mark as special or Unmark as special. A star shows on marked messages, and they stay at the top of the list. The mark is kept in the index, not inside the .eml file. Rebuild does not remove it. Trash and Delete do.

Oakkeep {__version__}
"""


def choose_folder(parent, title: str, current: str = "") -> str | None:
    dialog = Gtk.FileChooserNative.new(
        title, parent, Gtk.FileChooserAction.SELECT_FOLDER, "Open", "Cancel"
    )
    if current:
        dialog.set_filename(current)
    ok = dialog.run() == Gtk.ResponseType.ACCEPT
    path = dialog.get_filename() if ok else None
    dialog.destroy()
    return path


class OakkeepWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Oakkeep")
        set_program_identity()
        apply_window_icon(self)
        install_launcher()
        cfg = load_config()
        self.set_default_size(int(cfg.get("win_w") or 1200), int(cfg.get("win_h") or 760))
        self.archive: Archive | None = None
        self.current_folder: str | None = None
        self.current_prefix = False
        self.current_path: str | None = None
        self._split_timers: dict[str, int | None] = {}
        self._suspend_select = False
        self._working = False
        self._layout_ready = False
        self._build()
        last = load_config().get("archive")
        if last and Path(last).is_dir():
            GLib.idle_add(self.open_archive, last, False)

    def _build(self) -> None:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        bar = Gtk.HeaderBar()
        bar.set_show_close_button(True)
        bar.set_title("Oakkeep")
        bar.set_subtitle("Local email archive")
        self.set_titlebar(bar)

        self.open_btn = Gtk.Button(label="Open archive")
        self.open_btn.connect("clicked", self.on_open)
        bar.pack_start(self.open_btn)

        self.copy_btn = Gtk.Button(label="Copy from Thunderbird")
        self.copy_btn.connect("clicked", self.on_copy)
        bar.pack_start(self.copy_btn)

        self.settings_btn = Gtk.Button(label="Settings")
        self.settings_btn.connect("clicked", self.on_settings)
        bar.pack_start(self.settings_btn)

        self.dup_btn = Gtk.Button(label="Duplicates")
        self.dup_btn.connect("clicked", self.on_duplicates)
        bar.pack_start(self.dup_btn)

        self.help_btn = Gtk.Button(label="Help")
        self.help_btn.connect("clicked", self.on_help)
        bar.pack_end(self.help_btn)

        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.connect("clicked", self.on_stop)
        self.stop_btn.set_sensitive(False)
        bar.pack_start(self.stop_btn)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search subject, from, to, attachment names…")
        self.search.set_size_request(280, -1)
        self.search.connect("activate", lambda *_: self.refresh_list())
        self.search.connect("search-changed", lambda *_: self.refresh_list() if not self.search.get_text() else None)
        bar.pack_end(self.search)

        self.status = Gtk.Label(xalign=0)
        self.status.set_margin_start(12)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_size_request(160, -1)
        self.progress.set_no_show_all(True)
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        status_box.set_margin_start(8)
        status_box.set_margin_end(8)
        status_box.set_margin_top(4)
        status_box.set_margin_bottom(4)
        self.stop_status_btn = Gtk.Button(label="Stop")
        self.stop_status_btn.connect("clicked", self.on_stop)
        self.stop_status_btn.set_sensitive(False)
        self.stop_status_btn.set_no_show_all(True)
        self.stop_status_btn.hide()
        status_box.pack_start(self.status, True, True, 0)
        status_box.pack_end(self.progress, False, False, 0)
        status_box.pack_end(self.stop_status_btn, False, False, 0)
        vbox.pack_start(status_box, False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_wide_handle(False)
        self.hpaned = paned
        vbox.pack_start(paned, True, True, 0)

        # display, filter value, count, mode (0=all, 1=account prefix, 2=exact folder)
        self.folder_store = Gtk.ListStore(str, str, int, int)
        self.folder_view = Gtk.TreeView(model=self.folder_store)
        self.folder_view.append_column(Gtk.TreeViewColumn("Folder", Gtk.CellRendererText(), text=0))
        self.folder_view.append_column(Gtk.TreeViewColumn("#", Gtk.CellRendererText(), text=2))
        self.folder_view.get_selection().connect("changed", self.on_folder)
        self.folder_view.connect("button-press-event", self.on_folder_click)
        folder_view = self.folder_view
        scroll_f = Gtk.ScrolledWindow()
        scroll_f.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_f.add(folder_view)
        scroll_f.set_size_request(240, -1)
        paned.add1(scroll_f)

        # path, date, from, subject, clip, size_text, date_sort, has_attach, size_bytes, special, star
        self.msg_store = Gtk.ListStore(str, str, str, str, str, str, str, int, int, int, str)

        def pinned(model, a, b, col):
            """Special messages stay above the rest. Column direction still applies within each group."""
            sa = int(model[a][9] or 0)
            sb = int(model[b][9] or 0)
            if sa != sb:
                displayed = sb - sa
                direction = Gtk.SortType.ASCENDING
                info = model.get_sort_column_id()
                if isinstance(info, tuple):
                    if len(info) == 3:
                        direction = info[2]
                    elif len(info) == 2:
                        direction = info[1]
                if direction == Gtk.SortType.DESCENDING:
                    return -displayed
                return displayed
            va = model[a][col]
            vb = model[b][col]
            if isinstance(va, str) or isinstance(vb, str):
                va = (va or "").lower()
                vb = (vb or "").lower()
            else:
                va = int(va or 0)
                vb = int(vb or 0)
            return (va > vb) - (va < vb)

        for sort_id in (2, 3, 6, 7, 8):
            self.msg_store.set_sort_func(sort_id, pinned, sort_id)
        msg_view = Gtk.TreeView(model=self.msg_store)
        msg_view.set_headers_clickable(True)
        self.msg_cols: dict[str, Gtk.TreeViewColumn] = {}
        cfg0 = load_config()
        saved_w = cfg0.get("col_widths") or {}
        for cid, title, text_idx, width, sort_id in (
            ("star", "★", 10, 46, 9),
            ("date", "Date", 1, 140, 6),
            ("from", "From", 2, 200, 2),
            ("subject", "Subject", 3, 360, 3),
            ("attach", "📎", 4, 48, 7),
            ("size", "Size", 5, 80, 8),
        ):
            renderer = Gtk.CellRendererText()
            renderer.set_property("xpad", 8)
            if cid in ("attach", "star"):
                renderer.set_property("xalign", 0.5)
            if cid == "date":
                renderer.set_property("xpad", 12)
            col = Gtk.TreeViewColumn(title, renderer, text=text_idx)
            col.set_sort_column_id(sort_id)
            col.set_clickable(True)
            col.set_resizable(True)
            col.set_reorderable(True)
            col.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            col.set_min_width(36)
            col.set_fixed_width(int(saved_w.get(cid, width)))
            col.set_alignment(0.5 if cid in ("attach", "star") else 0.0)
            col.connect("notify::width", lambda *_: self._remember_layout())
            msg_view.append_column(col)
            self.msg_cols[cid] = col
        msg_view.connect("columns-changed", lambda *_: self._remember_layout())
        self.msg_store.set_sort_column_id(6, Gtk.SortType.DESCENDING)
        msg_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        msg_view.get_selection().connect("changed", self.on_select)
        msg_view.connect("button-press-event", self.on_list_click)
        msg_view.connect("row-activated", self.on_row_activated)
        scroll_m = Gtk.ScrolledWindow()
        scroll_m.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_m.add(msg_view)
        self.msg_view = msg_view

        self.empty_label = Gtk.Label(label="No messages found")
        self.empty_label.get_style_context().add_class("dim-label")
        self.empty_label.set_halign(Gtk.Align.CENTER)
        self.empty_label.set_valign(Gtk.Align.CENTER)
        overlay = Gtk.Overlay()
        overlay.add(scroll_m)
        overlay.add_overlay(self.empty_label)
        self.empty_label.set_no_show_all(True)
        self.empty_label.hide()

        self.preview = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.preview.set_left_margin(10)
        self.preview.set_right_margin(10)
        self.preview.set_top_margin(8)
        scroll_p = Gtk.ScrolledWindow()
        scroll_p.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_p.add(self.preview)

        vpaned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        vpaned.set_wide_handle(True)
        vpaned.set_margin_start(10)
        vpaned.add1(overlay)
        vpaned.add2(scroll_p)
        self.vpaned = vpaned
        paned.add2(vpaned)

        paned.connect("notify::position", lambda *_: self._remember_layout())
        vpaned.connect("notify::position", lambda *_: self._remember_layout())
        self.connect("configure-event", lambda *_: self._remember_layout() or False)
        self.connect("map-event", self._on_map)
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", self._on_destroy)
        self._set_status("Open your EML archive folder to begin.")

    def _layout_payload(self) -> dict:
        w, h = self.get_size()
        order = []
        widths = {}
        id_of = {col: cid for cid, col in self.msg_cols.items()}
        for col in self.msg_view.get_columns():
            cid = id_of.get(col)
            if not cid:
                continue
            order.append(cid)
            widths[cid] = max(col.get_width(), col.get_fixed_width(), 36)
        return {
            "split_h": self.hpaned.get_position(),
            "split_v": self.vpaned.get_position(),
            "win_w": w,
            "win_h": h,
            "col_order": order,
            "col_widths": widths,
        }

    def _remember_layout(self, *_a) -> None:
        if not self._layout_ready:
            return
        old = self._split_timers.get("layout")
        if old:
            GLib.source_remove(old)

        def write():
            self._split_timers["layout"] = None
            save_config(self._layout_payload())
            return False

        self._split_timers["layout"] = GLib.timeout_add(250, write)

    def _apply_layout(self) -> bool:
        cfg = load_config()
        ww = int(cfg.get("win_w") or 0)
        hh = int(cfg.get("win_h") or 0)
        if ww >= 640 and hh >= 400:
            self.resize(ww, hh)
        if self.hpaned.get_allocated_width() < 80:
            return True
        sh = int(cfg.get("split_h") or 0)
        sv = int(cfg.get("split_v") or 0)
        if sh > 40:
            self.hpaned.set_position(sh)
        if sv > 40:
            self.vpaned.set_position(sv)
        order = list(cfg.get("col_order") or [])
        if order and "star" not in order:
            order = ["star"] + order
        prev = None
        for cid in order:
            col = self.msg_cols.get(cid)
            if not col:
                continue
            self.msg_view.move_column_after(col, prev)
            prev = col
        widths = cfg.get("col_widths") or {}
        for cid, col in self.msg_cols.items():
            if cid in widths and int(widths[cid]) >= 36:
                col.set_fixed_width(int(widths[cid]))
        return False

    def _on_map(self, *_a):
        self._apply_layout()
        GLib.timeout_add(80, self._apply_layout)
        GLib.timeout_add(300, self._finish_layout)
        return False

    def _finish_layout(self) -> bool:
        self._apply_layout()
        self._layout_ready = True
        return False

    def _on_delete(self, *_a):
        if self.archive:
            self.archive.request_cancel()
            self.archive.close()
            self.archive = None
        return False

    def _on_destroy(self, *_a) -> None:
        self._layout_ready = True
        if self.archive:
            self.archive.request_cancel()
            self.archive.close()
            self.archive = None
        save_config(self._layout_payload())
        Gtk.main_quit()

    def on_stop(self, *_a) -> None:
        if self.archive:
            self.archive.request_cancel()
        self._set_status("Stopping… closing the index cleanly.")

    def _notify_done(self, title: str, body: str) -> None:
        try:
            display = self.get_display()
            if display:
                display.beep()
        except Exception:
            pass
        try:
            gi.require_version("Notify", "0.7")
            from gi.repository import Notify

            if not getattr(Notify, "_oakkeep_inited", False):
                Notify.init("Oakkeep")
                Notify._oakkeep_inited = True
            n = Notify.Notification.new(title, body, "oakkeep")
            n.set_timeout(8000)
            n.show()
        except Exception:
            try:
                subprocess.Popen(
                    ["notify-send", "-a", "Oakkeep", "-i", "oakkeep", title, body],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass

    def _show_incomplete_dialog(self, folders: list[str]) -> None:
        dialog = Gtk.Dialog(
            title="Incomplete Thunderbird folders",
            transient_for=self,
            flags=0,
        )
        dialog.add_button("OK", Gtk.ResponseType.OK)
        dialog.set_default_size(560, 360)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        label = Gtk.Label(xalign=0)
        label.set_line_wrap(True)
        label.set_markup(
            "These folders appear to have an incomplete local message store "
            "(the .msf index is substantial but the mbox yielded few or no messages).\n\n"
            "In Thunderbird, right-click each folder → Properties → Repair Folder.\n"
            "Then click OK and run Copy from Thunderbird again. "
            "Messages already copied will be skipped."
        )
        box.add(label)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(160)
        textview = Gtk.TextView()
        textview.set_editable(False)
        textview.set_wrap_mode(Gtk.WrapMode.WORD)
        textview.get_buffer().set_text("\n".join(folders))
        scrolled.add(textview)
        box.add(scrolled)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _set_working(self, on: bool) -> None:
        self._working = on
        for btn in (self.open_btn, self.copy_btn, self.settings_btn, getattr(self, "dup_btn", None)):
            if btn is None:
                continue
            btn.set_sensitive(not on)
        for stop in (self.stop_btn, getattr(self, "stop_status_btn", None)):
            if stop is None:
                continue
            stop.set_sensitive(on)
            if on:
                stop.show()
            elif stop is self.stop_status_btn:
                stop.hide()
        self.search.set_sensitive(not on)
        # Keep the pointer usable so Stop can be clicked.
        self._busy_cursor(False)
        self._pump()

    def _pump(self) -> None:
        self.status.queue_draw()
        self.progress.queue_draw()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        try:
            Gdk.flush()
        except Exception:
            pass

    def _busy_cursor(self, on: bool) -> None:
        win = self.get_window()
        if not win:
            return
        if on:
            win.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), "watch"))
        else:
            win.set_cursor(None)

    def _set_status(self, text: str, done: int | None = None, total: int | None = None) -> None:
        self.status.set_text(text)
        if total and total > 0 and done is not None:
            self.progress.show()
            self.progress.set_fraction(min(max(done / total, 0.0), 1.0))
            self.progress.set_text(f"{int(100 * done / total)}%")
        elif done is not None:
            self.progress.show()
            self.progress.pulse()
            self.progress.set_text("")
        else:
            self.progress.set_fraction(0)
            self.progress.hide()
        self._pump()

    def open_archive(self, path: str, rebuild: bool) -> bool:
        if self._working:
            return False
        root = Path(path)
        if not root.is_dir():
            self._set_status("That folder does not exist.")
            return False
        self._set_working(True)
        try:
            if self.archive:
                self.archive.request_cancel()
                self.archive.close()
            self.archive = Archive(root)
            save_config({"archive": str(root)})
            self.set_title(f"Oakkeep — {root.name}")
            if not self.archive.integrity_ok():
                self._set_status("Index file is damaged. Rebuilding from .eml files…")
                rebuild = True
            elif self.archive.count() == 0 and self.archive.has_eml():
                self._set_status("Index is empty but .eml files are present. Indexing…")
                rebuild = True
            if rebuild or self.archive.count() == 0:
                self._set_status("Scanning for .eml files…", 0, 0)

                def prog(done, total, msg):
                    self._set_status(msg, done, total)

                n = self.archive.rebuild(prog)
                unique = self.archive.count()
                if self.archive.cancelled:
                    msg = f"Indexing stopped. {unique:,} messages indexed so far in {root.name}."
                else:
                    msg = f"Indexed {unique:,} messages ({n:,} files) in {root.name}"
                self._set_status(msg)
                self._notify_done("Oakkeep", msg)
            else:
                self._set_status(f"{self.archive.count():,} messages in {root.name}")
            self.refresh_folders()
            self.refresh_list()
        finally:
            self._set_working(False)
        return False

    def _existing_path(self, key: str) -> str | None:
        p = (load_config().get(key) or "").strip()
        return p if p and Path(p).is_dir() else None

    def _ask_folder(self, title: str, key: str) -> str | None:
        path = choose_folder(self, title, load_config().get(key, ""))
        if path:
            save_config({key: path})
        return path

    def on_help(self, *_a) -> None:
        script = Path(__file__).resolve().parent.parent / "oakkeep-sync"
        dialog = Gtk.Dialog(title="Oakkeep help", transient_for=self, flags=0)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(640, 520)
        buf = Gtk.TextBuffer()
        buf.set_text(help_text(script))
        view = Gtk.TextView(buffer=buf)
        view.set_editable(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.set_left_margin(12)
        view.set_right_margin(12)
        view.set_top_margin(12)
        view.set_bottom_margin(12)
        scroll = Gtk.ScrolledWindow()
        scroll.add(view)
        dialog.get_content_area().pack_start(scroll, True, True, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def on_folder_click(self, view, event) -> bool:
        if event.button != 3 or not self.archive:
            return False
        path_info = view.get_path_at_pos(int(event.x), int(event.y))
        if not path_info:
            return False
        path, _col, _cx, _cy = path_info
        model = view.get_model()
        rel = model[path][1]
        if not rel:
            return True
        view.get_selection().select_path(path)
        if self.archive.folder_message_count(rel) != 0:
            return True
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Move empty folder to Trash")
        item.connect("activate", lambda *_: self.trash_empty_folder(rel))
        menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def trash_empty_folder(self, rel: str) -> None:
        if not self.archive:
            return
        if self.archive.folder_message_count(rel) != 0:
            self._set_status("That folder is not empty. Only empty folders can be trashed.")
            return
        dialog = Gtk.MessageDialog(
            transient_for=self,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Move this empty folder to Trash?",
        )
        dialog.format_secondary_text(
            f"{rel}\n\nThe folder and any empty subfolders go to the desktop Trash. The index is updated."
        )
        ok = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        if not ok:
            return
        result = self.archive.trash_empty_folder(rel)
        if result != "ok":
            self._set_status("Could not move that folder to Trash.")
            return
        current = (self.current_folder or "").replace("\\", "/")
        if current == rel or current.startswith(rel + "/"):
            self.current_folder = None
            self.current_prefix = False
        self.refresh_folders()
        self.refresh_list()
        self._set_status(f"Moved empty folder to Trash: {rel}")

    def on_open(self, *_a) -> None:
        path = self._existing_path("archive") or self._ask_folder("EML archive folder", "archive")
        if path:
            self.open_archive(path, False)

    def on_rebuild(self, *_a) -> None:
        if not self.archive:
            self.on_open()
            return
        self.open_archive(str(self.archive.root), True)

    def on_copy(self, *_a) -> None:
        if not self.archive:
            self.on_open()
            if not self.archive:
                return
        items = load_pairs()
        if not items:
            self.on_settings()
            items = load_pairs()
        if not items:
            self._set_status("Add a Thunderbird → archive pair in Settings first.")
            return
        dirty = False
        for idx, pair in enumerate(items):
            if isinstance(pair.get("folders"), dict):
                continue
            tb_path = Path(pair.get("thunderbird") or "")
            label = (pair.get("account") or "").strip()
            if not label or not tb_path.is_dir():
                continue
            self._set_status(f"Choose folders to archive for {label}…")
            edited = self._edit_pair(pair)
            items[idx] = edited if edited else {**pair, "folders": {}}
            dirty = True
        if dirty:
            save_pairs(items)
            items = load_pairs()
        if self._working:
            return
        self._set_working(True)
        imported = skipped = 0
        missing = 0
        incomplete: list[str] = []
        try:
            if not self.archive.integrity_ok():
                self._set_status("Index is damaged. Use Settings → Rebuild index before copying.")
                return
            self._set_status("Checking archive against the index…", 0, 0)
            seen, added = self.archive.catch_up(
                lambda done, total, msg: self._set_status(msg, done, total)
            )
            if added:
                self._set_status(f"Indexed {added:,} files that were on disk but missing from the index.")
            n_pairs = len(items)
            for pi, pair in enumerate(items, 1):
                tb = Path(pair.get("thunderbird") or "")
                account = (pair.get("account") or "").strip()
                if not account:
                    continue
                if not tb.is_dir():
                    missing += 1
                    self._set_status(f"Pair {pi}/{n_pairs}: Thunderbird folder missing — {tb}")
                    continue

                def prog(i, t, n, s, pi=pi, n_pairs=n_pairs, account=account):
                    self._set_status(
                        f"Account {pi}/{n_pairs} {account} · mailbox {i}/{t} · copied {n} · skipped {s}",
                        i,
                        t,
                    )

                n, s, bad = self.archive.copy_thunderbird(
                    tb, account, prog, folder_enabled=pair.get("folders") if isinstance(pair.get("folders"), dict) else None
                )
                imported += n
                skipped += s
                for rel in bad:
                    incomplete.append(f"{account} / {rel}")
            self.refresh_folders()
            self.refresh_list()
            extra = f" · {missing} pair(s) skipped (folder missing)" if missing else ""
            stopped = "Copy stopped. " if self.archive and self.archive.cancelled else ""
            msg = (
                f"{stopped}Copied {imported} new messages · skipped {skipped} already in the archive{extra}."
            )
            self._set_status(msg)
            self._notify_done("Oakkeep", msg)
            if incomplete:
                self._show_incomplete_dialog(incomplete)
        finally:
            self._set_working(False)

    def _edit_pair(self, current: dict | None) -> dict | None:
        current = current or {}
        dialog = Gtk.Dialog(title="Account pair", transient_for=self, flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_size(660, 560)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        tb_l = Gtk.Label(label=current.get("thunderbird") or "(choose Thunderbird mailbox folder)", xalign=0)
        tb_l.set_line_wrap(True)
        tb_path = [current.get("thunderbird") or ""]
        saved_folders = dict(current.get("folders") or {}) if isinstance(current.get("folders"), dict) else {}
        checks: dict[str, Gtk.CheckButton] = {}
        listed_folders: list[dict] = []

        pick_b = Gtk.Button(label="Choose Thunderbird folder")
        box.add(Gtk.Label(label="Thunderbird folder (one server/account, not ImapMail itself)", xalign=0))
        hb = Gtk.Box(spacing=8)
        hb.pack_start(tb_l, True, True, 0)
        hb.pack_end(pick_b, False, False, 0)
        box.add(hb)
        box.add(Gtk.Label(label="Archive subfolder under Eml (pick an existing folder, or type a new name)", xalign=0))
        account_e = Gtk.Entry()
        account_e.set_text(current.get("account") or "")
        account_e.set_placeholder_text("me@example.com")
        account_e.set_hexpand(True)

        def pick_archive(_b):
            root = (load_config().get("archive") or "").strip()
            start = account_e.get_text().strip()
            current_dir = ""
            if root and start:
                candidate = Path(root) / start
                current_dir = str(candidate if candidate.is_dir() else Path(root))
            elif root:
                current_dir = root
            path = choose_folder(self, "Archive folder inside Eml", current_dir)
            if not path:
                return
            rel = path
            if root:
                try:
                    rel = str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
                except ValueError:
                    rel = Path(path).name
            if rel in {".", ""}:
                self._set_status("Pick a subfolder inside Eml, not Eml itself.")
                return
            account_e.set_text(rel)

        arch_b = Gtk.Button(label="Choose archive folder")
        arch_b.connect("clicked", pick_archive)
        ah = Gtk.Box(spacing=8)
        ah.pack_start(account_e, True, True, 0)
        ah.pack_end(arch_b, False, False, 0)
        box.add(ah)

        head = Gtk.Box(spacing=8)
        title = Gtk.Label(label="Folders to archive", xalign=0)
        title.get_style_context().add_class("heading")
        sel_all = Gtk.Button(label="Select all")
        des_all = Gtk.Button(label="Deselect all")
        head.pack_start(title, True, True, 0)
        head.pack_end(des_all, False, False, 0)
        head.pack_end(sel_all, False, False, 0)
        box.pack_start(head, False, False, 0)

        hint = Gtk.Label(
            label="Default: all selected. New folders stay selected after restart.",
            xalign=0,
        )
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("dim-label")
        box.pack_start(hint, False, False, 0)

        checks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(240)
        scroll.add(checks_box)
        box.pack_start(scroll, True, True, 0)

        count_l = Gtk.Label(xalign=0)
        count_l.get_style_context().add_class("dim-label")
        box.pack_start(count_l, False, False, 0)

        def listed_state() -> dict[str, bool]:
            return {key: cb.get_active() for key, cb in checks.items()}

        def update_count(*_a):
            if not checks:
                count_l.set_text("Choose the Thunderbird folder to list its mailboxes.")
                return
            leaves = leaf_rows(listed_folders) if listed_folders else []
            if not leaves:
                leaves = [{"key": k} for k in checks]
            n = sum(1 for row in leaves if checks.get(row["key"]) and checks[row["key"]].get_active())
            count_l.set_text(f"{n} of {len(leaves)} selected")

        def populate():
            live = listed_state()
            saved_folders.update(live)
            for child in list(checks_box.get_children()):
                checks_box.remove(child)
            checks.clear()
            listed_folders.clear()
            tb = tb_path[0].strip()
            if not tb or not Path(tb).is_dir():
                update_count()
                checks_box.show_all()
                return
            for folder in list_account_folders(Path(tb)):
                listed_folders.append(folder)
                cb = Gtk.CheckButton(label=folder["display"])
                # New pair: start checked. Later folders stay off until ticked.
                default_on = not isinstance(current.get("folders"), dict)
                cb.set_active(saved_folders.get(folder["key"], default_on))
                cb.set_margin_start(8 + int(folder["depth"]) * 16)
                cb.connect("toggled", update_count)
                checks_box.pack_start(cb, False, False, 0)
                checks[folder["key"]] = cb
            checks_box.show_all()
            update_count()

        def pick(_b):
            path = choose_folder(self, "Thunderbird mailbox folder (one account: INBOX / Sent)", tb_path[0])
            if path:
                tb_path[0] = path
                tb_l.set_text(path)
                if not account_e.get_text().strip():
                    account_e.set_text(Path(path).name)
                populate()

        def select_all(_b):
            for cb in checks.values():
                cb.set_active(True)

        def deselect_all(_b):
            for cb in checks.values():
                cb.set_active(False)

        pick_b.connect("clicked", pick)
        sel_all.connect("clicked", select_all)
        des_all.connect("clicked", deselect_all)

        dialog.show_all()
        populate()
        ok = dialog.run() == Gtk.ResponseType.OK
        account = account_e.get_text().strip()
        tb = tb_path[0].strip()
        folders = merge_folder_map(saved_folders, list(checks.keys()), listed_state())
        dialog.destroy()
        if not ok or not tb or not account:
            return None
        return {"thunderbird": tb, "account": account, "folders": folders}

    def on_settings(self, *_a) -> None:
        cfg = load_config()
        dialog = Gtk.Dialog(title="Settings", transient_for=self, flags=0)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(920, 520)
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        archive_l = Gtk.Label(label=cfg.get("archive") or "(not set)", xalign=0)
        archive_l.set_line_wrap(True)
        ab = Gtk.Button(label="Change")

        def change_archive(_b):
            path = self._ask_folder("EML archive folder", "archive")
            if path:
                archive_l.set_text(path)
                self.open_archive(path, False)

        ab.connect("clicked", change_archive)
        ar = Gtk.Box(spacing=8)
        ar.pack_start(Gtk.Label(label="EML archive folder", xalign=0), False, False, 0)
        ar.pack_start(archive_l, True, True, 0)
        ar.pack_end(ab, False, False, 0)
        box.pack_start(ar, False, False, 0)

        box.pack_start(
            Gtk.Label(
                label="Thunderbird → archive pairs (kept after relaunch). Copy runs every pair. Edit a pair to choose folders.",
                xalign=0,
            ),
            False,
            False,
            0,
        )
        store = Gtk.ListStore(str, str, str, str)
        view = Gtk.TreeView(model=store)
        view.append_column(Gtk.TreeViewColumn("Thunderbird folder", Gtk.CellRendererText(), text=0))
        view.append_column(Gtk.TreeViewColumn("Archive subfolder", Gtk.CellRendererText(), text=1))
        view.append_column(Gtk.TreeViewColumn("Folders", Gtk.CellRendererText(), text=2))
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(180)
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)

        def pair_folders(pair: dict) -> dict:
            folders = pair.get("folders")
            return folders if isinstance(folders, dict) else {}

        def row_from_pair(pair: dict) -> list:
            folders = pair_folders(pair)
            tb = pair.get("thunderbird") or ""
            rows = list_account_folders(Path(tb)) if tb and Path(tb).is_dir() else None
            return [
                pair.get("thunderbird") or "",
                pair.get("account") or "",
                folder_summary(folders, rows),
                json.dumps(folders),
            ]

        def reload_pairs():
            store.clear()
            for p in load_pairs():
                store.append(row_from_pair(p))

        def persist():
            items = []
            for r in store:
                item = {"thunderbird": r[0], "account": r[1]}
                try:
                    folders = json.loads(r[3] or "{}")
                except Exception:
                    folders = {}
                if isinstance(folders, dict) and folders:
                    item["folders"] = folders
                items.append(item)
            save_pairs(items)

        def selected_iter():
            model, it = view.get_selection().get_selected()
            return it

        def add_pair(_b):
            pair = self._edit_pair(None)
            if pair:
                store.append(row_from_pair(pair))
                persist()

        def edit_pair(_b):
            it = selected_iter()
            if not it:
                return
            try:
                folders = json.loads(store[it][3] or "{}")
            except Exception:
                folders = {}
            pair = self._edit_pair(
                {
                    "thunderbird": store[it][0],
                    "account": store[it][1],
                    "folders": folders if isinstance(folders, dict) else {},
                }
            )
            if pair:
                row = row_from_pair(pair)
                for i, value in enumerate(row):
                    store[it][i] = value
                persist()

        def remove_pair(_b):
            it = selected_iter()
            if not it:
                return
            store.remove(it)
            persist()

        btns = Gtk.Box(spacing=8)
        for label, fn in (("Add pair", add_pair), ("Edit", edit_pair), ("Remove", remove_pair)):
            b = Gtk.Button(label=label)
            b.connect("clicked", fn)
            btns.pack_start(b, False, False, 0)
        box.pack_start(btns, False, False, 0)

        rebuild = Gtk.Button(label="Rebuild index from .eml files")
        REBUILD = 1001
        rebuild.connect("clicked", lambda *_: dialog.response(REBUILD))
        box.pack_start(rebuild, False, False, 0)

        reload_pairs()
        dialog.show_all()
        resp = dialog.run()
        persist()
        dialog.destroy()
        # Close Settings first: a modal dialog blocks the header Stop button.
        if resp == REBUILD:
            GLib.idle_add(self.on_rebuild)

    def on_duplicates(self, *_a) -> None:
        if not self.archive:
            self.on_open()
            if not self.archive:
                return
        if self._working:
            return
        self._set_working(True)
        self.archive.cancelled = False
        try:
            self._set_status("Pass 1: comparing headers…", 0, 0)

            def prog(done, total, msg):
                self._set_status(msg, done, total)

            groups = self.archive.find_duplicate_groups(prog)
        finally:
            self._set_working(False)
        extras = sum(max(len(g) - 1, 0) for g in groups)
        if not groups:
            self._set_status("No duplicates after content check.")
            info = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="No duplicates found.",
            )
            info.format_secondary_text(
                "Pass 1: same folder, sender, recipient, subject and message time.\n"
                "Pass 2: same file content. Only groups that pass both are listed."
            )
            info.run()
            info.destroy()
            return
        self._set_status(f"{extras} extra copies in {len(groups)} groups (content verified).")
        self._duplicates_dialog(groups, extras)

    def _duplicates_dialog(self, groups: list, extras: int) -> None:
        dialog = Gtk.Dialog(title="Duplicates", transient_for=self, flags=0)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(960, 580)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.pack_start(
            Gtk.Label(
                label=(
                    f"{extras} extra file(s) in {len(groups)} group(s) after content check. "
                    "The first file in each group is kept unchecked."
                ),
                xalign=0,
            ),
            False,
            False,
            0,
        )
        # checked, path, folder, date, from, subject
        store = Gtk.ListStore(bool, str, str, str, str, str)

        def nocase(model, a, b, col):
            sa = (model[a][col] or "").lower()
            sb = (model[b][col] or "").lower()
            return (sa > sb) - (sa < sb)

        for col in (1, 2, 3, 4, 5):
            store.set_sort_func(col, nocase, col)
        view = Gtk.TreeView(model=store)
        view.set_headers_clickable(True)
        toggle = Gtk.CellRendererToggle()
        toggle.connect("toggled", lambda cell, path: store[path].__setitem__(0, not store[path][0]))
        col0 = Gtk.TreeViewColumn("", toggle, active=0)
        col0.set_sort_column_id(0)
        view.append_column(col0)
        for title, idx, width in (
            ("Folder", 2, 220),
            ("Date", 3, 130),
            ("From", 4, 180),
            ("Subject", 5, 260),
            ("File", 1, 220),
        ):
            r = Gtk.CellRendererText()
            r.set_property("ellipsize", Pango.EllipsizeMode.END)
            c = Gtk.TreeViewColumn(title, r, text=idx)
            c.set_sort_column_id(idx)
            c.set_clickable(True)
            c.set_resizable(True)
            c.set_min_width(80)
            c.set_fixed_width(width)
            view.append_column(c)
        view.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        for group in groups:
            for i, row in enumerate(group):
                when = local_when(row["date"] or "", row["date_sort"] or "")
                store.append(
                    [
                        i > 0,
                        row["path"],
                        row["folder"] or "",
                        when,
                        row["from_name"] or row["from_addr"] or "",
                        row["subject"] or "",
                    ]
                )
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)

        def selected_paths() -> list[str]:
            return [r[1] for r in store if r[0]]

        def clicked_path() -> str | None:
            model, it = view.get_selection().get_selected()
            if not it:
                return None
            return model[it][1]

        def select_all(_b):
            for r in store:
                r[0] = True

        def select_none(_b):
            for r in store:
                r[0] = False

        def select_extras(_b):
            seen: set[tuple] = set()
            for r in store:
                key = (r[2], r[3], r[4], r[5])
                if key in seen:
                    r[0] = True
                else:
                    seen.add(key)
                    r[0] = False

        def reveal_in_nautilus(rel: str | None) -> None:
            if not rel or not self.archive:
                return
            path = self.archive.root / rel
            if not path.exists():
                self._set_status(f"File not found: {rel}")
                return
            try:
                subprocess.Popen(["nautilus", "--select", str(path)])
            except FileNotFoundError:
                try:
                    subprocess.Popen(["xdg-open", str(path.parent)])
                except Exception as exc:
                    self._set_status(str(exc))

        def trash_rels(rels: list[str]) -> None:
            if not rels or not self.archive:
                return
            n = self.archive.trash_paths(rels)
            drop = set(rels)
            for r in list(store):
                if r[1] in drop:
                    store.remove(r.iter)
            self.refresh_folders()
            self.refresh_list()
            self._set_status(f"Moved {n} file(s) to Trash.")

        def delete_rels(rels: list[str]) -> None:
            if not rels or not self.archive:
                return
            ask = Gtk.MessageDialog(
                transient_for=dialog,
                flags=0,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.OK_CANCEL,
                text=f"Permanently delete {len(rels)} file(s)?",
            )
            ok = ask.run() == Gtk.ResponseType.OK
            ask.destroy()
            if not ok:
                return
            n = self.archive.delete_paths(rels)
            drop = set(rels)
            for r in list(store):
                if r[1] in drop:
                    store.remove(r.iter)
            self.refresh_folders()
            self.refresh_list()
            self._set_status(f"Deleted {n} file(s).")

        def do_trash(_b):
            trash_rels(selected_paths())

        def do_delete(_b):
            delete_rels(selected_paths())

        def export_selected(_b):
            rels = selected_paths()
            if not rels:
                return
            chooser = Gtk.FileChooserNative.new(
                "Export selected list", dialog, Gtk.FileChooserAction.SAVE, "Save", "Cancel"
            )
            chooser.set_current_name("oakkeep-duplicates.txt")
            if chooser.run() != Gtk.ResponseType.ACCEPT:
                chooser.destroy()
                return
            dest = chooser.get_filename()
            chooser.destroy()
            if not dest:
                return
            lines = []
            for r in store:
                if not r[0]:
                    continue
                lines.append(
                    f"{r[3]}\t{r[2]}\t{r[4]}\t{r[5]}\t{r[1]}"
                )
            header = "# date\tfolder\tfrom\tsubject\tfile\n"
            Path(dest).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
            self._set_status(f"Exported {len(rels)} line(s) to {dest}")

        def on_right_click(widget, event) -> bool:
            if event.button != 3:
                return False
            hit = view.get_path_at_pos(int(event.x), int(event.y))
            if hit:
                view.get_selection().select_path(hit[0])
            rel = clicked_path()
            menu = Gtk.Menu()

            def add(label, fn):
                item = Gtk.MenuItem(label=label)
                item.connect("activate", fn)
                menu.append(item)

            add("Show in Nautilus", lambda *_: reveal_in_nautilus(rel))
            add("Move this file to Trash", lambda *_: trash_rels([rel] if rel else []))
            add("Delete this file", lambda *_: delete_rels([rel] if rel else []))
            menu.show_all()
            menu.popup_at_pointer(event)
            return True

        view.connect("button-press-event", on_right_click)

        btns = Gtk.Box(spacing=8)
        for label, fn in (
            ("Select all", select_all),
            ("Select none", select_none),
            ("Select extras only", select_extras),
            ("Move to Trash", do_trash),
            ("Delete", do_delete),
            ("Export selected…", export_selected),
        ):
            b = Gtk.Button(label=label)
            b.connect("clicked", fn)
            btns.pack_start(b, False, False, 0)
        box.pack_start(btns, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def refresh_folders(self) -> None:
        self.folder_store.clear()
        if not self.archive:
            return
        total = self.archive.count()
        self.folder_store.append(["All accounts", "", total, 0])
        for rel, depth, mode in self.archive.disk_folder_rows():
            n = self.archive.count_prefix(rel)
            label = rel.split("/")[-1]
            indent = "    " * depth
            self.folder_store.append([indent + label, rel, n, mode])

    def refresh_list(self) -> None:
        if not self.archive:
            self.msg_store.clear()
            self.empty_label.set_text("No messages found")
            self.empty_label.show()
            return
        q = self.search.get_text()
        self._suspend_select = True
        self.msg_view.freeze_child_notify()
        sort_col, sort_dir = self.msg_store.get_sort_column_id()
        self.msg_store.set_sort_column_id(-2, Gtk.SortType.ASCENDING)
        self.msg_store.clear()
        rows = self.archive.list_folder(self.current_folder, q, prefix=self.current_prefix)
        for r in rows:
            clip = "📎" if r["has_attach"] else ""
            size_n = int(r["size"] or 0)
            size = f"{size_n / 1024:.0f} KB" if size_n else ""
            self.msg_store.append(
                [
                    r["path"],
                    local_when(r["date"] or "", r["date_sort"] or ""),
                    r["from_name"] or r["from_addr"] or "",
                    r["subject"] or "",
                    clip,
                    size,
                    r["date_sort"] or "",
                    int(r["has_attach"] or 0),
                    size_n,
                    int(r["special"] or 0),
                    "★" if int(r["special"] or 0) else "",
                ]
            )
        if sort_col is not None and sort_col >= 0:
            self.msg_store.set_sort_column_id(sort_col, sort_dir)
        else:
            self.msg_store.set_sort_column_id(6, Gtk.SortType.DESCENDING)
        self.msg_view.thaw_child_notify()
        self._suspend_select = False
        extra = f" · search “{q}”" if q else ""
        folder = self.current_folder or "all accounts"
        shown = len(rows)
        total = self.archive.count_folder(self.current_folder, prefix=self.current_prefix)
        more = f" of {total:,}" if total > shown else ""
        if shown == 0:
            self.empty_label.set_text("No messages found" if q.strip() else "No messages in this folder")
            self.empty_label.show()
            self.current_path = None
            self.preview.get_buffer().set_text("")
            self._set_status(f"No messages found{extra} · {self.archive.count():,} in archive")
        else:
            self.empty_label.hide()
            self._set_status(f"{shown:,}{more} shown in {folder}{extra} · {self.archive.count():,} in archive")

    def on_folder(self, selection) -> None:
        model, it = selection.get_selected()
        if not it:
            return
        row = model[it]
        self.current_folder = row[1] or None
        mode = int(row[3]) if row[3] is not None else 1
        self.current_prefix = mode in (0, 1) and bool(self.current_folder)
        if mode == 0:
            self.current_folder = None
            self.current_prefix = False
        self.refresh_list()

    def on_select(self, selection) -> None:
        if self._suspend_select:
            return
        model, paths = selection.get_selected_rows()
        if not paths:
            self.current_path = None
            self.preview.get_buffer().set_text("")
            return
        self.show_message(model[paths[-1]][0])

    def on_row_activated(self, _view, _path, _col) -> None:
        self.open_external()

    def show_message(self, rel: str) -> None:
        if not self.archive:
            return
        self.current_path = rel
        try:
            data = self.archive.read_preview(rel)
        except OSError as exc:
            self.preview.get_buffer().set_text(str(exc))
            return
        msg = parse_message(data)
        date = best_date(msg)
        head = [
            f"From: {msg.get('from') or ''}",
            f"To: {msg.get('to') or ''}",
            f"Date: {local_when(date)}" + (f"  ({date})" if date else ""),
            f"Subject: {msg.get('subject') or ''}",
            f"File: {rel}",
        ]
        from .mail import attachment_names

        names = attachment_names(msg)
        if names:
            head.append("Attachments: " + ", ".join(names))
        text = body_text(msg, 200_000)
        self.preview.get_buffer().set_text("\n".join(head) + "\n\n" + (text or "(no plain text)"))

    def on_list_click(self, view, event) -> bool:
        if event.button != 3:
            return False
        hit = view.get_path_at_pos(int(event.x), int(event.y))
        selection = view.get_selection()
        if hit and not selection.path_is_selected(hit[0]):
            selection.unselect_all()
            selection.select_path(hit[0])
        rels = self.selected_paths()
        menu = Gtk.Menu()
        for label, fn in (
            ("Preview", self.preview_selected),
            ("Open .eml with default app", self.open_external),
            ("Show in file manager", self.reveal_selected),
            ("Save as…", self.save_as),
            ("Select all", lambda *_: self.msg_view.get_selection().select_all()),
            ("Mark as special", self.mark_selected),
            ("Unmark as special", self.unmark_selected),
            ("Move to Trash", self.trash_selected),
            ("Delete", self.delete_selected),
        ):
            item = Gtk.MenuItem(label=label)
            if label in ("Mark as special", "Unmark as special") and not rels:
                item.set_sensitive(False)
            item.connect("activate", fn)
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def selected_paths(self) -> list[str]:
        model, rows = self.msg_view.get_selection().get_selected_rows()
        return [model[p][0] for p in rows]

    def mark_selected(self, *_a) -> None:
        self._set_selected_special(True)

    def unmark_selected(self, *_a) -> None:
        self._set_selected_special(False)

    def _set_selected_special(self, marked: bool) -> None:
        if not self.archive:
            return
        rels = self.selected_paths()
        if not rels:
            return
        self.archive.set_special(rels, marked)
        self.refresh_list()
        self._reselect(rels)
        if marked:
            self._set_status(f"Marked {len(rels)} message(s) as special.")
        else:
            self._set_status(f"Unmarked {len(rels)} message(s).")

    def _reselect(self, rels: list[str]) -> None:
        want = set(rels)
        sel = self.msg_view.get_selection()
        for row in self.msg_store:
            if row[0] in want:
                sel.select_iter(row.iter)

    def preview_selected(self, *_a) -> None:
        if self.current_path:
            self.show_message(self.current_path)

    def open_external(self, *_a) -> None:
        if not self.archive or not self.current_path:
            return
        path = self.archive.root / self.current_path
        subprocess.Popen(["xdg-open", str(path)])

    def save_as(self, *_a) -> None:
        if not self.archive or not self.current_path:
            return
        src = self.archive.root / self.current_path
        dialog = Gtk.FileChooserNative.new("Save as", self, Gtk.FileChooserAction.SAVE, "Save", "Cancel")
        dialog.set_current_name(Path(self.current_path).name)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            dest = dialog.get_filename()
            if dest:
                Path(dest).write_bytes(src.read_bytes())
        dialog.destroy()

    def reveal_selected(self, *_a) -> None:
        if not self.archive:
            return
        rel = self.current_path or (self.selected_paths()[0] if self.selected_paths() else None)
        if not rel:
            return
        path = self.archive.root / rel
        folder = path.parent if path.exists() else self.archive.root
        try:
            subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            self._set_status(str(exc))

    def trash_selected(self, *_a) -> None:
        if not self.archive:
            return
        rels = self.selected_paths()
        if not rels:
            return
        dlg = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Move {len(rels)} message(s) to Trash?",
        )
        if dlg.run() == Gtk.ResponseType.OK:
            n = self.archive.trash_paths(rels)
            self.refresh_folders()
            self.refresh_list()
            self._set_status(f"Moved {n} messages to Trash.")
        dlg.destroy()

    def delete_selected(self, *_a) -> None:
        if not self.archive:
            return
        rels = self.selected_paths()
        if not rels:
            return
        dlg = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Permanently delete {len(rels)} message(s) from the archive folder?",
        )
        if dlg.run() == Gtk.ResponseType.OK:
            n = self.archive.delete_paths(rels)
            self.refresh_folders()
            self.refresh_list()
            self._set_status(f"Deleted {n} messages.")
        dlg.destroy()


def main() -> None:
    win = OakkeepWindow()
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
