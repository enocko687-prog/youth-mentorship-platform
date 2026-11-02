# Youth Mentorship Platform

YMP is a Flask web application with a responsive interface, account registration and sign-in, role-specific dashboards, and a shared SQLite database.

## Run locally

1. Install Python 3.10 or later.
2. From this folder, create and install the project environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Start the server:

   ```powershell
   .\.venv\Scripts\python.exe server.py
   ```

4. Open `http://127.0.0.1:5000` in your browser and create an account.

The database is created as `youth_mentorship.db` in this folder the first time the app starts. Existing databases receive additive schema updates on startup; existing records are kept. For local use, the app generates a persistent session key under `instance/`.

## Deploy to Render

This repository includes a Render Blueprint in `render.yaml`. To deploy it:

1. Push this project to a GitHub repository.
2. Sign in to Render and connect your GitHub account.
3. In the Render dashboard, choose **New + → Blueprint**, select this repository and its deployment branch, then apply the Blueprint.
4. Wait for the `youth-mentorship-platform` service to finish deploying, then open its Render URL.

The included Blueprint uses Render's free plan and stores its SQLite database and uploads under `/tmp`. That storage is temporary and can be cleared when the service restarts or redeploys. Do not use this configuration for real participant data; use persistent storage and backups before launch.

## Deploy to PythonAnywhere

For a PythonAnywhere deployment, you need a PythonAnywhere account and a GitHub repository containing the project. A public repository is simplest to clone; for a private repository, configure GitHub SSH access from PythonAnywhere first. The `.gitignore` excludes the local database, virtual environment, and session key; do not upload these files or commit secrets.

1. Push the project to a GitHub repository. A private repository is recommended.
2. In PythonAnywhere, create a web app using **Manual configuration** and a Python version 3.10 or later.
3. Open a PythonAnywhere Bash console and clone your repository, then create a virtual environment and install dependencies. Replace the example account and repository names:

   ```bash
   cd /home/<username>
   git clone https://github.com/<github-name>/<repository>.git ymp
   cd ymp
   mkvirtualenv --python=/usr/bin/python3.13 ymp
   pip install -r requirements.txt
   ```

   If Python 3.13 is not available for your account, use the path for another available Python 3.10+ interpreter consistently for the web app and virtual environment.
4. In the PythonAnywhere **Web** tab, set the web app's virtualenv to `/home/<username>/.virtualenvs/ymp`. Open its WSGI configuration file, replace the example values below, and save it:

   ```python
   import os
   import sys

   project_home = "/home/<username>/ymp"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   os.environ["YMP_ENV"] = "production"
   os.environ["YMP_SECRET_KEY"] = "<paste-a-new-random-secret-here>"
   os.environ["YMP_DATABASE_PATH"] = "/home/<username>/youth_mentorship.db"
   os.environ["YMP_UPLOAD_FOLDER"] = "/home/<username>/ymp_uploads"
   os.environ["YMP_COOKIE_SECURE"] = "1"

   from server import app as application
   ```

   Generate the secret in a local terminal with `python -c "import secrets; print(secrets.token_hex(32))"`. Paste it only into the private PythonAnywhere WSGI file—never into GitHub or this README. The database and uploads paths are outside the cloned repository so redeploying code does not overwrite them.
5. In the Web tab, click **Reload** and open the site URL to check the homepage and registration page.
6. Create the first administrator in a Bash console. Use the same secret and database path configured in the WSGI file:

   ```bash
   cd /home/<username>/ymp
   export YMP_ENV=production
   export YMP_SECRET_KEY='<the-same-random-secret>'
   export YMP_DATABASE_PATH=/home/<username>/youth_mentorship.db
   /home/<username>/.virtualenvs/ymp/bin/python -m flask --app server create-admin
   ```

Production mode refuses to start without `YMP_SECRET_KEY`. Keep the database on your account's persistent storage, back it up regularly, and do not run multiple app instances against the same SQLite file. The built-in Flask server is for local development only. See the [PythonAnywhere Flask deployment guide](https://help.pythonanywhere.com/pages/Flask/) for provider-specific setup details.

## Current functionality

- Mentee account registration, sign-in, and sign-out.
- Mentor registration, profile completion, admin review, mentee match requests, and mentor acceptance.
- CSRF protection for forms and session-protected dashboard access.
- Mentee goal progress and action-plan management, mentorship session requests and confirmations, resource viewing, feedback, and safeguarding reports.
- Admin tools to approve mentors, match members, publish learning resources, and review support reports.
- Dashboard counts sourced from goals, action plans, bookings, and resources in the shared database.
- Public contact form with an admin inbox, direct email/phone/WhatsApp follow-up links, and responded status.
- Mentees can send goal-related work as notes, an HTTP/HTTPS link, or a PDF/JPG/PNG attachment (up to 5 MB); only their accepted mentor can review it and return feedback.
- Uploaded work is private to the submitting mentee and their currently matched mentor. Configure `YMP_UPLOAD_FOLDER` on a persistent disk for deployments where uploaded files must survive restarts.
- Responsive landing page and dashboard with the custom YMP logo.
- Public programme phone defaults to `+254715552003`; set `YMP_PUBLIC_CONTACT_PHONE` to change it. WhatsApp contact links are generated from that number.
- Public Facebook, YouTube, LinkedIn, and GitHub links are shown in the homepage contact section and footer. Override them with `YMP_FACEBOOK_URL`, `YMP_YOUTUBE_URL`, `YMP_LINKEDIN_URL`, and `YMP_GITHUB_URL`.

Password recovery and email verification still need a configured email provider and are not enabled.

Create the first administrator from this folder with `python -m flask --app server create-admin`. The command prompts for a name, email and a password of at least 12 characters. Administrator accounts must not be created through public registration.

Run the focused checks with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
