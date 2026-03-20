# Travyo – Smart Travel & Booking Platform

Full-stack Flask + SQLite web app with:
- User & Admin authentication (separate logins)
- Property listing, search, booking
- Payment simulation (UPI / Credit / Debit Card)
- PDF booking receipt download
- Admin property CRUD (add / edit / delete)
- "Request Us" property listing requests
- Notifications system

## Quick Start (local)

```bash
pip install -r requirements.txt
python app.py
```
Visit http://localhost:5000

## Admin Login
Admin login is at `/admin/login` — **separate from user login**.

Set credentials via environment variables (never hardcoded):
```
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=your-secure-password
```

Default fallback (development only): `admin@travyo.com` / `admin123`

## Deploy on Render

1. Push to GitHub
2. **render.yaml** handles everything automatically
3. In Render dashboard → Environment, set:
   - `ADMIN_EMAIL` → your admin email
   - `ADMIN_PASSWORD` → a strong password (mark as Secret)
   - `SECRET_KEY` → auto-generated

## Key Routes

| Route | Description |
|-------|-------------|
| `/` | Homepage with featured properties |
| `/login` | User login |
| `/signup` | User registration |
| `/admin/login` | Admin-only login (separate page) |
| `/admin` | Admin dashboard |
| `/dashboard` | User dashboard |
| `/post-property` | Submit a property for review |
| `/property/<id>` | Property detail + booking |
| `/booking/<id>/payment` | Payment page (UPI/Card) |
| `/booking/<id>/acknowledgement` | Booking confirmation |
| `/booking/<id>/download-pdf` | Download PDF receipt |
| `/request` | "Request Us" form page |

## Project Structure

```
travyo/
├── app.py                    # All Flask routes + DB + PDF logic
├── requirements.txt          # Flask, Werkzeug, Gunicorn, ReportLab
├── render.yaml               # Render deployment config
├── wsgi.py                   # Gunicorn entrypoint
├── templates/
│   ├── index.html            # Homepage
│   ├── login.html            # User login
│   ├── signup.html           # User registration
│   ├── admin_login.html      # Admin-only login (NEW)
│   ├── userdashboard.html    # User dashboard
│   ├── admindashboard.html   # Admin dashboard
│   ├── post_property.html    # Property submission
│   ├── property_detail.html  # Property detail + booking form
│   ├── payment.html          # Payment page (NEW)
│   ├── acknowledgement.html  # Booking confirmation + PDF link (NEW)
│   └── request_page.html     # Request Us form
└── static/
    ├── unnamed.svg           # Logo
    └── uploads/              # User uploaded images
```
