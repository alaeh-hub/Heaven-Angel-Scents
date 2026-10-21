"""In-app backup: snapshot the whole database into one emailable .zip.

Format (decided for "restorable AND readable"):

  heaven-angel-backup-<stamp>.zip
    database.sql        full restore script: DROP/CREATE every table, then
                        batched INSERTs. Restore with
                        `mysql -u <user> -p <db> < database.sql`.
                        Written in plain Python (no mysqldump binary needed
                        inside the app container).
    csv/<table>.csv     the same rows, one CSV per table, openable in Excel
                        — for looking something up without restoring.
    uploads/...         product photos from static/uploads (they are not in
                        the database).
    MANIFEST.txt        when it was taken, row counts, how to restore.

Mail servers cap attachment size, so if the full set is too big the
optional parts are dropped in order (uploads, then CSVs); if even
database.sql alone won't fit, TooLargeError is raised and the caller points
the user at scripts/backup_db.sh (see BACKUP.md).

The snapshot is taken inside one REPEATABLE READ transaction, so every
table is read as of the same instant without locking out the live app.
"""
import csv
import datetime
import decimal
import io
import os
import re
import zipfile

from flask import current_app

from db import get_db
from utils import business_now


class TooLargeError(Exception):
    pass


_BATCH_ROWS = 200


def _sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, decimal.Decimal)):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex() if value else "''"
    if isinstance(value, datetime.datetime):
        return "'" + value.isoformat(sep=" ") + "'"
    if isinstance(value, (datetime.date, datetime.time)):
        return "'" + value.isoformat() + "'"
    if isinstance(value, datetime.timedelta):
        total = int(value.total_seconds())
        sign = "-" if total < 0 else ""
        total = abs(total)
        return "'%s%02d:%02d:%02d'" % (sign, total // 3600, total % 3600 // 60, total % 60)
    if isinstance(value, (set, frozenset)):
        value = ",".join(sorted(value))
    text = str(value)
    text = (text.replace("\\", "\\\\").replace("'", "\\'").replace("\0", "\\0")
            .replace("\n", "\\n").replace("\r", "\\r").replace("\x1a", "\\Z"))
    return "'" + text + "'"


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (set, frozenset)):
        return ",".join(sorted(value))
    return value


def _dump_database():
    """Returns (sql_bytes, {table: csv_bytes}, {table: row_count})."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
    try:
        cur.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
        tables = sorted(row[0] for row in cur.fetchall())

        sql = io.StringIO()
        sql.write("-- Heaven & Angel Scents database backup\n")
        sql.write("-- Taken %s (business time)\n\n" % business_now().strftime("%Y-%m-%d %H:%M:%S"))
        sql.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS = 0;\nSET UNIQUE_CHECKS = 0;\n"
                  "SET SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO';\n\n")
        csvs, counts = {}, {}

        for table in tables:
            cur.execute("SHOW CREATE TABLE `%s`" % table.replace("`", "``"))
            create_sql = cur.fetchone()[1]
            sql.write("-- ----- %s -----\nDROP TABLE IF EXISTS `%s`;\n%s;\n\n" % (table, table, create_sql))

            cur.execute("SELECT * FROM `%s`" % table.replace("`", "``"))
            columns = [d[0] for d in cur.description]
            col_list = ", ".join("`%s`" % c.replace("`", "``") for c in columns)

            buf = io.StringIO(newline="")
            writer = csv.writer(buf)
            writer.writerow(columns)

            n = 0
            while True:
                rows = cur.fetchmany(_BATCH_ROWS)
                if not rows:
                    break
                values = ",\n  ".join(
                    "(" + ", ".join(_sql_literal(v) for v in row) + ")" for row in rows)
                sql.write("INSERT INTO `%s` (%s) VALUES\n  %s;\n" % (table, col_list, values))
                for row in rows:
                    writer.writerow([_csv_cell(v) for v in row])
                n += len(rows)
            if n:
                sql.write("\n")
            counts[table] = n
            # utf-8-sig so Excel opens accented/peso text correctly.
            csvs[table] = buf.getvalue().encode("utf-8-sig")

        sql.write("SET FOREIGN_KEY_CHECKS = 1;\nSET UNIQUE_CHECKS = 1;\n")
        return sql.getvalue().encode("utf-8"), csvs, counts
    finally:
        try:
            conn.rollback()  # end the read-only snapshot transaction
        finally:
            cur.close()


def _uploads():
    """[(archive_name, absolute_path)] for product photos, if any."""
    root = os.path.join(current_app.static_folder or "", "uploads")
    found = []
    if os.path.isdir(root):
        for folder, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(folder, name)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                found.append(("uploads/" + rel, path))
    return found


def _manifest(stamp, counts, included):
    lines = [
        "Heaven & Angel Scents - backup",
        "Taken: %s (business time)" % stamp,
        "",
        "Contents:",
        "  database.sql   full restore script (schema + data)",
    ]
    if "csv" in included:
        lines.append("  csv/           one CSV per table, readable in Excel")
    if "uploads" in included:
        lines.append("  uploads/       product photos (not stored in the database)")
    else:
        lines.append("  (product photos were NOT included - see BACKUP.md for scripts/backup_db.sh)")
    lines += [
        "",
        "To restore into an empty (or existing - tables are dropped and recreated) database:",
        "  mysql -u <user> -p <database> < database.sql",
    ]
    if "uploads" in included:
        lines.append("Then copy uploads/ back into static/uploads/.")
    lines += ["", "Row counts:"]
    width = max((len(t) for t in counts), default=10)
    lines += ["  %s  %d" % (t.ljust(width), n) for t, n in sorted(counts.items())]
    lines += ["", "Total rows: %d" % sum(counts.values()), ""]
    return "\n".join(lines).encode("utf-8")


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in entries:
            if isinstance(data, str):  # a file path on disk
                zf.write(data, name)
            else:
                zf.writestr(name, data)
    return buf.getvalue()


def build_backup_zip(max_bytes):
    """Returns (zip_bytes, filename, summary) or raises TooLargeError."""
    sql_bytes, csvs, counts = _dump_database()
    now = business_now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    filename = "heaven-angel-backup-%s.zip" % now.strftime("%Y%m%d-%H%M")

    csv_entries = [("csv/%s.csv" % t, data) for t, data in csvs.items()]
    upload_entries = _uploads()

    # Richest first; drop optional parts until it fits in an email.
    options = [("csv", "uploads")] if upload_entries else []
    for included in options + [("csv",), ()]:
        entries = [("MANIFEST.txt", _manifest(stamp, counts, included)), ("database.sql", sql_bytes)]
        if "csv" in included:
            entries += csv_entries
        if "uploads" in included:
            entries += upload_entries
        data = _zip(entries)
        if len(data) <= max_bytes:
            summary = {"tables": len(counts), "rows": sum(counts.values()),
                       "csv": "csv" in included, "uploads": "uploads" in included,
                       "bytes": len(data)}
            return data, filename, summary
    raise TooLargeError(
        "The database alone is bigger than the email attachment limit "
        "(%.0f MB). Use scripts/backup_db.sh instead (see BACKUP.md)." % (max_bytes / 1024 / 1024))


# ---------------------------------------------------------------------------
# Restore (Settings -> Backup -> Restore from a backup)
# ---------------------------------------------------------------------------
class RestoreError(Exception):
    """A problem with the uploaded file or the restore itself, with a
    message that is safe and useful to show the admin."""


_MAX_SQL_BYTES = 600 * 1024 * 1024        # decompressed database.sql
_MAX_UPLOAD_FILES = 5000                  # photos
_HEADER = "-- Heaven & Angel Scents database backup"

# Only the statements backup.py itself writes are ever executed — a
# hand-edited or hostile file cannot run anything else (no DROP DATABASE,
# GRANT, UPDATE ..., stored routines, multi-statements, ...).
_ALLOWED = re.compile(
    r"^(?:SET\s+NAMES\s+utf8mb4"
    r"|SET\s+(?:FOREIGN_KEY_CHECKS|UNIQUE_CHECKS)\s*=\s*[01]"
    r"|SET\s+SQL_MODE\s*=\s*'NO_AUTO_VALUE_ON_ZERO'"
    r"|DROP\s+TABLE\s+IF\s+EXISTS\s+`[^`]+`"
    r"|CREATE\s+TABLE\s+`[^`]+`\s*\("
    r"|INSERT\s+INTO\s+`[^`]+`\s*\()",
    re.IGNORECASE,
)

# Tokens that may contain a ';' we must not split on, plus the ';' itself.
_TOKEN = re.compile(r"'(?:[^'\\]|\\.|\'\')*'|`[^`]*`|--[^\n]*(?:\n|$)|/\*.*?\*/|;", re.DOTALL)


def split_sql(text):
    """Split a dump into statements without being fooled by ';' inside
    quoted strings or comments."""
    statements, start = [], 0
    for m in _TOKEN.finditer(text):
        tok = m.group()
        if tok == ";":
            stmt = text[start:m.start()].strip()
            if stmt:
                statements.append(stmt)
            start = m.end()
        elif tok.startswith("--") or tok.startswith("/*"):
            if not text[start:m.start()].strip():   # comment between statements
                start = m.end()
    tail = text[start:].strip()
    if tail:
        statements.append(tail)
    return statements


def read_backup_sql(path):
    """Return (sql_text, zipfile_or_None) from an uploaded backup .zip or a bare .sql."""
    if zipfile.is_zipfile(path):
        zf = zipfile.ZipFile(path)
        try:
            info = zf.getinfo("database.sql")
        except KeyError:
            zf.close()
            raise RestoreError("That zip has no database.sql in it - it doesn't look like a backup made here.")
        if info.file_size > _MAX_SQL_BYTES:
            zf.close()
            raise RestoreError("That backup is larger than the restore limit.")
        return zf.read("database.sql").decode("utf-8"), zf
    with open(path, "rb") as fh:
        head = fh.read(_MAX_SQL_BYTES + 1)
    if len(head) > _MAX_SQL_BYTES:
        raise RestoreError("That file is larger than the restore limit.")
    try:
        return head.decode("utf-8"), None
    except UnicodeDecodeError:
        raise RestoreError("That file isn't a backup (it isn't a zip or a UTF-8 SQL file).")


def prepare_restore(path):
    """Validate an uploaded backup completely *before* anything is touched.
    Returns (statements, tables, zip_or_None)."""
    sql, zf = read_backup_sql(path)
    try:
        if not sql.lstrip("\ufeff \r\n").startswith(_HEADER):
            raise RestoreError("That file isn't a Heaven & Angel Scents backup (header not found).")
        statements = split_sql(sql)
        tables = []
        for stmt in statements:
            if not _ALLOWED.match(stmt):
                raise RestoreError("The backup contains a statement that is not allowed: "
                                   + stmt[:60].replace("\n", " ") + "...")
            m = re.match(r"CREATE\s+TABLE\s+`([^`]+)`", stmt, re.IGNORECASE)
            if m:
                tables.append(m.group(1))
        if "users" not in tables or "branches" not in tables:
            raise RestoreError("That backup is missing core tables (users / branches), so it can't be restored.")
        return statements, tables, zf
    except Exception:
        if zf:
            zf.close()
        raise


# Logs that must survive a restore: the audit trail (which also records the
# restore itself, and the backup/restore history shown in Settings) and the
# sign-in security log (failed-login lockouts must not be rewindable by
# restoring an old file), and the backup schedule. Their current contents
# are kept as they are.
PRESERVE_ON_RESTORE = ("admin_actions", "login_activity", "app_settings")
_TABLE_OF = re.compile(r"`([^`]+)`")


def _is_preserved(stmt):
    if stmt.upper().startswith("SET"):
        return False
    m = _TABLE_OF.search(stmt)
    return bool(m) and m.group(1) in PRESERVE_ON_RESTORE


def run_restore(statements):
    """Execute pre-validated statements on one connection."""
    conn = get_db()
    cur = conn.cursor()
    try:
        done = 0
        for stmt in statements:
            if _is_preserved(stmt):
                continue
            cur.execute(stmt)
            done += 1
            if done % 50 == 0:
                conn.commit()
        conn.commit()
    finally:
        try:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            cur.execute("SET UNIQUE_CHECKS = 1")
        finally:
            cur.close()


def restore_uploads(zf):
    """Copy uploads/ from the zip into static/uploads, refusing any path
    that would land outside it. Returns the number of files written."""
    if zf is None:
        return 0
    root = os.path.realpath(os.path.join(current_app.static_folder or "", "uploads"))
    names = [n for n in zf.namelist() if n.startswith("uploads/") and not n.endswith("/")]
    if len(names) > _MAX_UPLOAD_FILES:
        raise RestoreError("The backup holds an unreasonable number of photos.")
    written = 0
    for name in names:
        target = os.path.realpath(os.path.join(root, name[len("uploads/"):]))
        if not target.startswith(root + os.sep):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(name) as src, open(target, "wb") as dst:
            dst.write(src.read())
        written += 1
    return written


def save_safety_backup():
    """Write a full backup of the CURRENT data to disk before a restore
    overwrites it. Returns the file path."""
    data, filename, _summary = build_backup_zip(2 ** 40)
    folder = current_app.config.get("BACKUP_DIR") or os.path.join(current_app.root_path, "backups")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "before-restore-" + filename)
    with open(path, "wb") as fh:
        fh.write(data)
    return path
