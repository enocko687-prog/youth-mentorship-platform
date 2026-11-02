import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "youth_mentorship.db"


def get_connection(database_path=DATABASE_PATH):
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_columns(connection, table, columns):
    existing_columns = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
    }
    for name, definition in columns.items():
        if name not in existing_columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def initialize_database(database_path=DATABASE_PATH):
    connection = get_connection(database_path)
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'mentee'
                    CHECK(role IN ('mentee', 'mentor', 'admin')),
                account_status TEXT NOT NULL DEFAULT 'active'
                    CHECK(account_status IN ('pending', 'active', 'suspended')),
                email_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS mentor_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                bio TEXT,
                expertise TEXT,
                qualifications TEXT,
                experience TEXT,
                availability TEXT,
                vetting_status TEXT NOT NULL DEFAULT 'pending',
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS mentee_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                age INTEGER,
                education_level TEXT,
                interests TEXT,
                personal_goals TEXT,
                support_needs TEXT,
                location TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS mentor_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mentor_id INTEGER NOT NULL,
                mentee_id INTEGER NOT NULL,
                match_status TEXT NOT NULL DEFAULT 'pending',
                matched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(mentor_id) REFERENCES users(id),
                FOREIGN KEY(mentee_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mentee_id INTEGER NOT NULL,
                mentor_id INTEGER,
                title TEXT NOT NULL,
                description TEXT,
                target_date TEXT,
                status TEXT NOT NULL DEFAULT 'not_started',
                progress INTEGER NOT NULL DEFAULT 0
                    CHECK(progress BETWEEN 0 AND 100),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(mentee_id) REFERENCES users(id),
                FOREIGN KEY(mentor_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS action_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mentee_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                action_date TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(mentee_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mentor_id INTEGER NOT NULL,
                mentee_id INTEGER NOT NULL,
                session_date TEXT NOT NULL,
                session_time TEXT,
                meeting_type TEXT NOT NULL DEFAULT 'online',
                status TEXT NOT NULL DEFAULT 'pending',
                notes TEXT,
                FOREIGN KEY(mentor_id) REFERENCES users(id),
                FOREIGN KEY(mentee_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                category TEXT,
                resource_type TEXT,
                resource_url TEXT,
                uploaded_by INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(uploaded_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS lesson_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mentee_id INTEGER NOT NULL,
                resource_id INTEGER NOT NULL,
                viewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(mentee_id) REFERENCES users(id),
                FOREIGN KEY(resource_id) REFERENCES resources(id)
            );

            CREATE TABLE IF NOT EXISTS interaction_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mentor_id INTEGER NOT NULL,
                mentee_id INTEGER NOT NULL,
                interaction_type TEXT,
                notes TEXT,
                interaction_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(mentor_id) REFERENCES users(id),
                FOREIGN KEY(mentee_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS safeguarding_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL,
                report_category TEXT NOT NULL,
                description TEXT NOT NULL,
                urgency TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'open',
                assigned_to INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(reporter_id) REFERENCES users(id),
                FOREIGN KEY(assigned_to) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject TEXT,
                message TEXT NOT NULL,
                rating INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 5),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS contact_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                request_type TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'responded')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS work_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER NOT NULL,
                mentee_id INTEGER NOT NULL,
                mentor_id INTEGER NOT NULL,
                work_notes TEXT NOT NULL DEFAULT '',
                work_url TEXT,
                file_name TEXT,
                stored_file_name TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'reviewed')),
                mentor_feedback TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT,
                FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE,
                FOREIGN KEY(mentee_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(mentor_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                activity TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS password_resets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
            CREATE INDEX IF NOT EXISTS idx_goals_mentee ON goals(mentee_id);
            CREATE INDEX IF NOT EXISTS idx_bookings_mentor ON bookings(mentor_id);
            CREATE INDEX IF NOT EXISTS idx_bookings_mentee ON bookings(mentee_id);
            CREATE INDEX IF NOT EXISTS idx_matches_mentee ON mentor_matches(mentee_id);
            CREATE INDEX IF NOT EXISTS idx_reports_status ON safeguarding_reports(status);
            CREATE INDEX IF NOT EXISTS idx_contact_requests_status
                ON contact_requests(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_work_submissions_mentee
                ON work_submissions(mentee_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_work_submissions_mentor
                ON work_submissions(mentor_id, status, created_at);
            """)
        _ensure_columns(connection, "mentor_profiles", {
            "expertise": "TEXT",
            "biography": "TEXT",
            "qualifications": "TEXT",
            "experience": "TEXT",
            "availability": "TEXT",
            "vetting_status": "TEXT NOT NULL DEFAULT 'pending'",
        })
        _ensure_columns(connection, "mentor_matches", {
            "match_status": "TEXT NOT NULL DEFAULT 'pending'",
            "matched_at": "TEXT",
        })
        _ensure_columns(connection, "goals", {
            "mentor_id": "INTEGER",
            "description": "TEXT",
            "target_date": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'not_started'",
            "progress": "INTEGER NOT NULL DEFAULT 0",
        })
        _ensure_columns(connection, "action_plans", {
            "title": "TEXT NOT NULL DEFAULT ''",
            "description": "TEXT",
            "action_date": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "completed": "INTEGER NOT NULL DEFAULT 0",
        })
        _ensure_columns(connection, "bookings", {
            "session_time": "TEXT",
            "meeting_type": "TEXT NOT NULL DEFAULT 'online'",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "notes": "TEXT",
        })
        _ensure_columns(connection, "resources", {
            "category": "TEXT",
            "resource_type": "TEXT",
            "resource_url": "TEXT",
            "uploaded_by": "INTEGER",
        })
        _ensure_columns(connection, "safeguarding_reports", {
            "reporter_id": "INTEGER",
            "report_category": "TEXT NOT NULL DEFAULT 'support'",
            "description": "TEXT NOT NULL DEFAULT ''",
            "urgency": "TEXT NOT NULL DEFAULT 'normal'",
            "status": "TEXT NOT NULL DEFAULT 'open'",
            "assigned_to": "INTEGER",
        })
        _ensure_columns(connection, "feedback", {
            "user_id": "INTEGER",
            "subject": "TEXT",
            "message": "TEXT NOT NULL DEFAULT ''",
            "rating": "INTEGER",
        })
        _ensure_columns(connection, "audit_logs", {
            "user_id": "INTEGER",
            "activity": "TEXT NOT NULL DEFAULT 'legacy activity'",
            "details": "TEXT",
        })

        action_plan_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(action_plans)")
        }
        if "task" in action_plan_columns:
            connection.execute(
                "UPDATE action_plans SET title = task "
                "WHERE title = '' AND task IS NOT NULL"
            )

        legacy_copy_columns = (
            ("mentor_matches", "assigned_at", "matched_at"),
            ("safeguarding_reports", "mentee_id", "reporter_id"),
            ("safeguarding_reports", "report_text", "description"),
            ("feedback", "mentee_id", "user_id"),
            ("audit_logs", "action", "activity"),
        )
        for table, source, target in legacy_copy_columns:
            columns = {
                row["name"] for row in connection.execute(
                    f"PRAGMA table_info({table})"
                )
            }
            if source in columns:
                empty_target = f"{target} IS NULL OR {target} = ''"
                if table == "audit_logs" and target == "activity":
                    empty_target += " OR activity = 'legacy activity'"
                connection.execute(
                    f"UPDATE {table} SET {target} = {source} "
                    f"WHERE {empty_target}"
                )
        connection.commit()
    finally:
        connection.close()
