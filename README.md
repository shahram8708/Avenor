# Avenor Pre-Launch Website

Production-grade Flask pre-launch website for validating demand for an AI Automation Agent Platform.

## Features

1. Full landing page with 54 categories and 481 automation services
2. Waitlist registration with first name, last name, email, role, and industry
3. SQLite persistence using Python built-in sqlite3
4. Live waitlist counter API and animated frontend counters
5. Admin authentication and analytics dashboard
6. CSV export of all waitlist registrants

## Stack

1. Python 3.10+
2. Flask 3.x with Jinja2 templates
3. Bootstrap 5.3 via CDN
4. Vanilla JavaScript
5. SQLite

## Setup

```bash
# 1. Clone or download the project
cd prelaunch/

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and set SECRET_KEY, ADMIN_EMAIL, and ADMIN_PASSWORD

# 5. Run the application
python app.py

# 6. Open in browser
# http://localhost:5000          <- Landing page
# http://localhost:5000/admin    <- Admin dashboard
```

## Public Routes

1. `GET /` landing page
2. `POST /register` waitlist registration
3. `GET /success` success page
4. `GET /api/waitlist-count` JSON waitlist count

## Admin Routes

1. `GET /admin/login`
2. `POST /admin/login`
3. `GET /admin/dashboard`
4. `GET /admin/export/csv`
5. `GET /admin/logout`

## Security Notes

1. Input sanitization with bleach
2. Parameterized SQL queries only
3. Admin password comparison with constant-time check
4. HttpOnly and SameSite session cookie settings
5. Anonymized IP logging for analytics
6. Graceful duplicate email handling

## Database

`prelaunch.db` is auto-created on first application start. Tables are initialized automatically from `database.py`.

On startup, the app also auto-creates an `admin` role and seeds/updates the admin account from `.env`.
