import sqlite3, os, sys

db = r"Z:\Project Management\Seica\other\BOM_DB_28.09.25\data\app.db"
print("Opening:", db)
conn = sqlite3.connect(db)
cur = conn.cursor()

def has_table(name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None

def column_names(table):
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]

# assembly.test_mode
if has_table("assembly"):
    cols = column_names("assembly")
    if "test_mode" not in cols:
        cur.execute("ALTER TABLE assembly ADD COLUMN test_mode TEXT DEFAULT 'unpowered'")
        print("Added assembly.test_mode")
    else:
        print("assembly.test_mode already exists")
    cur.execute("UPDATE assembly SET test_mode = 'unpowered' WHERE test_mode = 'non_powered'")
    conn.commit()
else:
    print("assembly table not found; skipping")

# testmacro
cur.execute("CREATE TABLE IF NOT EXISTS testmacro (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, glb_path TEXT, notes TEXT)")
# pythontest
cur.execute("CREATE TABLE IF NOT EXISTS pythontest (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, file_path TEXT, notes TEXT)")
# part_test_map
cur.execute("CREATE TABLE IF NOT EXISTS part_test_map (part_id INTEGER NOT NULL, power_mode TEXT NOT NULL DEFAULT 'unpowered', profile TEXT NOT NULL, test_macro_id INTEGER, python_test_id INTEGER, detail TEXT, PRIMARY KEY (part_id, power_mode, profile))")
cur.execute("CREATE INDEX IF NOT EXISTS ix_part_test_map_part_profile_mode ON part_test_map(part_id, profile, power_mode)")
cur.execute("CREATE INDEX IF NOT EXISTS ix_part_test_map_part_mode ON part_test_map(part_id, power_mode)")
# bom_item_test_override
cur.execute("CREATE TABLE IF NOT EXISTS bom_item_test_override (bom_item_id INTEGER NOT NULL, power_mode TEXT NOT NULL DEFAULT 'unpowered', test_macro_id INTEGER, python_test_id INTEGER, detail TEXT, PRIMARY KEY (bom_item_id, power_mode))")
cur.execute("CREATE INDEX IF NOT EXISTS ix_bom_item_test_override_item_mode ON bom_item_test_override(bom_item_id, power_mode)")

conn.commit()
conn.close()
print("Done.")
