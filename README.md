# Travyo – Smart Travel Adviser & Property Booking Platform

Full-stack Flask web app with SQLite, session auth, user/admin dashboards.

## Quick Start (local)

```bash
pip install -r requirements.txt
python app.py
```
Visit http://localhost:5000

## Admin Credentials
- Email: admin@travyo.com
- Password: admin123

## Deploy on Render (Free Tier)

1. Push this repo to GitHub
2. Go to https://render.com → New Web Service
3. Connect your repo
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT --timeout 120`
6. Add env var: `SECRET_KEY` → (any long random string)

The `render.yaml` handles all of this automatically if you use "Blueprint" deploy.

## Project Structure
```
travyo/
├── app.py                  # Main Flask application (all routes + DB logic)
├── requirements.txt
├── render.yaml             # Render deployment config
├── wsgi.py                 # Gunicorn entry point
├── travyo.db               # SQLite DB (auto-created on first run)
├── templates/
│   ├── index.html          # Homepage
│   ├── login.html          # Login page
│   ├── signup.html         # Signup page
│   ├── userdashboard.html  # User dashboard
│   ├── admindashboard.html # Admin dashboard
│   ├── post_property.html  # Submit property form
│   ├── property_detail.html# Property detail + booking
│   └── request_page.html   # Public request form
└── static/
    ├── unnamed.svg         # Logo
    └── uploads/            # User-uploaded images
```

## Features
- User registration & login (hashed passwords)
- Role-based access: User / Admin
- Properties: submit, approve, list, delete
- Bookings: create, view
- Notifications system (per-user)
- Admin: manage users, requests, properties, bookings
- AJAX property request form
- Render-ready with gunicorn
