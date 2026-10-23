"""Read recorded task metadata without importing the retired extraction worker."""
import sqlite3

from .records import sessions


def task_list(root):
    tasks = []
    for manifest in sessions(root):
        try:
            with sqlite3.connect((manifest.parent / 'index.sqlite3').resolve().as_uri() + '?mode=ro',
                                 uri=True, timeout=1) as db:
                db.row_factory = sqlite3.Row
                tasks.extend(dict(row, session_id=manifest.parent.name) for row in db.execute(
                    'SELECT id,goal,status,created_at,updated_at,source FROM episodes'))
        except (OSError, sqlite3.Error):
            continue
    return sorted(tasks, key=lambda task: task['created_at'], reverse=True)
