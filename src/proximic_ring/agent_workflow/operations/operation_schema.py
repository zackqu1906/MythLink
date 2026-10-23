"""通用操作增量表。只新增表；旧 pairs、assignments、feedback 原样保留。"""


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS operation_heads(
        operation_id TEXT PRIMARY KEY, active_revision TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS operation_merges(
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, revision_id TEXT NOT NULL,
        detail TEXT NOT NULL, at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS operation_displays(
        revision_id TEXT PRIMARY KEY, content TEXT NOT NULL,
        generation TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS operation_display_history(
        id TEXT PRIMARY KEY, revision_id TEXT NOT NULL, content TEXT NOT NULL,
        generation TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS operation_revisions(
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, parent_id TEXT,
        flow TEXT NOT NULL, status TEXT NOT NULL, change_kind TEXT NOT NULL,
        reason TEXT NOT NULL, sources TEXT NOT NULL, generation TEXT NOT NULL,
        created_at TEXT NOT NULL, validation TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS operation_memberships(
        sample_id TEXT NOT NULL, version_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        revision_id TEXT NOT NULL, admission_id TEXT NOT NULL, at TEXT NOT NULL,
        PRIMARY KEY(sample_id, version_id));
      CREATE TABLE IF NOT EXISTS operation_membership_events(
        sample_id TEXT NOT NULL, version_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        revision_id TEXT NOT NULL, admission_id TEXT NOT NULL, at TEXT NOT NULL,
        PRIMARY KEY(sample_id, version_id, admission_id));
      CREATE TABLE IF NOT EXISTS operation_admissions(
        id TEXT PRIMARY KEY, sample_id TEXT NOT NULL, version_id TEXT NOT NULL,
        status TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS operation_jobs(
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_id TEXT NOT NULL,
        status TEXT NOT NULL, input TEXT NOT NULL, result TEXT NOT NULL,
        generation TEXT NOT NULL, error TEXT NOT NULL, at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS operation_updates(
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, base_revision TEXT NOT NULL,
        source_version TEXT NOT NULL, admission_id TEXT NOT NULL,
        novelty TEXT NOT NULL, status TEXT NOT NULL, revision_id TEXT, at TEXT NOT NULL);
    ''')
