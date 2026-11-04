import base64
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from datetime import date, datetime, time
from functools import wraps
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as URLRequest, urlopen

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
import click
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import database


BASE_DIR = Path(__file__).resolve().parent
MAX_WORK_FILE_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = MAX_WORK_FILE_BYTES + 64 * 1024


def _international_phone_number(phone_number):
    cleaned_number = re.sub(r"[^\d+]", "", phone_number)
    if re.fullmatch(r"0\d{9}", cleaned_number):
        return f"+254{cleaned_number[1:]}"
    return cleaned_number


def _send_contact_request_sms(app):
    account_sid = app.config["TWILIO_ACCOUNT_SID"]
    auth_token = app.config["TWILIO_AUTH_TOKEN"]
    from_phone = app.config["TWILIO_FROM_PHONE"]
    if not all((account_sid, auth_token, from_phone)):
        return None

    message = "A new YMP contact request is waiting in the admin dashboard."
    payload = urlencode(
        {
            "To": app.config["CONTACT_ALERT_PHONE"],
            "From": from_phone,
            "Body": message,
        }
    ).encode("ascii")
    authorization = base64.b64encode(
        f"{account_sid}:{auth_token}".encode("utf-8")
    ).decode("ascii")
    sms_request = URLRequest(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data=payload,
        headers={
            "Authorization": f"Basic {authorization}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlopen(sms_request, timeout=10):
            return True
    except HTTPError as error:
        app.logger.error(
            "Twilio contact alert failed with HTTP status %s.", error.code
        )
    except (URLError, TimeoutError) as error:
        app.logger.error(
            "Twilio contact alert failed due to a network error (%s).",
            type(error).__name__,
        )
    return False


def _get_secret_key(instance_path):
    configured_key = os.environ.get("YMP_SECRET_KEY")
    if configured_key:
        return configured_key
    if os.environ.get("YMP_ENV", "").lower() == "production":
        raise RuntimeError("YMP_SECRET_KEY must be configured in production.")

    secret_path = Path(instance_path) / "secret_key"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with secret_path.open("x", encoding="utf-8") as secret_file:
            secret_file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return secret_path.read_text(encoding="utf-8").strip()


def create_app(test_config=None):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        SECRET_KEY=_get_secret_key(app.instance_path),
        DATABASE=os.environ.get("YMP_DATABASE_PATH", str(database.DATABASE_PATH)),
        PUBLIC_CONTACT_PHONE=os.environ.get(
            "YMP_PUBLIC_CONTACT_PHONE", "0715552003"
        ),
        CONTACT_ALERT_PHONE=os.environ.get("YMP_CONTACT_ALERT_PHONE"),
        TWILIO_ACCOUNT_SID=os.environ.get("YMP_TWILIO_ACCOUNT_SID"),
        TWILIO_AUTH_TOKEN=os.environ.get("YMP_TWILIO_AUTH_TOKEN"),
        TWILIO_FROM_PHONE=os.environ.get("YMP_TWILIO_FROM_PHONE"),
        ACCESS_PASSWORD_HASH_FILE=str(
            Path(app.instance_path) / "access_password_hash"
        ),
        ACCESS_PASSWORD_HASH=None,
        PUBLIC_SOCIAL_LINKS=(
            (
                "Facebook",
                os.environ.get(
                    "YMP_FACEBOOK_URL",
                    "https://www.facebook.com/profile.php?id=100083323632489",
                ),
            ),
            (
                "YouTube",
                os.environ.get(
                    "YMP_YOUTUBE_URL",
                    "https://www.youtube.com/@enockochiengu9r",
                ),
            ),
            (
                "LinkedIn",
                os.environ.get("YMP_LINKEDIN_URL", "https://www.linkedin.com/feed/"),
            ),
            (
                "GitHub",
                os.environ.get(
                    "YMP_GITHUB_URL",
                    "https://github.com/enocko687-prog/DI-BOOTCAMP-1",
                ),
            ),
        ),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_REQUEST_BYTES,
        UPLOAD_FOLDER=os.environ.get(
            "YMP_UPLOAD_FOLDER",
            str(Path(app.instance_path) / "submissions"),
        ),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("YMP_COOKIE_SECURE") == "1",
    )
    if test_config:
        app.config.update(test_config)
    if not test_config or "ACCESS_PASSWORD_HASH" not in test_config:
        access_password_file = Path(app.config["ACCESS_PASSWORD_HASH_FILE"])
        if access_password_file.exists():
            app.config["ACCESS_PASSWORD_HASH"] = access_password_file.read_text(
                encoding="utf-8"
            ).strip()
    if not app.config["CONTACT_ALERT_PHONE"]:
        app.config["CONTACT_ALERT_PHONE"] = app.config["PUBLIC_CONTACT_PHONE"]
    app.config["CONTACT_ALERT_PHONE"] = _international_phone_number(
        app.config["CONTACT_ALERT_PHONE"]
    )
    twilio_settings = (
        app.config["TWILIO_ACCOUNT_SID"],
        app.config["TWILIO_AUTH_TOKEN"],
        app.config["TWILIO_FROM_PHONE"],
    )
    if any(twilio_settings) and not all(twilio_settings):
        raise RuntimeError(
            "Configure YMP_TWILIO_ACCOUNT_SID, YMP_TWILIO_AUTH_TOKEN, and "
            "YMP_TWILIO_FROM_PHONE together."
        )

    database_path = app.config["DATABASE"]
    if str(database_path) != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    database.initialize_database(app.config["DATABASE"])

    def get_db():
        if "db" not in g:
            g.db = database.get_connection(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(_error=None):
        del _error
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    def current_csrf_token():
        token = session.get("_csrf_token")
        if token is None:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token

    @app.context_processor
    def inject_template_values():
        phone_number = re.sub(r"[^\d+]", "", app.config["PUBLIC_CONTACT_PHONE"])
        whatsapp_number = re.sub(
            r"\D", "", _international_phone_number(phone_number)
        )
        return {
            "csrf_token": current_csrf_token,
            "public_contact_phone": app.config["PUBLIC_CONTACT_PHONE"],
            "public_contact_tel_url": f"tel:{phone_number}",
            "public_contact_sms_url": f"sms:{phone_number}",
            "public_whatsapp_url": f"https://wa.me/{whatsapp_number}",
            "public_social_links": app.config["PUBLIC_SOCIAL_LINKS"],
        }

    @app.before_request
    def require_shared_access():
        password_hash = app.config["ACCESS_PASSWORD_HASH"]
        if request.endpoint in {"static", "shared_access"} or not password_hash:
            return

        granted_hash = session.get("site_access_granted", "")
        if not hmac.compare_digest(granted_hash, password_hash):
            return redirect(url_for("shared_access"))

    @app.before_request
    def clear_invalid_session():
        if request.endpoint == "static" or "user_id" not in session:
            return

        user = get_db().execute(
            "SELECT account_status FROM users WHERE id = ?",
            (session["user_id"],),
        ).fetchone()
        if user is None or user["account_status"] != "active":
            session.clear()
            flash("Your sign-in session expired. Please sign in again.", "info")

    @app.before_request
    def protect_forms():
        if request.method == "POST":
            submitted_token = request.form.get("csrf_token", "")
            expected_token = session.get("_csrf_token", "")
            if not expected_token or not hmac.compare_digest(
                submitted_token, expected_token
            ):
                abort(400, description="Your form session expired. Please try again.")

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.errorhandler(413)
    def handle_request_too_large(_error):
        if request.endpoint == "submit_goal_work":
            flash("Work files must be 5 MB or smaller.", "error")
            return redirect(url_for("dashboard") + "#submissions")
        return "The submitted request is too large.", 413

    def login_required(view):
        @wraps(view)
        def wrapped_view(**kwargs):
            user_id = session.get("user_id")
            if user_id is None:
                flash("Please sign in to view your dashboard.", "info")
                return redirect(url_for("login"))

            user = get_db().execute(
                "SELECT id, full_name, email, role, account_status "
                "FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if user is None or user["account_status"] != "active":
                session.clear()
                flash("Your account is not currently active.", "error")
                return redirect(url_for("login"))

            g.user = user
            return view(**kwargs)

        return wrapped_view

    def mentee_required(view):
        @wraps(view)
        @login_required
        def wrapped_view(**kwargs):
            if g.user["role"] != "mentee":
                abort(403)
            return view(**kwargs)

        return wrapped_view

    def role_required(*roles):
        def decorate(view):
            @wraps(view)
            @login_required
            def wrapped_view(**kwargs):
                if g.user["role"] not in roles:
                    abort(403)
                return view(**kwargs)

            return wrapped_view

        return decorate

    @app.cli.command("create-admin")
    def create_admin():
        """Create an administrator account from the command line."""
        full_name = click.prompt("Administrator name").strip()
        email = click.prompt("Administrator email").strip().lower()
        password = click.prompt(
            "Administrator password", hide_input=True, confirmation_prompt=True
        )
        if len(full_name) < 2 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email
        ) or len(password) < 12:
            raise click.ClickException(
                "Enter a valid name and email and use a password of at least 12 characters."
            )
        connection = database.get_connection(app.config["DATABASE"])
        try:
            with connection:
                connection.execute(
                    "INSERT INTO users "
                    "(full_name, email, password_hash, role, account_status) "
                    "VALUES (?, ?, ?, 'admin', 'active')",
                    (full_name, email, generate_password_hash(password)),
                )
        except sqlite3.IntegrityError as error:
            if "UNIQUE constraint failed: users.email" not in str(error):
                raise
            raise click.ClickException("An account with that email already exists.") from error
        finally:
            connection.close()
        click.echo("Administrator account created.")

    @app.cli.command("configure-access-password")
    def configure_access_password():
        """Set the shared password required to access the platform."""
        password = click.prompt(
            "Shared platform access password",
            hide_input=True,
            confirmation_prompt=True,
        )
        if not 12 <= len(password) <= 128:
            raise click.ClickException(
                "Use a shared access password between 12 and 128 characters."
            )

        password_file = Path(app.config["ACCESS_PASSWORD_HASH_FILE"])
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text(generate_password_hash(password), encoding="utf-8")
        click.echo(
            "Shared access password saved. Restart the web app to apply the change."
        )

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.route("/access", methods=["GET", "POST"])
    def shared_access():
        password_hash = app.config["ACCESS_PASSWORD_HASH"]
        if not password_hash:
            return render_template("shared_access.html", password_configured=False)
        if request.method == "POST":
            password = request.form.get("password", "")
            password_confirmation = request.form.get("password_confirmation", "")
            if not hmac.compare_digest(password, password_confirmation):
                flash("The passwords do not match. Please try again.", "error")
            elif not 12 <= len(password) <= 128 or not check_password_hash(
                password_hash, password
            ):
                flash("The shared password is incorrect.", "error")
            else:
                session["site_access_granted"] = password_hash
                return redirect(url_for("home"))
        return render_template("shared_access.html", password_configured=True)

    @app.post("/contact")
    def submit_contact_request():
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        request_type = request.form.get("request_type", "").strip()
        message = request.form.get("message", "").strip()

        if not 2 <= len(full_name) <= 100:
            flash("Enter your name (2 to 100 characters).", "error")
        elif len(email) > 254 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email
        ):
            flash("Enter a valid email address.", "error")
        elif len(phone) > 30 or (
            phone and not re.fullmatch(r"[+()\d.\-\s]+", phone)
        ):
            flash("Enter a valid phone number or leave it blank.", "error")
        elif request_type not in {"mentorship", "question", "feedback", "other"}:
            flash("Choose what you are contacting us about.", "error")
        elif not message or len(message) > 3000:
            flash("Add a message of up to 3,000 characters.", "error")
        else:
            get_db().execute(
                """
                INSERT INTO contact_requests
                    (full_name, email, phone, request_type, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (full_name, email, phone or None, request_type, message),
            )
            get_db().commit()
            if _send_contact_request_sms(app) is False:
                flash(
                    "Your request was saved, but the programme team's phone "
                    "alert could not be sent. Please call us directly if your "
                    "request is urgent.",
                    "error",
                )
            else:
                flash(
                    "Your request has been sent. We will contact you using "
                    "the details provided.",
                    "success",
                )
        return redirect(url_for("home") + "#contact")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if session.get("user_id"):
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            password_confirmation = request.form.get("password_confirmation", "")
            role = request.form.get("role", "mentee")

            if len(full_name) < 2 or len(full_name) > 100:
                flash("Enter your name (2 to 100 characters).", "error")
            elif len(email) > 254 or not re.fullmatch(
                r"[^@\s]+@[^@\s]+\.[^@\s]+", email
            ):
                flash("Enter a valid email address.", "error")
            elif len(password) < 8:
                flash("Choose a password with at least 8 characters.", "error")
            elif password != password_confirmation:
                flash("Your passwords do not match.", "error")
            elif role not in {"mentee", "mentor"}:
                flash("Choose whether you are joining as a mentee or mentor.", "error")
            else:
                try:
                    cursor = get_db().execute(
                        "INSERT INTO users "
                        "(full_name, email, password_hash, role, account_status) "
                        "VALUES (?, ?, ?, ?, 'active')",
                        (full_name, email, generate_password_hash(password), role),
                    )
                    profile_table = (
                        "mentor_profiles" if role == "mentor" else "mentee_profiles"
                    )
                    get_db().execute(
                        f"INSERT INTO {profile_table} (user_id) VALUES (?)",
                        (cursor.lastrowid,),
                    )
                    get_db().commit()
                except sqlite3.IntegrityError as error:
                    if "UNIQUE constraint failed: users.email" not in str(error):
                        raise
                    flash("An account with that email already exists.", "error")
                else:
                    session.clear()
                    session["user_id"] = cursor.lastrowid
                    flash("Welcome to YMP. Your account is ready.", "success")
                    return redirect(url_for("dashboard"))

        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("user_id"):
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_db().execute(
                "SELECT id, password_hash, account_status FROM users WHERE email = ?",
                (email,),
            ).fetchone()

            if (
                user is None
                or not user["password_hash"]
                or not check_password_hash(user["password_hash"], password)
            ):
                account_count = get_db().execute(
                    "SELECT COUNT(*) FROM users"
                ).fetchone()[0]
                if account_count == 0:
                    flash(
                        "There are no accounts yet. Please create one before signing in.",
                        "info",
                    )
                else:
                    flash(
                        "That email and password combination was not recognised.",
                        "error",
                    )
            elif user["account_status"] != "active":
                flash("Your account is not currently active.", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                flash("Good to see you again.", "success")
                return redirect(url_for("dashboard"))

        return render_template("auth.html", mode="login")

    @app.post("/logout")
    def logout():
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("home"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        connection = get_db()
        user_id = g.user["id"]
        if g.user["role"] == "admin":
            return redirect(url_for("admin_dashboard"))
        if g.user["role"] == "mentor":
            return redirect(url_for("mentor_dashboard"))
        stats = {
            "goals": connection.execute(
                "SELECT COUNT(*) FROM goals WHERE mentee_id = ?", (user_id,)
            ).fetchone()[0],
            "actions": connection.execute(
                "SELECT COUNT(*) FROM action_plans "
                "WHERE mentee_id = ? AND completed = 0",
                (user_id,),
            ).fetchone()[0],
            "sessions": connection.execute(
                "SELECT COUNT(*) FROM bookings "
                "WHERE mentee_id = ? AND status IN ('pending', 'confirmed')",
                (user_id,),
            ).fetchone()[0],
            "resources": connection.execute(
                "SELECT COUNT(*) FROM resources"
            ).fetchone()[0],
        }
        mentor = connection.execute(
            """
            SELECT users.full_name, mentor_profiles.expertise
            FROM mentor_matches
            JOIN users ON users.id = mentor_matches.mentor_id
            LEFT JOIN mentor_profiles ON mentor_profiles.user_id = users.id
            WHERE mentor_matches.mentee_id = ?
                AND mentor_matches.match_status = 'accepted'
            ORDER BY mentor_matches.matched_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        goals = connection.execute(
            "SELECT id, title, description, target_date, status, progress "
            "FROM goals WHERE mentee_id = ? ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        submissions = connection.execute(
            """
            SELECT work_submissions.id, work_submissions.work_notes,
                   work_submissions.work_url, work_submissions.file_name,
                   work_submissions.status, work_submissions.mentor_feedback,
                   work_submissions.created_at, work_submissions.reviewed_at,
                   goals.title AS goal_title, mentors.full_name AS mentor_name
            FROM work_submissions
            JOIN goals ON goals.id = work_submissions.goal_id
            JOIN users AS mentors ON mentors.id = work_submissions.mentor_id
            WHERE work_submissions.mentee_id = ?
            ORDER BY work_submissions.created_at DESC, work_submissions.id DESC
            """,
            (user_id,),
        ).fetchall()
        actions = connection.execute(
            "SELECT id, title, description, action_date, completed "
            "FROM action_plans WHERE mentee_id = ? "
            "ORDER BY completed, action_date, id DESC",
            (user_id,),
        ).fetchall()
        bookings = connection.execute(
            """
            SELECT bookings.id, bookings.session_date, bookings.session_time,
                   bookings.meeting_type, bookings.status, users.full_name AS mentor_name
            FROM bookings
            JOIN users ON users.id = bookings.mentor_id
            WHERE bookings.mentee_id = ?
            ORDER BY bookings.session_date, bookings.session_time
            """,
            (user_id,),
        ).fetchall()
        resources = connection.execute(
            "SELECT id, title, description, category, resource_url "
            "FROM resources ORDER BY created_at DESC, id DESC"
        ).fetchall()
        mentors = connection.execute(
            """
            SELECT users.id, users.full_name, mentor_profiles.expertise
            FROM users
            JOIN mentor_profiles ON mentor_profiles.user_id = users.id
            WHERE users.role = 'mentor' AND users.account_status = 'active'
                AND mentor_profiles.vetting_status = 'approved'
                AND NOT EXISTS (
                    SELECT 1 FROM mentor_matches
                    WHERE mentor_matches.mentor_id = users.id
                        AND mentor_matches.mentee_id = ?
                        AND mentor_matches.match_status IN ('pending', 'accepted')
                )
            ORDER BY users.full_name
            """,
            (user_id,),
        ).fetchall()
        match_request = connection.execute(
            "SELECT match_status FROM mentor_matches WHERE mentee_id = ? "
            "AND match_status IN ('pending', 'accepted') "
            "ORDER BY matched_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return render_template(
            "mentee_dashboard.html",
            user=g.user,
            stats=stats,
            mentor=mentor,
            goals=goals,
            submissions=submissions,
            actions=actions,
            bookings=bookings,
            resources=resources,
            mentors=mentors,
            match_request=match_request,
        )

    @app.post("/goals")
    @mentee_required
    def create_goal():
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        target_date = request.form.get("target_date", "").strip() or None
        if not title or len(title) > 120 or len(description) > 2000:
            flash("Add a goal title (up to 120 characters) and a shorter description.", "error")
        else:
            try:
                if target_date:
                    date.fromisoformat(target_date)
            except ValueError:
                flash("Choose a valid target date.", "error")
            else:
                get_db().execute(
                    "INSERT INTO goals (mentee_id, title, description, target_date) "
                    "VALUES (?, ?, ?, ?)",
                    (g.user["id"], title, description, target_date),
                )
                get_db().commit()
                flash("Your goal has been added.", "success")
        return redirect(url_for("dashboard") + "#goals")

    @app.post("/goals/<int:goal_id>/progress")
    @mentee_required
    def update_goal_progress(goal_id):
        try:
            progress = int(request.form.get("progress", ""))
        except ValueError:
            abort(400, description="Choose a progress value from 0 to 100.")
        if not 0 <= progress <= 100:
            abort(400, description="Choose a progress value from 0 to 100.")
        result = get_db().execute(
            "UPDATE goals SET progress = ?, status = ? "
            "WHERE id = ? AND mentee_id = ?",
            (
                progress,
                "completed" if progress == 100 else (
                    "not_started" if progress == 0 else "in_progress"
                ),
                goal_id,
                g.user["id"],
            ),
        )
        if result.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("Your goal progress has been updated.", "success")
        return redirect(url_for("dashboard") + "#goals")

    @app.post("/submissions")
    @mentee_required
    def submit_goal_work():
        try:
            goal_id = int(request.form.get("goal_id", ""))
        except ValueError:
            flash("Choose the goal this work is for.", "error")
            return redirect(url_for("dashboard") + "#submissions")
        goal = get_db().execute(
            "SELECT id FROM goals WHERE id = ? AND mentee_id = ?",
            (goal_id, g.user["id"]),
        ).fetchone()
        if goal is None:
            abort(404)

        mentor = get_db().execute(
            """
            SELECT users.id
            FROM mentor_matches
            JOIN users ON users.id = mentor_matches.mentor_id
            WHERE mentor_matches.mentee_id = ?
                AND mentor_matches.match_status = 'accepted'
                AND users.role = 'mentor'
                AND users.account_status = 'active'
            ORDER BY mentor_matches.matched_at DESC
            LIMIT 1
            """,
            (g.user["id"],),
        ).fetchone()
        if mentor is None:
            flash("You need an accepted mentor match before sending work for review.", "error")
            return redirect(url_for("dashboard") + "#submissions")

        work_notes = request.form.get("work_notes", "").strip()
        work_url = request.form.get("work_url", "").strip()
        upload = request.files.get("work_file")
        if upload is not None and not upload.filename:
            upload = None

        if len(work_notes) > 3000 or len(work_url) > 2000:
            flash("Keep your notes under 3,000 characters and link under 2,000.", "error")
            return redirect(url_for("dashboard") + "#submissions")
        if work_url:
            try:
                parsed_url = urlsplit(work_url)
                valid_url = (
                    parsed_url.scheme in {"http", "https"}
                    and bool(parsed_url.hostname)
                    and parsed_url.username is None
                    and parsed_url.password is None
                )
            except ValueError:
                valid_url = False
            if not valid_url:
                flash("Use a valid public HTTP or HTTPS link.", "error")
                return redirect(url_for("dashboard") + "#submissions")
        if not work_notes and not work_url and upload is None:
            flash("Add notes, a work link, or an allowed file before sending.", "error")
            return redirect(url_for("dashboard") + "#submissions")

        safe_file_name = None
        stored_file_name = None
        if upload is not None:
            safe_file_name = secure_filename(upload.filename or "")
            extension = Path(safe_file_name).suffix.lower()
            if extension not in {".pdf", ".jpg", ".jpeg", ".png"}:
                flash("Upload a PDF, JPG, or PNG file.", "error")
                return redirect(url_for("dashboard") + "#submissions")

            upload.stream.seek(0, os.SEEK_END)
            file_size = upload.stream.tell()
            upload.stream.seek(0)
            if file_size > MAX_WORK_FILE_BYTES:
                flash("Work files must be 5 MB or smaller.", "error")
                return redirect(url_for("dashboard") + "#submissions")

            signature = upload.stream.read(8)
            upload.stream.seek(0)
            valid_signature = (
                (extension == ".pdf" and signature.startswith(b"%PDF-"))
                or (extension == ".png" and signature.startswith(b"\x89PNG\r\n\x1a\n"))
                or (extension in {".jpg", ".jpeg"} and signature.startswith(b"\xff\xd8\xff"))
            )
            if not safe_file_name or not valid_signature:
                flash("The file content does not match a supported PDF, JPG, or PNG.", "error")
                return redirect(url_for("dashboard") + "#submissions")

            safe_file_name = safe_file_name[:120]
            stored_file_name = f"{uuid.uuid4().hex}{extension}"
            upload.save(Path(app.config["UPLOAD_FOLDER"]) / stored_file_name)

        try:
            get_db().execute(
                """
                INSERT INTO work_submissions
                    (goal_id, mentee_id, mentor_id, work_notes, work_url,
                     file_name, stored_file_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    goal_id,
                    g.user["id"],
                    mentor["id"],
                    work_notes,
                    work_url or None,
                    safe_file_name,
                    stored_file_name,
                ),
            )
            get_db().commit()
        except sqlite3.Error:
            if stored_file_name:
                (Path(app.config["UPLOAD_FOLDER"]) / stored_file_name).unlink(
                    missing_ok=True
                )
            raise

        flash("Your work has been sent to your mentor for review.", "success")
        return redirect(url_for("dashboard") + "#submissions")

    @app.get("/submissions/<int:submission_id>/file")
    @login_required
    def download_work_submission(submission_id):
        submission = get_db().execute(
            """
            SELECT id, mentee_id, mentor_id, file_name, stored_file_name
            FROM work_submissions
            WHERE id = ?
            """,
            (submission_id,),
        ).fetchone()
        if submission is None or not submission["stored_file_name"]:
            abort(404)

        is_owner = (
            g.user["role"] == "mentee"
            and submission["mentee_id"] == g.user["id"]
        )
        is_assigned_mentor = (
            g.user["role"] == "mentor"
            and submission["mentor_id"] == g.user["id"]
            and get_db().execute(
                """
                SELECT 1 FROM mentor_matches
                WHERE mentee_id = ? AND mentor_id = ? AND match_status = 'accepted'
                """,
                (submission["mentee_id"], g.user["id"]),
            ).fetchone()
            is not None
        )
        if not is_owner and not is_assigned_mentor:
            abort(404)
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            submission["stored_file_name"],
            as_attachment=True,
            download_name=submission["file_name"],
            mimetype="application/octet-stream",
            max_age=0,
        )

    @app.post("/goals/<int:goal_id>/delete")
    @mentee_required
    def delete_goal(goal_id):
        if get_db().execute(
            "SELECT 1 FROM work_submissions WHERE goal_id = ? LIMIT 1",
            (goal_id,),
        ).fetchone():
            flash(
                "This goal has submitted work. Keep it so your mentor can review the submission history.",
                "error",
            )
            return redirect(url_for("dashboard") + "#goals")
        result = get_db().execute(
            "DELETE FROM goals WHERE id = ? AND mentee_id = ?",
            (goal_id, g.user["id"]),
        )
        if result.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("Your goal has been removed.", "info")
        return redirect(url_for("dashboard") + "#goals")

    @app.post("/actions")
    @mentee_required
    def create_action():
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        action_date = request.form.get("action_date", "").strip() or None
        if not title or len(title) > 160 or len(description) > 2000:
            flash("Add an action title (up to 160 characters) and a shorter description.", "error")
        else:
            try:
                if action_date:
                    date.fromisoformat(action_date)
            except ValueError:
                flash("Choose a valid action date.", "error")
            else:
                get_db().execute(
                    "INSERT INTO action_plans "
                    "(mentee_id, title, description, action_date) VALUES (?, ?, ?, ?)",
                    (g.user["id"], title, description, action_date),
                )
                get_db().commit()
                flash("Your action step has been added.", "success")
        return redirect(url_for("dashboard") + "#actions")

    @app.post("/actions/<int:action_id>/complete")
    @mentee_required
    def complete_action(action_id):
        result = get_db().execute(
            "UPDATE action_plans SET completed = 1, status = 'completed' "
            "WHERE id = ? AND mentee_id = ?",
            (action_id, g.user["id"]),
        )
        if result.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("Nice work. Your action step is complete.", "success")
        return redirect(url_for("dashboard") + "#actions")

    @app.post("/actions/<int:action_id>/delete")
    @mentee_required
    def delete_action(action_id):
        result = get_db().execute(
            "DELETE FROM action_plans WHERE id = ? AND mentee_id = ?",
            (action_id, g.user["id"]),
        )
        if result.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("Your action step has been removed.", "info")
        return redirect(url_for("dashboard") + "#actions")

    @app.post("/sessions")
    @mentee_required
    def create_booking():
        mentor = get_db().execute(
            """
            SELECT users.id FROM mentor_matches
            JOIN users ON users.id = mentor_matches.mentor_id
            WHERE mentor_matches.mentee_id = ?
                AND mentor_matches.match_status = 'accepted'
                AND users.role = 'mentor'
                AND users.account_status = 'active'
            ORDER BY mentor_matches.matched_at DESC
            LIMIT 1
            """,
            (g.user["id"],),
        ).fetchone()
        if mentor is None:
            flash("You can request a session after a mentor has been matched to you.", "info")
            return redirect(url_for("dashboard") + "#sessions")

        session_date = request.form.get("session_date", "")
        session_time = request.form.get("session_time", "")
        meeting_type = request.form.get("meeting_type", "online")
        notes = request.form.get("notes", "").strip()
        try:
            requested_at = datetime.combine(
                date.fromisoformat(session_date), time.fromisoformat(session_time)
            )
        except ValueError:
            flash("Choose a valid date and time for your session.", "error")
            return redirect(url_for("dashboard") + "#sessions")
        if requested_at <= datetime.now():
            flash("Choose a session time in the future.", "error")
        elif meeting_type not in {"online", "in_person"} or len(notes) > 1000:
            flash("Choose a valid meeting type and keep notes under 1,000 characters.", "error")
        else:
            get_db().execute(
                "INSERT INTO bookings "
                "(mentor_id, mentee_id, session_date, session_time, meeting_type, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mentor["id"], g.user["id"], session_date, session_time, meeting_type, notes),
            )
            get_db().commit()
            flash("Your session request has been sent to your mentor.", "success")
        return redirect(url_for("dashboard") + "#sessions")

    @app.post("/resources/<int:resource_id>/open")
    @mentee_required
    def open_resource(resource_id):
        resource = get_db().execute(
            "SELECT resource_url FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()
        if resource is None:
            abort(404)
        resource_url = resource["resource_url"] or ""
        if not resource_url.startswith(("https://", "http://")):
            flash("This resource does not have a valid web link yet.", "error")
            return redirect(url_for("dashboard") + "#resources")
        get_db().execute(
            "INSERT INTO lesson_views (mentee_id, resource_id) VALUES (?, ?)",
            (g.user["id"], resource_id),
        )
        get_db().commit()
        return redirect(resource_url)

    @app.post("/feedback")
    @mentee_required
    def submit_feedback():
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        raw_rating = request.form.get("rating", "").strip()
        if not message or len(message) > 3000 or len(subject) > 120:
            flash("Add a message (up to 3,000 characters) and a shorter subject.", "error")
        else:
            try:
                rating = int(raw_rating) if raw_rating else None
            except ValueError:
                rating = 0
            if rating is not None and not 1 <= rating <= 5:
                flash("Choose a rating from 1 to 5.", "error")
                return redirect(url_for("dashboard") + "#feedback")
            get_db().execute(
                "INSERT INTO feedback (user_id, subject, message, rating) "
                "VALUES (?, ?, ?, ?)",
                (g.user["id"], subject, message, rating),
            )
            get_db().commit()
            flash("Thanks for sharing your feedback.", "success")
        return redirect(url_for("dashboard") + "#feedback")

    @app.post("/support")
    @mentee_required
    def submit_support_report():
        category = request.form.get("report_category", "").strip()
        description = request.form.get("description", "").strip()
        urgency = request.form.get("urgency", "normal")
        if category not in {"safety", "wellbeing", "mentorship", "other"}:
            flash("Choose a report category.", "error")
        elif not description or len(description) > 5000:
            flash("Describe your concern in 1 to 5,000 characters.", "error")
        elif urgency not in {"normal", "urgent"}:
            flash("Choose a valid urgency.", "error")
        else:
            get_db().execute(
                "INSERT INTO safeguarding_reports "
                "(reporter_id, report_category, description, urgency) "
                "VALUES (?, ?, ?, ?)",
                (g.user["id"], category, description, urgency),
            )
            get_db().commit()
            flash(
                "Your report has been recorded. If anyone is in immediate danger, contact local emergency services.",
                "success",
            )
        return redirect(url_for("dashboard") + "#support")

    @app.post("/matches")
    @mentee_required
    def request_mentor_match():
        try:
            mentor_id = int(request.form.get("mentor_id", ""))
        except ValueError:
            flash("Choose a mentor to request.", "error")
            return redirect(url_for("dashboard") + "#mentor")
        mentor = get_db().execute(
            """
            SELECT users.id FROM users
            JOIN mentor_profiles ON mentor_profiles.user_id = users.id
            WHERE users.id = ? AND users.role = 'mentor'
                AND users.account_status = 'active'
                AND mentor_profiles.vetting_status = 'approved'
            """,
            (mentor_id,),
        ).fetchone()
        if mentor is None:
            flash("That mentor is not currently available for matching.", "error")
            return redirect(url_for("dashboard") + "#mentor")
        existing_request = get_db().execute(
            "SELECT id FROM mentor_matches WHERE mentee_id = ? "
            "AND match_status IN ('pending', 'accepted') LIMIT 1",
            (g.user["id"],),
        ).fetchone()
        if existing_request:
            flash("You already have a pending or accepted mentor match.", "info")
        else:
            get_db().execute(
                "INSERT INTO mentor_matches (mentor_id, mentee_id, match_status) "
                "VALUES (?, ?, 'pending')",
                (mentor_id, g.user["id"]),
            )
            get_db().commit()
            flash("Your mentor request has been sent.", "success")
        return redirect(url_for("dashboard") + "#mentor")

    @app.get("/mentor")
    @role_required("mentor")
    def mentor_dashboard():
        connection = get_db()
        mentor_id = g.user["id"]
        profile = connection.execute(
            "SELECT expertise, qualifications, experience, biography, availability, "
            "vetting_status FROM mentor_profiles WHERE user_id = ?",
            (mentor_id,),
        ).fetchone()
        matches = connection.execute(
            """
            SELECT mentor_matches.id, mentor_matches.match_status,
                   users.id AS mentee_id, users.full_name, users.email
            FROM mentor_matches
            JOIN users ON users.id = mentor_matches.mentee_id
            WHERE mentor_matches.mentor_id = ?
                AND mentor_matches.match_status IN ('pending', 'accepted')
            ORDER BY mentor_matches.match_status, mentor_matches.matched_at DESC
            """,
            (mentor_id,),
        ).fetchall()
        bookings = connection.execute(
            """
            SELECT bookings.id, bookings.session_date, bookings.session_time,
                   bookings.meeting_type, bookings.status, bookings.notes,
                   users.full_name AS mentee_name
            FROM bookings
            JOIN users ON users.id = bookings.mentee_id
            WHERE bookings.mentor_id = ?
            ORDER BY bookings.session_date, bookings.session_time
            """,
            (mentor_id,),
        ).fetchall()
        work_submissions = connection.execute(
            """
            SELECT work_submissions.id, work_submissions.work_notes,
                   work_submissions.work_url, work_submissions.file_name,
                   work_submissions.status, work_submissions.created_at,
                   goals.title AS goal_title, users.full_name AS mentee_name
            FROM work_submissions
            JOIN goals ON goals.id = work_submissions.goal_id
            JOIN users ON users.id = work_submissions.mentee_id
            WHERE work_submissions.mentor_id = ?
                AND EXISTS (
                    SELECT 1 FROM mentor_matches
                    WHERE mentor_matches.mentor_id = work_submissions.mentor_id
                        AND mentor_matches.mentee_id = work_submissions.mentee_id
                        AND mentor_matches.match_status = 'accepted'
                )
            ORDER BY CASE work_submissions.status WHEN 'pending' THEN 0 ELSE 1 END,
                     work_submissions.created_at DESC
            """,
            (mentor_id,),
        ).fetchall()
        return render_template(
            "mentor_dashboard.html",
            user=g.user,
            profile=profile,
            matches=matches,
            bookings=bookings,
            work_submissions=work_submissions,
        )

    @app.post("/mentor/profile")
    @role_required("mentor")
    def update_mentor_profile():
        expertise = request.form.get("expertise", "").strip()
        qualifications = request.form.get("qualifications", "").strip()
        experience = request.form.get("experience", "").strip()
        biography = request.form.get("biography", "").strip()
        availability = request.form.get("availability", "").strip()
        if (
            len(expertise) > 160
            or len(qualifications) > 500
            or len(experience) > 500
            or len(biography) > 3000
            or len(availability) > 500
        ):
            flash("Please shorten one or more profile fields.", "error")
        else:
            get_db().execute(
                """
                INSERT INTO mentor_profiles
                    (user_id, expertise, qualifications, experience, biography, availability)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    expertise = excluded.expertise,
                    qualifications = excluded.qualifications,
                    experience = excluded.experience,
                    biography = excluded.biography,
                    availability = excluded.availability,
                    vetting_status = 'pending'
                """,
                (
                    g.user["id"],
                    expertise,
                    qualifications,
                    experience,
                    biography,
                    availability,
                ),
            )
            get_db().commit()
            flash("Your mentor profile has been saved and sent for review.", "success")
        return redirect(url_for("mentor_dashboard") + "#profile")

    @app.post("/mentor/matches/<int:match_id>/<decision>")
    @role_required("mentor")
    def decide_mentor_match(match_id, decision):
        if decision not in {"accept", "decline"}:
            abort(404)
        match = get_db().execute(
            "SELECT id, mentee_id FROM mentor_matches "
            "WHERE id = ? AND mentor_id = ? AND match_status = 'pending'",
            (match_id, g.user["id"]),
        ).fetchone()
        if match is None:
            abort(404)
        if decision == "accept":
            other_match = get_db().execute(
                "SELECT id FROM mentor_matches WHERE mentee_id = ? "
                "AND match_status = 'accepted' LIMIT 1",
                (match["mentee_id"],),
            ).fetchone()
            if other_match:
                flash("This mentee already has an accepted mentor match.", "error")
            else:
                get_db().execute(
                    "UPDATE mentor_matches SET match_status = 'declined' "
                    "WHERE mentee_id = ? AND id != ? AND match_status = 'pending'",
                    (match["mentee_id"], match_id),
                )
                get_db().execute(
                    "UPDATE mentor_matches SET match_status = 'accepted' WHERE id = ?",
                    (match_id,),
                )
                get_db().commit()
                flash("Mentor match accepted.", "success")
        else:
            get_db().execute(
                "UPDATE mentor_matches SET match_status = 'declined' WHERE id = ?",
                (match_id,),
            )
            get_db().commit()
            flash("Mentor request declined.", "info")
        return redirect(url_for("mentor_dashboard") + "#matches")

    @app.post("/mentor/bookings/<int:booking_id>/confirm")
    @role_required("mentor")
    def confirm_booking(booking_id):
        result = get_db().execute(
            "UPDATE bookings SET status = 'confirmed' "
            "WHERE id = ? AND mentor_id = ? AND status = 'pending'",
            (booking_id, g.user["id"]),
        )
        if result.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("The mentorship session has been confirmed.", "success")
        return redirect(url_for("mentor_dashboard") + "#sessions")

    @app.post("/mentor/submissions/<int:submission_id>/feedback")
    @role_required("mentor")
    def review_work_submission(submission_id):
        feedback = request.form.get("mentor_feedback", "").strip()
        if not feedback or len(feedback) > 3000:
            flash("Add feedback (up to 3,000 characters).", "error")
            return redirect(url_for("mentor_dashboard") + "#submissions")

        result = get_db().execute(
            """
            UPDATE work_submissions
            SET mentor_feedback = ?, status = 'reviewed',
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND mentor_id = ? AND status = 'pending'
                AND EXISTS (
                    SELECT 1 FROM mentor_matches
                    WHERE mentor_matches.mentor_id = work_submissions.mentor_id
                        AND mentor_matches.mentee_id = work_submissions.mentee_id
                        AND mentor_matches.match_status = 'accepted'
                )
            """,
            (feedback, submission_id, g.user["id"]),
        )
        if result.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("Your feedback has been sent to the mentee.", "success")
        return redirect(url_for("mentor_dashboard") + "#submissions")

    @app.get("/admin")
    @role_required("admin")
    def admin_dashboard():
        connection = get_db()
        mentees = connection.execute(
            "SELECT id, full_name, email FROM users "
            "WHERE role = 'mentee' AND account_status = 'active' "
            "ORDER BY full_name"
        ).fetchall()
        mentors = connection.execute(
            """
            SELECT users.id, users.full_name, users.email,
                   mentor_profiles.expertise, mentor_profiles.vetting_status
            FROM users JOIN mentor_profiles ON mentor_profiles.user_id = users.id
            WHERE users.role = 'mentor' AND users.account_status = 'active'
            ORDER BY users.full_name
            """
        ).fetchall()
        matches = connection.execute(
            """
            SELECT mentor_matches.id, mentor_matches.match_status,
                   mentees.full_name AS mentee_name, mentors.full_name AS mentor_name
            FROM mentor_matches
            JOIN users AS mentees ON mentees.id = mentor_matches.mentee_id
            JOIN users AS mentors ON mentors.id = mentor_matches.mentor_id
            WHERE mentor_matches.match_status IN ('pending', 'accepted')
            ORDER BY mentor_matches.matched_at DESC
            """
        ).fetchall()
        reports = connection.execute(
            """
            SELECT safeguarding_reports.id, safeguarding_reports.report_category,
                   safeguarding_reports.description, safeguarding_reports.urgency,
                   safeguarding_reports.created_at, users.full_name AS reporter_name
            FROM safeguarding_reports
            JOIN users ON users.id = safeguarding_reports.reporter_id
            WHERE safeguarding_reports.status = 'open'
            ORDER BY CASE safeguarding_reports.urgency WHEN 'urgent' THEN 0 ELSE 1 END,
                     safeguarding_reports.created_at
            """
        ).fetchall()
        feedback_items = connection.execute(
            """
            SELECT feedback.subject, feedback.message, feedback.rating,
                   feedback.created_at, users.full_name AS author_name
            FROM feedback
            JOIN users ON users.id = feedback.user_id
            ORDER BY feedback.created_at DESC
            LIMIT 50
            """
        ).fetchall()
        contact_requests = connection.execute(
            """
            SELECT id, full_name, email, phone, request_type, message, status, created_at
            FROM contact_requests
            ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC
            LIMIT 100
            """
        ).fetchall()
        resources = connection.execute(
            "SELECT id, title, category, resource_url FROM resources "
            "ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return render_template(
            "admin_dashboard.html",
            user=g.user,
            mentees=mentees,
            mentors=mentors,
            matches=matches,
            reports=reports,
            feedback_items=feedback_items,
            contact_requests=contact_requests,
            resources=resources,
        )

    @app.post("/admin/mentors/<int:mentor_id>/approve")
    @role_required("admin")
    def approve_mentor(mentor_id):
        result = get_db().execute(
            """
            UPDATE mentor_profiles SET vetting_status = 'approved'
            WHERE user_id = ? AND EXISTS (
                SELECT 1 FROM users
                WHERE users.id = mentor_profiles.user_id AND users.role = 'mentor'
            )
            """,
            (mentor_id,),
        )
        if result.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("Mentor profile approved and available for matching.", "success")
        return redirect(url_for("admin_dashboard") + "#mentors")

    @app.post("/admin/matches")
    @role_required("admin")
    def admin_create_match():
        try:
            mentee_id = int(request.form.get("mentee_id", ""))
            mentor_id = int(request.form.get("mentor_id", ""))
        except ValueError:
            flash("Choose both a mentee and a mentor.", "error")
            return redirect(url_for("admin_dashboard") + "#matches")
        connection = get_db()
        mentee = connection.execute(
            "SELECT id FROM users WHERE id = ? AND role = 'mentee' "
            "AND account_status = 'active'",
            (mentee_id,),
        ).fetchone()
        mentor = connection.execute(
            """
            SELECT users.id FROM users
            JOIN mentor_profiles ON mentor_profiles.user_id = users.id
            WHERE users.id = ? AND users.role = 'mentor'
                AND users.account_status = 'active'
                AND mentor_profiles.vetting_status = 'approved'
            """,
            (mentor_id,),
        ).fetchone()
        if mentee is None or mentor is None:
            flash("Choose an active mentee and an approved active mentor.", "error")
        elif connection.execute(
            "SELECT 1 FROM mentor_matches WHERE mentee_id = ? "
            "AND match_status IN ('pending', 'accepted')",
            (mentee_id,),
        ).fetchone():
            flash("This mentee already has a pending or accepted mentor match.", "error")
        else:
            connection.execute(
                "INSERT INTO mentor_matches (mentor_id, mentee_id, match_status) "
                "VALUES (?, ?, 'pending')",
                (mentor_id, mentee_id),
            )
            connection.commit()
            flash("The mentor match request has been sent.", "success")
        return redirect(url_for("admin_dashboard") + "#matches")

    @app.post("/admin/resources")
    @role_required("admin")
    def admin_create_resource():
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        resource_url = request.form.get("resource_url", "").strip()
        if (
            not title
            or len(title) > 160
            or len(description) > 2000
            or len(category) > 80
            or not resource_url.startswith(("https://", "http://"))
            or len(resource_url) > 2000
        ):
            flash("Add a valid title and an HTTP or HTTPS resource link.", "error")
        else:
            get_db().execute(
                """
                INSERT INTO resources (title, description, category, resource_url, uploaded_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (title, description, category, resource_url, g.user["id"]),
            )
            get_db().commit()
            flash("Learning resource published.", "success")
        return redirect(url_for("admin_dashboard") + "#resources")

    @app.post("/admin/reports/<int:report_id>/close")
    @role_required("admin")
    def close_support_report(report_id):
        result = get_db().execute(
            "UPDATE safeguarding_reports SET status = 'closed' "
            "WHERE id = ? AND status = 'open'",
            (report_id,),
        )
        if result.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("The support report has been marked as reviewed.", "success")
        return redirect(url_for("admin_dashboard") + "#reports")

    @app.post("/admin/contact-requests/<int:contact_request_id>/responded")
    @role_required("admin")
    def mark_contact_request_responded(contact_request_id):
        result = get_db().execute(
            "UPDATE contact_requests SET status = 'responded' "
            "WHERE id = ? AND status = 'pending'",
            (contact_request_id,),
        )
        if result.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("The contact request has been marked as responded.", "success")
        return redirect(url_for("admin_dashboard") + "#contact-requests")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("YMP_HOST", "127.0.0.1"),
        port=int(os.environ.get("YMP_PORT", "5000")),
        debug=os.environ.get("YMP_DEBUG") == "1",
    )
