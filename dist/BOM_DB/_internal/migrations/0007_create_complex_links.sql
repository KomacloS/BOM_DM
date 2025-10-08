CREATE TABLE IF NOT EXISTS complex_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  part_id INTEGER NOT NULL,
  ce_db_uri TEXT,
  ce_complex_id TEXT NOT NULL,
  aliases TEXT,
  pin_map TEXT,
  macro_ids TEXT,
  source_hash TEXT,
  synced_at TEXT,
  created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
  updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_complex_links_part ON complex_links(part_id);
CREATE INDEX IF NOT EXISTS ix_complex_links_ce ON complex_links(ce_complex_id);
