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

To require a shared access password before visitors can view the platform, run this once from the project folder and enter a passphrase of 12–128 characters twice when prompted:

```powershell
.\.venv\Scripts\python.exe -m flask --app server configure-access-password
```

Restart the app after setting or changing the password, then send the passphrase to the people you want to let in. The app stores only a password hash in the ignored `instance/` folder. Visitors enter and confirm the shared password on the access page; their browser session is then allowed through the gate. Anyone with this shared password gets the same site access, so use individual accounts for personal identity and never share an administrator password.

## Deploy to PythonAnywhere

The app is prepared for deployment, but is not yet published. You need a PythonAnywhere account and a GitHub repository containing the project. A public repository is simplest to clone; for a private repository, configure GitHub SSH access from PythonAnywhere first. The `.gitignore` excludes the local database, virtual environment, and session key; do not upload these files or commit secrets.

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

To enable the shared access gate on PythonAnywhere, run `/home/<username>/.virtualenvs/ymp/bin/python -m flask --app server configure-access-password` in a Bash console, then reload the web app. The password hash is saved under the project `instance/` directory, which must be writable and persistent.

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
- Public programme phone defaults to `0715552003`; set `YMP_PUBLIC_CONTACT_PHONE` to change it. Call and SMS links use the configured number, while local Kenyan numbers are converted to international format for WhatsApp and Twilio.
- Optional Twilio SMS alerts notify the programme phone when a public contact request is submitted. Set `YMP_TWILIO_ACCOUNT_SID`, `YMP_TWILIO_AUTH_TOKEN`, and `YMP_TWILIO_FROM_PHONE` together to enable alerts; `YMP_CONTACT_ALERT_PHONE` can override the destination and otherwise defaults to `YMP_PUBLIC_CONTACT_PHONE`. Use E.164 phone numbers (for example, `+254715552003`). Configure these as private environment variables in the hosting provider, never in source control. Create a Twilio account and SMS-enabled sender number first; trial accounts may only send to verified recipient numbers. If delivery fails, the request remains saved in the admin inbox and the site reports the alert failure.
- Public Facebook, YouTube, LinkedIn, and GitHub links are shown in the homepage contact section and footer. Override them with `YMP_FACEBOOK_URL`, `YMP_YOUTUBE_URL`, `YMP_LINKEDIN_URL`, and `YMP_GITHUB_URL`.

Password recovery and email verification still need a configured email provider and are not enabled.

Create the first administrator from this folder with `python -m flask --app server create-admin`. The command prompts for a name, email and a password of at least 12 characters. Administrator accounts must not be created through public registration.

Run the focused checks with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
