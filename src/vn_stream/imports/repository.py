"""Import checkpoint persistence, including registration in the publication transaction."""

import json
from dataclasses import asdict

from vn_stream.i18n import durable_messages


class ImportRepository:
    def __init__(self, database):
        self.database = database

    def update(self, job_id, *, release_upload=False, **updates):
        with self.database.transaction() as db:
            row = db.execute("SELECT record FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
            record = json.loads(row[0])
            record.update(updates)
            record = durable_messages(record)
            db.execute(
                "UPDATE import_jobs SET record = ?, state = ?, "
                "upload_id = CASE WHEN ? THEN NULL ELSE upload_id END, "
                "directory_upload_id = CASE WHEN ? THEN NULL ELSE directory_upload_id END "
                "WHERE id = ?",
                (
                    json.dumps(record, ensure_ascii=False),
                    record["state"],
                    release_upload,
                    release_upload,
                    job_id,
                ),
            )

    def registered(self, connection, task_id, project):
        row = connection.execute("SELECT record FROM import_jobs WHERE id=?", (task_id,)).fetchone()
        record = json.loads(row[0])
        record.update(result=asdict(project), phase="cleaning", registered=True)
        connection.execute(
            "UPDATE import_jobs SET record=? WHERE id=?", (json.dumps(record), task_id)
        )
