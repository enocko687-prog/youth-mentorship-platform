import re
import sqlite3
import tempfile
import unittest
from io import BytesIO
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch
from urllib.error import URLError

from server import _get_secret_key, create_app
from werkzeug.security import check_password_hash, generate_password_hash


class MentorshipAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE": str(database_path),
                "UPLOAD_FOLDER": str(Path(self.temp_dir.name) / "uploads"),
                "ACCESS_PASSWORD_HASH_FILE": str(
                    Path(self.temp_dir.name) / "access_password_hash"
                ),
            }
        )
        self.database_path = database_path
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf_token(self, path, client=None):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def register(self, email, role="mentee", client=None):
        client = client or self.client
        token = self.csrf_token("/register", client)
        return client.post(
            "/register",
            data={
                "csrf_token": token,
                "full_name": "Test Participant",
                "email": email,
                "role": role,
                "password": "secure-passphrase",
                "password_confirmation": "secure-passphrase",
            },
            follow_redirects=True,
        )

    def post_as_signed_in_user(self, path, data=None, client=None):
        client = client or self.client
        payload = dict(data or {})
        if path.startswith("/mentor"):
            csrf_path = "/mentor"
        elif path.startswith("/admin"):
            csrf_path = "/admin"
        else:
            csrf_path = "/dashboard"
        payload["csrf_token"] = self.csrf_token(csrf_path, client)
        return client.post(path, data=payload)

    def create_user(self, full_name, email, role):
        connection = sqlite3.connect(self.database_path)
        try:
            cursor = connection.execute(
                "INSERT INTO users "
                "(full_name, email, password_hash, role, account_status) "
                "VALUES (?, ?, ?, ?, 'active')",
                (
                    full_name,
                    email,
                    generate_password_hash("secure-passphrase"),
                    role,
                ),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def create_matched_mentee_with_goal(self, client, mentee_email):
        self.register(mentee_email, client=client)
        mentor_id = self.create_user(
            "Matched Mentor", "matched-mentor@example.com", "mentor"
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "INSERT INTO mentor_profiles (user_id, expertise, vetting_status) "
                "VALUES (?, 'Career guidance', 'approved')",
                (mentor_id,),
            )
            mentee_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", (mentee_email,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO mentor_matches (mentor_id, mentee_id, match_status) "
                "VALUES (?, ?, 'accepted')",
                (mentor_id, mentee_id),
            )
            connection.commit()
        finally:
            connection.close()
        self.post_as_signed_in_user(
            "/goals",
            {"title": "Build a portfolio", "description": "Complete a sample project"},
            client=client,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            goal_id = connection.execute(
                "SELECT id FROM goals WHERE mentee_id = ?", (mentee_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        return mentee_id, mentor_id, goal_id

    def test_home_and_assets_render(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mentorship, with a plan.", response.data)
        self.assertIn(b"Learning resources", response.data)
        self.assertIn(b"0715552003", response.data)
        self.assertIn(b"tel:0715552003", response.data)
        self.assertIn(b"sms:0715552003", response.data)
        self.assertIn(b"https://wa.me/254715552003", response.data)
        self.assertIn(b"https://www.facebook.com/profile.php?id=100083323632489", response.data)
        self.assertIn(b"https://www.youtube.com/@enockochiengu9r", response.data)
        self.assertIn(b"https://www.linkedin.com/feed/", response.data)
        self.assertIn(b"https://github.com/enocko687-prog/DI-BOOTCAMP-1", response.data)
        self.assertIn(b"Send your request", response.data)
        self.assertNotIn(b"hero-art", response.data)
        self.assertIn(b'class="menu-toggle" type="button"', response.data)
        self.assertIn(b'aria-controls="primary-nav"', response.data)
        asset_response = self.client.get("/static/ymp-mark.svg")
        self.assertEqual(asset_response.status_code, 200)
        asset_response.close()

    def test_public_contact_request_is_saved_and_admin_can_follow_up(self):
        token = self.csrf_token("/")
        response = self.client.post(
            "/contact",
            data={
                "csrf_token": token,
                "full_name": "Amina Njeri",
                "email": "AMINA@example.com",
                "phone": "+254 700 123 456",
                "request_type": "mentorship",
                "message": "I would like to find a mentor.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your request has been sent.", response.data)

        connection = sqlite3.connect(self.database_path)
        try:
            contact_request_id, email, status = connection.execute(
                "SELECT id, email, status FROM contact_requests"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual((email, status), ("amina@example.com", "pending"))

        admin_result = self.app.test_cli_runner().invoke(
            args=["create-admin"],
            input=(
                "Programme Admin\nadmin@example.com\n"
                "secure-admin-passphrase\nsecure-admin-passphrase\n"
            ),
        )
        self.assertEqual(admin_result.exit_code, 0, admin_result.output)
        token = self.csrf_token("/login")
        login_response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "admin@example.com",
                "password": "secure-admin-passphrase",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Amina Njeri", login_response.data)
        self.assertIn(b"I would like to find a mentor.", login_response.data)
        self.assertIn(b"mailto:amina@example.com", login_response.data)
        self.assertIn(b"https://wa.me/254700123456", login_response.data)
        self.assertIn(b"WhatsApp", login_response.data)

        self.post_as_signed_in_user(
            f"/admin/contact-requests/{contact_request_id}/responded",
            client=self.client,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            status = connection.execute(
                "SELECT status FROM contact_requests WHERE id = ?",
                (contact_request_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(status, "responded")
        self.assertNotIn(b"Mark responded", self.client.get("/admin").data)

    def test_public_contact_request_validates_fields_and_requires_csrf(self):
        response = self.client.post(
            "/contact",
            data={
                "full_name": "Amina Njeri",
                "email": "amina@example.com",
                "request_type": "feedback",
                "message": "A note",
            },
        )
        self.assertEqual(response.status_code, 400)

        token = self.csrf_token("/")
        response = self.client.post(
            "/contact",
            data={
                "csrf_token": token,
                "full_name": "Amina Njeri",
                "email": "not-an-email",
                "request_type": "feedback",
                "message": "A note",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Enter a valid email address.", response.data)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM contact_requests").fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_public_contact_request_sends_configured_twilio_alert(self):
        self.app.config.update(
            TWILIO_ACCOUNT_SID="ACtest",
            TWILIO_AUTH_TOKEN="test-token",
            TWILIO_FROM_PHONE="+15005550006",
            CONTACT_ALERT_PHONE="+254715552003",
        )
        token = self.csrf_token("/")
        with patch("server.urlopen") as mock_urlopen:
            response = self.client.post(
                "/contact",
                data={
                    "csrf_token": token,
                    "full_name": "Amina Njeri",
                    "email": "amina@example.com",
                    "request_type": "mentorship",
                    "message": "I would like to find a mentor.",
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your request has been sent.", response.data)
        mock_urlopen.assert_called_once()
        sms_request = mock_urlopen.call_args.args[0]
        self.assertEqual(
            parse_qs(sms_request.data.decode("ascii")),
            {
                "To": ["+254715552003"],
                "From": ["+15005550006"],
                "Body": [
                    "A new YMP contact request is waiting in the admin dashboard."
                ],
            },
        )
        self.assertIn("Authorization", sms_request.headers)

    def test_twilio_alert_failure_keeps_contact_request_and_reports_failure(self):
        self.app.config.update(
            TWILIO_ACCOUNT_SID="ACtest",
            TWILIO_AUTH_TOKEN="test-token",
            TWILIO_FROM_PHONE="+15005550006",
        )
        token = self.csrf_token("/")
        with patch("server.urlopen", side_effect=URLError("unavailable")):
            response = self.client.post(
                "/contact",
                data={
                    "csrf_token": token,
                    "full_name": "Amina Njeri",
                    "email": "amina@example.com",
                    "request_type": "mentorship",
                    "message": "I would like to find a mentor.",
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"phone alert could not be sent", response.data)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM contact_requests"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_twilio_configuration_must_be_complete(self):
        with self.assertRaisesRegex(RuntimeError, "Configure YMP_TWILIO"):
            create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "test-key",
                    "DATABASE": str(Path(self.temp_dir.name) / "incomplete.db"),
                    "UPLOAD_FOLDER": str(Path(self.temp_dir.name) / "incomplete-uploads"),
                    "TWILIO_ACCOUNT_SID": "ACtest",
                }
            )

    def test_shared_access_password_requires_matching_entries_then_grants_session(self):
        password = "shared-platform-passphrase"
        self.app.config["ACCESS_PASSWORD_HASH"] = generate_password_hash(password)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/access")
        access_page = self.client.get("/access")
        self.assertEqual(access_page.status_code, 200)
        self.assertIn(b"Confirm shared password", access_page.data)

        token = self.csrf_token("/access")
        response = self.client.post(
            "/access",
            data={
                "csrf_token": token,
                "password": password,
                "password_confirmation": "different-passphrase",
            },
            follow_redirects=True,
        )
        self.assertIn(b"The passwords do not match.", response.data)
        self.assertEqual(self.client.get("/").status_code, 302)

        token = self.csrf_token("/access")
        response = self.client.post(
            "/access",
            data={
                "csrf_token": token,
                "password": password,
                "password_confirmation": password,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mentorship, with a plan.", response.data)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_access_page_explains_when_shared_password_is_not_configured(self):
        response = self.client.get("/access")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Access password not set up", response.data)
        self.assertIn(b"configure-access-password", response.data)
        self.assertNotIn(b'name="password_confirmation"', response.data)

    def test_access_password_command_saves_a_hash_for_next_app_start(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(
            args=["configure-access-password"],
            input="shared-platform-passphrase\nshared-platform-passphrase\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Restart the web app", result.output)

        password_file = Path(self.app.config["ACCESS_PASSWORD_HASH_FILE"])
        saved_hash = password_file.read_text(encoding="utf-8")
        self.assertNotEqual(saved_hash, "shared-platform-passphrase")
        self.assertTrue(check_password_hash(saved_hash, "shared-platform-passphrase"))

    def test_access_password_rotation_revokes_existing_gate_sessions(self):
        first_password = "first-shared-passphrase"
        self.app.config["ACCESS_PASSWORD_HASH"] = generate_password_hash(first_password)
        token = self.csrf_token("/access")
        self.client.post(
            "/access",
            data={
                "csrf_token": token,
                "password": first_password,
                "password_confirmation": first_password,
            },
        )
        self.assertEqual(self.client.get("/").status_code, 200)

        self.app.config["ACCESS_PASSWORD_HASH"] = generate_password_hash(
            "second-shared-passphrase"
        )
        self.assertEqual(self.client.get("/").location, "/access")

    def test_registration_login_dashboard_and_logout(self):
        token = self.csrf_token("/register")
        response = self.client.post(
            "/register",
            data={
                "csrf_token": token,
                "full_name": "Amina Njeri",
                "email": "AMINA@example.com",
                "password": "secure-passphrase",
                "password_confirmation": "secure-passphrase",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Welcome, <span>Amina.", response.data)

        with self.client.session_transaction() as current_session:
            current_session.clear()

        token = self.csrf_token("/login")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "amina@example.com",
                "password": "secure-passphrase",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"YOUR YMP SPACE", response.data)

        token = re.search(
            rb'name="csrf_token" value="([^"]+)"', response.data
        ).group(1).decode()
        response = self.client.post(
            "/logout",
            data={"csrf_token": token},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"You have been signed out.", response.data)

    def test_registration_requires_valid_csrf_and_matching_passwords(self):
        response = self.client.post(
            "/register",
            data={"full_name": "Amina", "email": "amina@example.com"},
        )
        self.assertEqual(response.status_code, 400)

        token = self.csrf_token("/register")
        response = self.client.post(
            "/register",
            data={
                "csrf_token": token,
                "full_name": "Amina Njeri",
                "email": "amina@example.com",
                "password": "secure-passphrase",
                "password_confirmation": "different-passphrase",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your passwords do not match.", response.data)

    def test_registration_works_with_legacy_required_role_schema(self):
        legacy_dir = tempfile.TemporaryDirectory()
        self.addCleanup(legacy_dir.cleanup)
        database_path = Path(legacy_dir.name) / "legacy.db"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL
                        CHECK(role IN ('mentee', 'mentor', 'admin')),
                    account_status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(account_status IN ('pending', 'active', 'suspended')),
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.commit()
        finally:
            connection.close()

        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "legacy-test-key",
                "DATABASE": str(database_path),
            }
        )
        client = app.test_client()
        token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            client.get("/register").data,
        ).group(1).decode()
        response = client.post(
            "/register",
            data={
                "csrf_token": token,
                "full_name": "Legacy Schema User",
                "email": "legacy@example.com",
                "password": "secure-passphrase",
                "password_confirmation": "secure-passphrase",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Welcome, <span>Legacy.", response.data)
        connection = sqlite3.connect(database_path)
        try:
            role, account_status = connection.execute(
                "SELECT role, account_status FROM users WHERE email = ?",
                ("legacy@example.com",),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual((role, account_status), ("mentee", "active"))

    def test_existing_database_schema_is_upgraded_and_keeps_records(self):
        legacy_dir = tempfile.TemporaryDirectory()
        self.addCleanup(legacy_dir.cleanup)
        database_path = Path(legacy_dir.name) / "older-schema.db"
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    account_status TEXT NOT NULL DEFAULT 'active'
                );
                INSERT INTO users VALUES
                    (1, 'Existing Mentee', 'existing@example.com', 'hash', 'mentee', 'active');
                CREATE TABLE action_plans (
                    id INTEGER PRIMARY KEY,
                    mentee_id INTEGER NOT NULL,
                    task TEXT NOT NULL
                );
                INSERT INTO action_plans VALUES (1, 1, 'Legacy action');
                CREATE TABLE mentor_matches (
                    id INTEGER PRIMARY KEY,
                    mentor_id INTEGER NOT NULL,
                    mentee_id INTEGER NOT NULL,
                    match_status TEXT DEFAULT 'pending',
                    assigned_at TEXT
                );
                CREATE TABLE mentor_profiles (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, bio TEXT);
                CREATE TABLE safeguarding_reports (
                    id INTEGER PRIMARY KEY,
                    mentee_id INTEGER NOT NULL,
                    report_text TEXT NOT NULL,
                    status TEXT DEFAULT 'open'
                );
                INSERT INTO safeguarding_reports VALUES (1, 1, 'Legacy concern', 'open');
                CREATE TABLE feedback (
                    id INTEGER PRIMARY KEY,
                    mentee_id INTEGER NOT NULL,
                    message TEXT NOT NULL
                );
                INSERT INTO feedback VALUES (1, 1, 'Legacy feedback');
                CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, action TEXT NOT NULL);
                INSERT INTO audit_logs VALUES (1, 'Legacy activity');
            """)
            connection.commit()
        finally:
            connection.close()

        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "migration-test",
                "DATABASE": str(database_path),
            }
        )
        client = app.test_client()
        response = client.get("/login")
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as current_session:
            current_session["user_id"] = 1
        response = client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Legacy action", response.data)

        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT title, completed FROM action_plans WHERE id = 1"
                ).fetchone(),
                ("Legacy action", 0),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT reporter_id, description FROM safeguarding_reports WHERE id = 1"
                ).fetchone(),
                (1, "Legacy concern"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT user_id, message FROM feedback WHERE id = 1"
                ).fetchone(),
                (1, "Legacy feedback"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT activity FROM audit_logs WHERE id = 1"
                ).fetchone()[0],
                "Legacy activity",
            )
        finally:
            connection.close()

    def test_local_session_secret_is_stable_across_app_restarts(self):
        with tempfile.TemporaryDirectory() as instance_path:
            first_secret = _get_secret_key(instance_path)
            second_secret = _get_secret_key(instance_path)
        self.assertEqual(first_secret, second_secret)

    def test_production_requires_a_configured_session_secret(self):
        with patch.dict("os.environ", {"YMP_ENV": "production", "YMP_SECRET_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "YMP_SECRET_KEY"):
                _get_secret_key(self.temp_dir.name)

    def test_database_can_use_a_persistent_deployment_path(self):
        database_path = Path(self.temp_dir.name) / "persistent" / "ymp.db"
        with patch.dict("os.environ", {"YMP_DATABASE_PATH": str(database_path)}):
            app = create_app({"TESTING": True, "SECRET_KEY": "deployment-test"})

        self.assertEqual(app.config["DATABASE"], str(database_path))
        self.assertTrue(database_path.is_file())

    def test_dashboard_requires_sign_in(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_stale_session_is_cleared_before_register_or_login(self):
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = 99999

        response = self.client.get("/register")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="full_name"', response.data)
        self.assertIn(b"Your sign-in session expired.", response.data)
        token = re.search(
            rb'name="csrf_token" value="([^"]+)"', response.data
        ).group(1).decode()
        response = self.client.post(
            "/register",
            data={
                "csrf_token": token,
                "full_name": "Recovered Session User",
                "email": "recovered-session@example.com",
                "role": "mentee",
                "password": "secure-passphrase",
                "password_confirmation": "secure-passphrase",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Welcome, <span>Recovered.", response.data)

    def test_mentee_goals_actions_feedback_and_support_persist(self):
        self.register("workspace@example.com")

        self.post_as_signed_in_user(
            "/goals",
            {
                "title": "Explore a career in design",
                "description": "Research local design courses",
                "target_date": (date.today() + timedelta(days=30)).isoformat(),
            },
        )
        response = self.client.get("/dashboard")
        self.assertIn(b"Explore a career in design", response.data)

        connection = sqlite3.connect(self.database_path)
        try:
            goal_id = connection.execute(
                "SELECT id FROM goals WHERE title = ?",
                ("Explore a career in design",),
            ).fetchone()[0]
        finally:
            connection.close()

        self.post_as_signed_in_user(
            f"/goals/{goal_id}/progress", {"progress": "30"}
        )
        self.post_as_signed_in_user(
            "/actions",
            {"title": "Research courses", "description": "Compare two options"},
        )
        connection = sqlite3.connect(self.database_path)
        try:
            action_id = connection.execute(
                "SELECT id FROM action_plans WHERE mentee_id = 1"
            ).fetchone()[0]
        finally:
            connection.close()
        self.post_as_signed_in_user(f"/actions/{action_id}/complete")
        self.post_as_signed_in_user(
            "/feedback",
            {"subject": "Getting started", "message": "The dashboard is clear", "rating": "5"},
        )
        self.post_as_signed_in_user(
            "/support",
            {
                "report_category": "wellbeing",
                "urgency": "normal",
                "description": "I would like to speak with the programme team.",
            },
        )

        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT progress FROM goals WHERE id = ?", (goal_id,)
                ).fetchone()[0],
                30,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT title, completed FROM action_plans WHERE mentee_id = 1"
                ).fetchone(),
                ("Research courses", 1),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM feedback WHERE user_id = 1"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM safeguarding_reports WHERE reporter_id = 1"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_mentee_can_submit_pdf_and_matched_mentor_can_reply(self):
        mentee_client = self.app.test_client()
        mentee_id, mentor_id, goal_id = self.create_matched_mentee_with_goal(
            mentee_client, "submitter@example.com"
        )
        token = self.csrf_token("/dashboard", mentee_client)
        response = mentee_client.post(
            "/submissions",
            data={
                "csrf_token": token,
                "goal_id": str(goal_id),
                "work_notes": "I completed the first draft of my portfolio.",
                "work_url": "https://example.com/portfolio",
                "work_file": (BytesIO(b"%PDF-1.7\nportfolio evidence"), "portfolio.pdf"),
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your work has been sent to your mentor for review.", response.data)
        self.assertIn(b"portfolio.pdf", response.data)
        self.assertIn(b"Build a portfolio", response.data)

        connection = sqlite3.connect(self.database_path)
        try:
            submission_id, stored_file_name = connection.execute(
                "SELECT id, stored_file_name FROM work_submissions WHERE mentee_id = ?",
                (mentee_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertTrue(
            (Path(self.app.config["UPLOAD_FOLDER"]) / stored_file_name).is_file()
        )
        self.post_as_signed_in_user(
            f"/goals/{goal_id}/delete", client=mentee_client
        )
        self.assertIn(
            b"This goal has submitted work.",
            mentee_client.get("/dashboard").data,
        )

        mentee_download = mentee_client.get(
            f"/submissions/{submission_id}/file"
        )
        self.assertEqual(mentee_download.status_code, 200)
        self.assertIn("attachment", mentee_download.headers["Content-Disposition"])
        self.assertEqual(
            mentee_download.headers["Content-Type"], "application/octet-stream"
        )
        mentee_download.close()

        mentor_client = self.app.test_client()
        with mentor_client.session_transaction() as current_session:
            current_session["user_id"] = mentor_id
        mentor_dashboard = mentor_client.get("/mentor")
        self.assertEqual(mentor_dashboard.status_code, 200)
        self.assertIn(b"I completed the first draft of my portfolio.", mentor_dashboard.data)
        mentor_download = mentor_client.get(f"/submissions/{submission_id}/file")
        self.assertEqual(mentor_download.status_code, 200)
        mentor_download.close()

        other_mentee_client = self.app.test_client()
        self.register("another-mentee@example.com", client=other_mentee_client)
        unauthorized_download = other_mentee_client.get(
            f"/submissions/{submission_id}/file"
        )
        self.assertEqual(unauthorized_download.status_code, 404)

        self.post_as_signed_in_user(
            f"/mentor/submissions/{submission_id}/feedback",
            {"mentor_feedback": "Great start. Add a short introduction."},
            client=mentor_client,
        )
        mentee_dashboard = mentee_client.get("/dashboard")
        self.assertIn(b"Great start. Add a short introduction.", mentee_dashboard.data)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT status, mentor_feedback FROM work_submissions WHERE id = ?",
                    (submission_id,),
                ).fetchone(),
                ("reviewed", "Great start. Add a short introduction."),
            )
        finally:
            connection.close()

    def test_work_submission_requires_accepted_mentor_and_valid_file_or_link(self):
        mentee_client = self.app.test_client()
        self.register("no-mentor@example.com", client=mentee_client)
        self.post_as_signed_in_user(
            "/goals", {"title": "Practice", "description": ""}, client=mentee_client
        )
        token = self.csrf_token("/dashboard", mentee_client)
        response = mentee_client.post(
            "/submissions",
            data={
                "csrf_token": token,
                "goal_id": "1",
                "work_notes": "Here is my result.",
            },
            follow_redirects=True,
        )
        self.assertIn(b"accepted mentor match", response.data)

        matched_client = self.app.test_client()
        mentee_id, _, goal_id = self.create_matched_mentee_with_goal(
            matched_client, "other-mentee@example.com"
        )
        token = self.csrf_token("/dashboard", matched_client)
        response = matched_client.post(
            "/submissions",
            data={
                "csrf_token": token,
                "goal_id": str(goal_id),
                "work_url": "javascript:alert(1)",
                "work_file": (BytesIO(b"not a real PDF"), "evidence.pdf"),
            },
            follow_redirects=True,
        )
        self.assertIn(b"valid public HTTP or HTTPS link", response.data)
        token = self.csrf_token("/dashboard", matched_client)
        response = matched_client.post(
            "/submissions",
            data={
                "csrf_token": token,
                "goal_id": str(goal_id),
                "work_file": (BytesIO(b"not a PDF"), "evidence.pdf"),
            },
            follow_redirects=True,
        )
        self.assertIn(
            b"file content does not match a supported PDF", response.data
        )
        token = self.csrf_token("/dashboard", matched_client)
        response = matched_client.post(
            "/submissions",
            data={
                "csrf_token": token,
                "goal_id": str(goal_id),
                "work_file": (BytesIO(b"some notes"), "evidence.txt"),
            },
            follow_redirects=True,
        )
        self.assertIn(b"Upload a PDF, JPG, or PNG file.", response.data)
        token = self.csrf_token("/dashboard", matched_client)
        response = matched_client.post(
            "/submissions",
            data={
                "csrf_token": token,
                "goal_id": str(goal_id),
                "work_file": (
                    BytesIO(b"%PDF-" + b"x" * (5 * 1024 * 1024)),
                    "large.pdf",
                ),
            },
            follow_redirects=True,
        )
        self.assertIn(b"Work files must be 5 MB or smaller.", response.data)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM work_submissions WHERE mentee_id = ?",
                    (mentee_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_mentor_matching_and_booking_flow(self):
        mentor_client = self.app.test_client()
        self.register("mentor@example.com", role="mentor", client=mentor_client)
        mentor_id = 1
        response = mentor_client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mentor", response.headers["Location"])
        self.post_as_signed_in_user(
            "/mentor/profile",
            {
                "expertise": "Career planning",
                "qualifications": "Career coach",
                "experience": "Five years",
                "biography": "I enjoy helping young people plan their next step.",
                "availability": "Weekday evenings",
            },
            mentor_client,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE mentor_profiles SET vetting_status = 'approved' "
                "WHERE user_id = ?",
                (mentor_id,),
            )
            connection.commit()
        finally:
            connection.close()

        mentee_client = self.app.test_client()
        self.register("mentee@example.com", client=mentee_client)
        response = mentee_client.get("/dashboard")
        self.assertIn(b"Career planning", response.data)
        self.post_as_signed_in_user(
            "/matches", {"mentor_id": str(mentor_id)}, mentee_client
        )
        connection = sqlite3.connect(self.database_path)
        try:
            match_id = connection.execute(
                "SELECT id FROM mentor_matches WHERE mentor_id = ?",
                (mentor_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        self.post_as_signed_in_user(
            f"/mentor/matches/{match_id}/accept", client=mentor_client
        )
        future_date = (date.today() + timedelta(days=2)).isoformat()
        self.post_as_signed_in_user(
            "/sessions",
            {
                "session_date": future_date,
                "session_time": "14:30",
                "meeting_type": "online",
                "notes": "First introduction",
            },
            mentee_client,
        )
        response = mentor_client.get("/mentor")
        self.assertIn(b"First introduction", response.data)
        connection = sqlite3.connect(self.database_path)
        try:
            booking_id = connection.execute(
                "SELECT id FROM bookings WHERE mentee_id = 2 AND mentor_id = 1"
            ).fetchone()[0]
        finally:
            connection.close()
        self.post_as_signed_in_user(
            f"/mentor/bookings/{booking_id}/confirm", client=mentor_client
        )
        self.assertIn(b"Confirmed", mentee_client.get("/dashboard").data)

    def test_admin_can_approve_mentor_publish_resource_and_review_report(self):
        result = self.app.test_cli_runner().invoke(
            args=["create-admin"],
            input=(
                "Programme Admin\nadmin@example.com\n"
                "secure-admin-passphrase\nsecure-admin-passphrase\n"
            ),
        )
        self.assertEqual(result.exit_code, 0, result.output)
        mentor_id = self.create_user("Programme Mentor", "mentor@example.com", "mentor")
        mentee_id = self.create_user("Programme Mentee", "mentee@example.com", "mentee")
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "INSERT INTO mentor_profiles (user_id, expertise) VALUES (?, ?)",
                (mentor_id, "Public speaking"),
            )
            connection.execute(
                "INSERT INTO safeguarding_reports "
                "(reporter_id, report_category, description) VALUES (?, ?, ?)",
                (mentee_id, "wellbeing", "Please follow up with me."),
            )
            connection.execute(
                "INSERT INTO feedback (user_id, subject, message, rating) "
                "VALUES (?, ?, ?, ?)",
                (mentee_id, "Programme feedback", "Please add more career resources.", 4),
            )
            connection.commit()
        finally:
            connection.close()

        token = self.csrf_token("/login")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "admin@example.com",
                "password": "secure-admin-passphrase",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Admin <span>workspace.</span>", response.data)
        self.assertIn(b"Please add more career resources.", response.data)
        self.post_as_signed_in_user(
            f"/admin/mentors/{mentor_id}/approve", client=self.client
        )
        self.post_as_signed_in_user(
            "/admin/matches",
            {"mentee_id": str(mentee_id), "mentor_id": str(mentor_id)},
            client=self.client,
        )
        self.post_as_signed_in_user(
            "/admin/resources",
            {
                "title": "Interview practice",
                "category": "Careers",
                "resource_url": "https://example.com/interview",
                "description": "Prepare for an interview.",
            },
        )
        connection = sqlite3.connect(self.database_path)
        try:
            report_id = connection.execute(
                "SELECT id FROM safeguarding_reports WHERE reporter_id = ?",
                (mentee_id,),
            ).fetchone()[0]
            resource_id = connection.execute(
                "SELECT id FROM resources WHERE title = ?",
                ("Interview practice",),
            ).fetchone()[0]
            self.assertEqual(
                connection.execute(
                    "SELECT match_status FROM mentor_matches "
                    "WHERE mentee_id = ? AND mentor_id = ?",
                    (mentee_id, mentor_id),
                ).fetchone()[0],
                "pending",
            )
        finally:
            connection.close()
        self.post_as_signed_in_user(
            f"/admin/reports/{report_id}/close", client=self.client
        )

        mentee_client = self.app.test_client()
        self.register("reader@example.com", client=mentee_client)
        resource_response = self.post_as_signed_in_user(
            f"/resources/{resource_id}/open", client=mentee_client
        )
        self.assertEqual(
            resource_response.headers["Location"],
            "https://example.com/interview",
        )
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM safeguarding_reports WHERE id = ?",
                    (report_id,),
                ).fetchone()[0],
                "closed",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM lesson_views WHERE resource_id = ?",
                    (resource_id,),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_login_explains_when_no_accounts_exist(self):
        token = self.csrf_token("/login")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "new-user@example.com",
                "password": "secure-passphrase",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"There are no accounts yet. Please create one before signing in.",
            response.data,
        )


if __name__ == "__main__":
    unittest.main()
