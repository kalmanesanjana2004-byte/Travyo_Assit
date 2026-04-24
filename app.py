"""
Travyo – Full-stack Flask application
Updated with:
  · Separate admin login (env-var credentials, no hardcoded hints in UI)
  · Admin property CRUD  (add / edit / delete)
  · Booking → Payment → Acknowledgement flow (simulated payment)
  · PDF receipt download (reportlab)
  · Request-Us feature (homepage AJAX + admin list)
"""

import io, os, sqlite3, uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g, send_file,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import os

os.makedirs("static/uploads", exist_ok=True)
# ─────────────────────────────────────────────────────────────────────────────
# App configuration
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "travyo-super-secret-key-change-me")

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))

# ── Persistent storage paths ──────────────────────────────────────────────────
# On Render, set DB_PATH=/var/data/travyo.db and UPLOAD_FOLDER=/var/data/uploads
# after attaching a Persistent Disk mounted at /var/data.
# Falls back to local paths so the app still works in dev without any changes.
_DEFAULT_DB_PATH      = os.path.join(BASE_DIR, "travyo.db")
_DEFAULT_UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

DATABASE      = os.environ.get("DB_PATH", _DEFAULT_DB_PATH)
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", _DEFAULT_UPLOAD_FOLDER)
ALLOWED_EXT   = {"png", "jpg", "jpeg", "gif", "webp"}

# Ensure the directory that contains the DB file exists (critical for /var/data)
os.makedirs(os.path.dirname(os.path.abspath(DATABASE)), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "static", "images"), exist_ok=True)
app.config["UPLOAD_FOLDER"]         = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"]    = 16 * 1024 * 1024   # 16 MB

# ── Admin credentials ────────────────────────────────────────────────────────
# Default hardcoded: username=admin / password=admin123
# Override via environment variables for production.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL",    "admin@travyo.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


# ─────────────────────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv  = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    db  = get_db()
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid


# ─────────────────────────────────────────────────────────────────────────────
# Database initialisation
# ─────────────────────────────────────────────────────────────────────────────
def init_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        username   TEXT    NOT NULL UNIQUE,
        name       TEXT    NOT NULL,
        email      TEXT    NOT NULL UNIQUE,
        password   TEXT    NOT NULL,
        role       TEXT    NOT NULL DEFAULT 'user',
        is_active  INTEGER NOT NULL DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS properties (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL,
        location    TEXT    NOT NULL,
        category    TEXT    NOT NULL DEFAULT 'hotel',
        description TEXT,
        price       REAL    NOT NULL DEFAULT 0,
        rating      REAL    NOT NULL DEFAULT 4.0,
        image_url   TEXT,
        status      TEXT    NOT NULL DEFAULT 'pending',
        user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS bookings (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        property_id    INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
        check_in       DATE,
        check_out      DATE,
        guests         INTEGER NOT NULL DEFAULT 1,
        rooms          INTEGER NOT NULL DEFAULT 1,
        total_price    REAL    NOT NULL DEFAULT 0,
        status         TEXT    NOT NULL DEFAULT 'pending_payment',
        payment_method TEXT,
        payment_id     TEXT,
        payment_status TEXT    NOT NULL DEFAULT 'pending',
        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
        message    TEXT    NOT NULL,
        type       TEXT    DEFAULT 'general',
        is_read    INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS requests (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        name         TEXT    NOT NULL,
        email        TEXT    NOT NULL,
        phone        TEXT,
        request_type TEXT    DEFAULT 'property_request',
        subject      TEXT    NOT NULL DEFAULT 'Property Request',
        message      TEXT    NOT NULL,
        status       TEXT    NOT NULL DEFAULT 'pending',
        admin_notes  TEXT,
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS activities (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT NOT NULL,
        icon    TEXT DEFAULT 'fas fa-info-circle',
        time    DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # ── Live-migrate bookings table ───────────────────────────────────────────
    cols = [r[1] for r in db.execute("PRAGMA table_info(bookings)").fetchall()]
    for col, defn in [
        ("payment_method", "TEXT"),
        ("payment_id",     "TEXT"),
        ("payment_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ]:
        if col not in cols:
            db.execute(f"ALTER TABLE bookings ADD COLUMN {col} {defn}")

    # ── Live-migrate users table: add username column if missing ──────────────
    user_cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if "username" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        # Back-fill existing rows with a derived username from email prefix
        for row in db.execute("SELECT id, email FROM users").fetchall():
            base = (row[1].split("@")[0] or "user").lower().replace(".", "_")
            candidate = base
            suffix = 1
            while db.execute(
                "SELECT id FROM users WHERE username=? AND id!=?", (candidate, row[0])
            ).fetchone():
                candidate = f"{base}_{suffix}"
                suffix += 1
            db.execute("UPDATE users SET username=? WHERE id=?", (candidate, row[0]))
        # Create unique index for fast lookups
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)"
        )

    # ── Seed / sync admin account ─────────────────────────────────────────────
    # Hardcoded defaults: username=admin, password=admin123
    # Override via ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD env vars in prod.
    admin = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()
    if not admin:
        db.execute(
            "INSERT OR IGNORE INTO users (username,name,email,password,role) VALUES (?,?,?,?,?)",
            (ADMIN_USERNAME, "Admin", ADMIN_EMAIL,
             generate_password_hash(ADMIN_PASSWORD), "admin"),
        )
        db.execute("INSERT INTO activities (message,icon) VALUES (?,?)",
                   ("Admin account initialised.", "fas fa-user-shield"))
    else:
        # Sync credentials on every restart so env changes take effect
        db.execute(
            "UPDATE users SET username=?,email=?,password=? WHERE role='admin'",
            (ADMIN_USERNAME, ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD)),
        )

    # Seed sample properties on first run
    if db.execute("SELECT COUNT(*) FROM properties WHERE status='approved'").fetchone()[0] == 0:
        for s in [
            ("Sunset Villa Maldives",    "Maldives",               "villa",
             "Stunning over-water villa with private pool.", 350, 4.9,
             "https://images.unsplash.com/photo-1544550581-5f7ceaf7f992?w=800&q=80"),
            ("Tokyo Business Hotel",     "Tokyo, Japan",           "hotel",
             "Modern hotel in the heart of Tokyo.",         120, 4.6,
             "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?w=800&q=80"),
            ("Bali Beach Bungalow",      "Bali, Indonesia",        "resort",
             "Cozy bungalow steps from the ocean.",          95, 4.7,
             "https://images.unsplash.com/photo-1506929562872-bb421503ef21?w=800&q=80"),
            ("Swiss Alps Chalet",        "Interlaken, Switzerland","chalet",
             "Luxury chalet with panoramic mountain views.",280, 4.8,
             "https://images.unsplash.com/photo-1469854523086-cc02fe5d8800?w=800&q=80"),
            ("Dubai Sky Tower",          "Dubai, UAE",             "hotel",
             "High-rise luxury with Burj Khalifa views.",   220, 4.5,
             "https://images.unsplash.com/photo-1512453979798-5ea266f8880c?w=800&q=80"),
            ("Santorini Cliffside Suite","Santorini, Greece",      "villa",
             "Iconic suite overlooking the caldera.",       400, 5.0,
             "https://images.unsplash.com/photo-1570077188670-e3a8d69ac5ff?w=800&q=80"),
        ]:
            db.execute(
                "INSERT INTO properties (name,location,category,description,price,rating,image_url,status)"
                " VALUES (?,?,?,?,?,?,?,?)", (*s, "approved"),
            )

    db.commit()
    db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Auth decorators
# ─────────────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("user_login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Admin access only.", "danger")
            return redirect(url_for("user_login"))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────
def allowed_file(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def get_unread_count(uid):
    r = query_db("SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0",
                 (uid,), one=True)
    return r["c"] if r else 0

def log_activity(msg, icon="fas fa-info-circle"):
    execute_db("INSERT INTO activities (message,icon) VALUES (?,?)", (msg, icon))

def rows_to_dicts(rows):
    return [dict(r) for r in rows]

def parse_dt(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt)
        except ValueError:
            continue
    return None

def enrich_dt(rows, fields):
    for r in rows:
        for f in fields:
            r[f] = parse_dt(r.get(f))
    return rows

def save_upload(field):
    """Save an uploaded image file; return its static URL or empty string."""
    file = request.files.get(field)
    if file and file.filename and allowed_file(file.filename):
        ts       = datetime.now().strftime("%Y%m%d%H%M%S%f")
        filename = f"{ts}_{secure_filename(file.filename)}"
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        return url_for("static", filename=f"uploads/{filename}")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Public routes
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    props    = rows_to_dicts(
        query_db("SELECT * FROM properties WHERE status='approved' ORDER BY rating DESC")
    )
    featured = props[:6]
    return render_template("index.html", all_properties=props, featured_properties=featured)


# ── Unified login (Admin + User — single page) ───────────────────────────────
@app.route("/login", methods=["GET","POST"])
def user_login():
    # Already logged in → redirect appropriately
    if "user_id" in session:
        return redirect(
            url_for("admin_dashboard") if session.get("is_admin") else url_for("dashboard")
        )

    error = None
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()   # username OR email
        pwd        = request.form.get("password", "")

        if not identifier or not pwd:
            error = "Please enter your username/email and password."
        else:
            # ── 1. Try to find the user by username or email ──────────────────
            user = query_db(
                "SELECT * FROM users WHERE username=? OR email=?",
                (identifier, identifier), one=True
            )

            if user and check_password_hash(user["password"], pwd):
                session.clear()
                if user["role"] == "admin":
                    session.update({
                        "user_id":  user["id"],
                        "role":     "admin",
                        "name":     user["name"],
                        "username": user["username"],
                        "is_admin": True,
                    })
                    log_activity("Admin logged in.", "fas fa-user-shield")
                    flash(f"Welcome back, {user['name']}! 👋", "success")
                    return redirect(url_for("admin_dashboard"))
                else:
                    session.update({
                        "user_id":        user["id"],
                        "role":           "user",
                        "name":           user["name"],
                        "username":       user["username"],
                        "user_logged_in": True,
                    })
                    log_activity(f"{user['name']} logged in.", "fas fa-sign-in-alt")
                    flash(f"Welcome back, {user['name']}! 👋", "success")
                    return redirect(url_for("dashboard"))
            else:
                error = "Invalid username / email or password."

    return render_template("login.html", error=error)


@app.route("/signup", methods=["GET","POST"])
def user_register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        name     = request.form.get("name",     "").strip()
        email    = request.form.get("email",    "").strip().lower()
        pwd      = request.form.get("password", "")
        pwd2     = request.form.get("confirm_password", "")

        # ── Validation ────────────────────────────────────────────────────────
        if not username or not name or not email or not pwd or not pwd2:
            error = "All fields are required."
        elif len(username) < 3:
            error = "Username must be at least 3 characters."
        elif not username.replace("_","").replace("-","").isalnum():
            error = "Username may only contain letters, numbers, hyphens and underscores."
        elif pwd != pwd2:
            error = "Passwords do not match."
        elif len(pwd) < 6:
            error = "Password must be at least 6 characters."
        elif query_db("SELECT id FROM users WHERE username=?", (username,), one=True):
            error = "Username already taken. Please choose another."
        elif query_db("SELECT id FROM users WHERE email=?", (email,), one=True):
            error = "Email already registered. Try logging in instead."
        else:
            uid = execute_db(
                "INSERT INTO users (username,name,email,password,role) VALUES (?,?,?,?,?)",
                (username, name, email, generate_password_hash(pwd), "user"),
            )
            execute_db(
                "INSERT INTO notifications (user_id,message,type) VALUES (?,?,?)",
                (uid, f"Welcome to Travyo, {name}! Your account is ready. 🎉", "welcome"),
            )
            log_activity(f"New user registered: {username}.", "fas fa-user-plus")
            session.clear()
            session.update({
                "user_id":        uid,
                "role":           "user",
                "name":           name,
                "username":       username,
                "user_logged_in": True,
            })
            flash(f"Account created! Welcome aboard, {name}! 🎉", "success")
            return redirect(url_for("dashboard"))

    return render_template("signup.html", error=error)


@app.route("/logout")
def user_logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))


# ── Admin login alias (kept for backward-compat; redirects to unified login) ─
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    """Redirect legacy /admin/login URL to the unified login page."""
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("user_login"))


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("user_login"))


# ─────────────────────────────────────────────────────────────────────────────
# User dashboard
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]

    user_bookings = rows_to_dicts(query_db("""
        SELECT b.*, p.name AS property_name, p.location, p.image_url
        FROM bookings b JOIN properties p ON p.id=b.property_id
        WHERE b.user_id=? ORDER BY b.created_at DESC
    """, (uid,)))

    featured = rows_to_dicts(
        query_db("SELECT * FROM properties WHERE status='approved' ORDER BY rating DESC LIMIT 6")
    )
    cats = {r["category"]: r["cnt"] for r in
            query_db("SELECT category,COUNT(*) as cnt FROM properties "
                     "WHERE status='approved' GROUP BY category")}
    notifs = rows_to_dicts(
        query_db("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (uid,))
    )
    enrich_dt(notifs, ["created_at"])
    user_row = query_db("SELECT email FROM users WHERE id=?", (uid,), one=True)

    return render_template(
        "userdashboard.html",
        user_name=session.get("name"),
        user_email=user_row["email"] if user_row else "",
        user_bookings=user_bookings,
        featured_properties=featured,
        category_counts=cats,
        notifications=notifs,
        unread_notifications_count=get_unread_count(uid),
    )


@app.route("/update-profile", methods=["POST"])
@login_required
def update_profile():
    uid   = session["user_id"]
    name  = request.form.get("name","").strip()
    email = request.form.get("email","").strip()
    if name and email:
        execute_db("UPDATE users SET name=?,email=? WHERE id=?", (name, email, uid))
        session["name"] = name
        flash("Profile updated.", "success")
    else:
        flash("Name and email required.", "danger")
    return redirect(url_for("dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Post property  (user → pending)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/post-property", methods=["GET","POST"])
@login_required
def post_property():
    if request.method == "POST":
        uid  = session["user_id"]
        name = request.form.get("name","").strip()
        loc  = request.form.get("location","").strip()
        cat  = request.form.get("category","hotel").strip()
        desc = request.form.get("description","").strip()
        try:    price = float(request.form.get("price","0"))
        except: price = 0.0

        img = save_upload("image") or \
              "https://images.unsplash.com/photo-1560518883-ce09059eeffa?w=800&q=80"

        execute_db(
            "INSERT INTO properties (name,location,category,description,price,image_url,status,user_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (name, loc, cat, desc, price, img, "pending", uid),
        )
        execute_db("INSERT INTO notifications (user_id,message,type) VALUES (?,?,?)",
                   (uid, f"'{name}' submitted for review.", "property_submission"))
        ar = query_db("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)
        if ar:
            execute_db("INSERT INTO notifications (user_id,message,type) VALUES (?,?,?)",
                       (ar["id"], f"New property '{name}' by {session.get('name')}.",
                        "property_submission"))
        log_activity(f"'{name}' submitted by {session.get('name')}.", "fas fa-home")
        flash("Property submitted for review!", "success")
        return redirect(url_for("dashboard"))
    return render_template("post_property.html")


# ─────────────────────────────────────────────────────────────────────────────
# Property detail
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/property/<int:property_id>")
def property_detail(property_id):
    prop = query_db("SELECT * FROM properties WHERE id=? AND status='approved'",
                    (property_id,), one=True)
    if not prop:
        flash("Property not found.", "danger")
        return redirect(url_for("index"))
    return render_template("property_detail.html", property=dict(prop))


# ─────────────────────────────────────────────────────────────────────────────
# Booking → Payment → Acknowledgement
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/property/<int:property_id>/book", methods=["POST"])
@login_required
def book_property(property_id):
    """Create a pending booking then hand off to payment."""
    uid      = session["user_id"]
    check_in = request.form.get("check_in")
    check_out= request.form.get("check_out")
    guests   = int(request.form.get("guests",1))
    rooms    = int(request.form.get("rooms",1))

    prop = query_db("SELECT * FROM properties WHERE id=? AND status='approved'",
                    (property_id,), one=True)
    if not prop:
        flash("Property not found.", "danger")
        return redirect(url_for("index"))

    nights = 1
    try:
        ci = datetime.strptime(check_in,  "%Y-%m-%d")
        co = datetime.strptime(check_out, "%Y-%m-%d")
        nights = max(1, (co - ci).days)
    except Exception:
        pass

    total = prop["price"] * nights * rooms
    bid   = execute_db(
        "INSERT INTO bookings "
        "(user_id,property_id,check_in,check_out,guests,rooms,total_price,status,payment_status)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (uid, property_id, check_in, check_out, guests, rooms, total,
         "pending_payment", "pending"),
    )
    log_activity(f"{session.get('name')} initiated booking #{bid}.", "fas fa-calendar-plus")
    return redirect(url_for("payment_page", booking_id=bid))


@app.route("/booking/<int:booking_id>/payment", methods=["GET","POST"])
@login_required
def payment_page(booking_id):
    uid = session["user_id"]
    booking = query_db("""
        SELECT b.*, p.name AS property_name, p.location, p.image_url, p.category
        FROM bookings b JOIN properties p ON p.id=b.property_id
        WHERE b.id=? AND b.user_id=?
    """, (booking_id, uid), one=True)

    if not booking:
        flash("Booking not found.", "danger")
        return redirect(url_for("dashboard"))

    if booking["status"] == "confirmed":
        return redirect(url_for("acknowledgement", booking_id=booking_id))

    if request.method == "POST":
        method = request.form.get("payment_method","").strip()
        error  = _validate_payment(method, request.form)
        if error:
            return render_template("payment.html", booking=dict(booking), error=error)

        pay_id = f"TXN{uuid.uuid4().hex[:12].upper()}"
        execute_db(
            "UPDATE bookings SET status='confirmed',payment_method=?,"
            "payment_id=?,payment_status='paid' WHERE id=?",
            (method, pay_id, booking_id),
        )
        execute_db("INSERT INTO notifications (user_id,message,type) VALUES (?,?,?)",
                   (uid,
                    f"Payment confirmed for booking #{booking_id} – "
                    f"{booking['property_name']}. TXN: {pay_id}",
                    "booking_confirmation"))
        ar = query_db("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)
        if ar:
            execute_db("INSERT INTO notifications (user_id,message,type) VALUES (?,?,?)",
                       (ar["id"],
                        f"Booking #{booking_id} confirmed by {session.get('name')}.",
                        "booking_confirmation"))
        log_activity(f"Booking #{booking_id} paid via {method}.", "fas fa-check-circle")
        return redirect(url_for("acknowledgement", booking_id=booking_id))

    return render_template("payment.html", booking=dict(booking), error=None)


def _validate_payment(method, form):
    if method not in ("upi","credit_card","debit_card"):
        return "Please select a payment method."
    if method == "upi":
        upi = form.get("upi_id","").strip()
        if not upi or "@" not in upi:
            return "Enter a valid UPI ID (e.g. name@upi)."
    else:
        card_no = form.get("card_number","").replace(" ","")
        expiry  = form.get("expiry","").strip()
        cvv     = form.get("cvv","").strip()
        name    = form.get("card_name","").strip()
        if len(card_no) < 12 or not card_no.isdigit():
            return "Enter a valid card number (12–16 digits)."
        if not expiry or len(expiry) < 4:
            return "Enter a valid expiry date."
        if len(cvv) < 3 or not cvv.isdigit():
            return "Enter a valid CVV (3–4 digits)."
        if not name:
            return "Enter the cardholder name."
    return None


@app.route("/booking/<int:booking_id>/acknowledgement")
@login_required
def acknowledgement(booking_id):
    uid = session["user_id"]
    booking = query_db("""
        SELECT b.*,
               p.name AS property_name, p.location, p.image_url,
               p.category, p.description AS property_description,
               u.name AS user_name, u.email AS user_email
        FROM bookings b
        JOIN properties p ON p.id=b.property_id
        JOIN users      u ON u.id=b.user_id
        WHERE b.id=? AND b.user_id=?
    """, (booking_id, uid), one=True)
    if not booking:
        flash("Booking not found.", "danger")
        return redirect(url_for("dashboard"))
    return render_template("acknowledgement.html", booking=dict(booking))


@app.route("/booking/<int:booking_id>/download-pdf")
@login_required
def download_pdf(booking_id):
    uid = session["user_id"]
    booking = query_db("""
        SELECT b.*,
               p.name AS property_name, p.location, p.category,
               u.name AS user_name, u.email AS user_email
        FROM bookings b
        JOIN properties p ON p.id=b.property_id
        JOIN users      u ON u.id=b.user_id
        WHERE b.id=? AND b.user_id=?
    """, (booking_id, uid), one=True)
    if not booking:
        flash("Booking not found.", "danger")
        return redirect(url_for("dashboard"))
    pdf_bytes = _generate_pdf(dict(booking))
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"travyo_booking_{booking_id}.pdf",
    )


def _generate_pdf(b: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story  = []

    H1  = ParagraphStyle("H1",  fontSize=28, fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#1F3C88"), spaceAfter=4)
    H1b = ParagraphStyle("H1b", fontSize=11, fontName="Helvetica",
                          textColor=colors.HexColor("#ff6b35"), spaceAfter=16)
    H2  = ParagraphStyle("H2",  fontSize=18, fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#222222"), spaceAfter=8)
    SH  = ParagraphStyle("SH",  fontSize=13, fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#1F3C88"), spaceBefore=10, spaceAfter=6)
    FT  = ParagraphStyle("FT",  fontSize=9,  fontName="Helvetica",
                          textColor=colors.HexColor("#888888"))

    story += [
        Paragraph("Travyo", H1),
        Paragraph("Smart Travel Adviser &amp; Booking Platform", H1b),
        HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1F3C88")),
        Spacer(1, .4*cm),
        Paragraph("Booking Confirmation Receipt", H2),
        Spacer(1, .2*cm),
    ]

    # Status badge row
    paid = b.get("payment_status") == "paid"
    badge_col = colors.HexColor("#28a745") if paid else colors.HexColor("#ffc107")
    badge_text = "PAYMENT CONFIRMED" if paid else "PAYMENT PENDING"
    tbl_badge = Table([[badge_text]], colWidths=[16*cm])
    tbl_badge.setStyle(TableStyle([
        ("BACKGROUND",  (0,0),(-1,-1), badge_col),
        ("TEXTCOLOR",   (0,0),(-1,-1), colors.white),
        ("FONTNAME",    (0,0),(-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0),(-1,-1), 12),
        ("ALIGN",       (0,0),(-1,-1), "CENTER"),
        ("TOPPADDING",  (0,0),(-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("ROUNDEDCORNERS", [6]),
    ]))
    story += [tbl_badge, Spacer(1, .3*cm)]

    def section(title, rows):
        story.append(Paragraph(title, SH))
        tbl = Table(rows, colWidths=[6*cm, 10*cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",      (0,0),(0,-1), colors.HexColor("#f0f4ff")),
            ("FONTNAME",        (0,0),(0,-1), "Helvetica-Bold"),
            ("FONTNAME",        (1,0),(1,-1), "Helvetica"),
            ("FONTSIZE",        (0,0),(-1,-1), 10),
            ("TEXTCOLOR",       (0,0),(0,-1), colors.HexColor("#333333")),
            ("TEXTCOLOR",       (1,0),(1,-1), colors.HexColor("#444444")),
            ("ROWBACKGROUNDS",  (0,0),(-1,-1),
             [colors.HexColor("#f9fbff"), colors.white]),
            ("GRID",            (0,0),(-1,-1), .5, colors.HexColor("#dddddd")),
            ("TOPPADDING",      (0,0),(-1,-1), 7),
            ("BOTTOMPADDING",   (0,0),(-1,-1), 7),
            ("LEFTPADDING",     (0,0),(-1,-1), 10),
        ]))
        story.append(tbl)

    section("Booking Details", [
        ["Booking ID",    f"#{b['id']}"],
        ["Status",        (b.get("status") or "").replace("_"," ").title()],
        ["Booking Date",  str(b.get("created_at",""))[:10]],
    ])
    section("Guest Information", [
        ["Guest Name",    b.get("user_name","")],
        ["Email",         b.get("user_email","")],
        ["Guests",        str(b.get("guests",1))],
        ["Rooms",         str(b.get("rooms",1))],
    ])
    section("Property Details", [
        ["Property",      b.get("property_name","")],
        ["Location",      b.get("location","")],
        ["Category",      (b.get("category","") or "").title()],
        ["Check-In",      str(b.get("check_in",""))],
        ["Check-Out",     str(b.get("check_out",""))],
    ])
    section("Payment Summary", [
        ["Total Amount",  f"${float(b.get('total_price',0)):.2f}"],
        ["Payment Method",(b.get("payment_method","") or "—").replace("_"," ").title()],
        ["Transaction ID", b.get("payment_id","—") or "—"],
        ["Payment Status",(b.get("payment_status","pending") or "pending").title()],
    ])

    story += [
        Spacer(1, .6*cm),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dddddd")),
        Spacer(1, .2*cm),
        Paragraph(
            "Thank you for choosing Travyo! Have a wonderful stay. "
            "Support: support@travyo.com", FT),
    ]
    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/notifications/mark-read/<int:nid>", methods=["POST"])
@login_required
def mark_notification_read(nid):
    execute_db("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
               (nid, session["user_id"]))
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/notifications/mark-all-read", methods=["POST"])
def mark_all_notifications_read():
    uid = session.get("user_id")
    if uid:
        execute_db("UPDATE notifications SET is_read=1 WHERE user_id=?", (uid,))
    return redirect(request.referrer or url_for("dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Request Us
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/request")
def request_page():
    return render_template("request_page.html")


@app.route("/request-property", methods=["POST"])
def request_property():
    name  = request.form.get("name","").strip()
    email = request.form.get("email","").strip()
    phone = request.form.get("phone","").strip()
    if not name or not email:
        return jsonify({"status":"error","message":"Name and email are required."})
    uid = session.get("user_id")
    execute_db(
        "INSERT INTO requests (user_id,name,email,phone,request_type,subject,message)"
        " VALUES (?,?,?,?,?,?,?)",
        (uid, name, email, phone, "property_request", "Property Listing Request",
         f"{name} ({email}, {phone or 'N/A'}) wants to list a property."),
    )
    ar = query_db("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)
    if ar:
        execute_db("INSERT INTO notifications (user_id,message,type) VALUES (?,?,?)",
                   (ar["id"], f"New listing request from {name} ({email}).", "request"))
    log_activity(f"Listing request from {name}.", "fas fa-envelope")
    return jsonify({
        "status":  "success",
        "message": "We will contact you and send a separate link to list your property.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Admin dashboard
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin")
@admin_required
def admin_dashboard():
    total_users = query_db(
        "SELECT COUNT(*) as c FROM users WHERE role='user'", one=True
    )["c"]

    # ── New registrations in last 7 days ─────────────────────────────────────
    new_users_week = query_db(
        "SELECT COUNT(*) as c FROM users WHERE role='user' "
        "AND created_at >= DATE('now','-7 days')", one=True
    )["c"]

    # ── Total confirmed bookings ──────────────────────────────────────────────
    total_bookings = query_db(
        "SELECT COUNT(*) as c FROM bookings WHERE status='confirmed'", one=True
    )["c"]

    # ── Total approved properties ─────────────────────────────────────────────
    total_properties = query_db(
        "SELECT COUNT(*) as c FROM properties WHERE status='approved'", one=True
    )["c"]

    pending_raw = rows_to_dicts(query_db("""
        SELECT p.*, u.name AS user_name, u.email AS user_email
        FROM properties p LEFT JOIN users u ON u.id=p.user_id
        WHERE p.status='pending' ORDER BY p.created_at DESC
    """))
    enrich_dt(pending_raw, ["created_at"])

    all_properties = rows_to_dicts(query_db("""
        SELECT p.*, u.name AS user_name
        FROM properties p LEFT JOIN users u ON u.id=p.user_id
        ORDER BY p.created_at DESC
    """))
    enrich_dt(all_properties, ["created_at"])

    recent_users = rows_to_dicts(
        query_db("SELECT * FROM users WHERE role='user' ORDER BY created_at DESC LIMIT 10")
    )
    enrich_dt(recent_users, ["created_at"])

    users = rows_to_dicts(
        query_db("SELECT * FROM users WHERE role='user' ORDER BY created_at DESC")
    )
    enrich_dt(users, ["created_at"])

    bookings = rows_to_dicts(query_db("""
        SELECT b.*, u.name AS user_name, u.email AS user_email, p.name AS property_name
        FROM bookings b
        JOIN users u ON u.id=b.user_id
        JOIN properties p ON p.id=b.property_id
        ORDER BY b.created_at DESC LIMIT 50
    """))

    requests_list = rows_to_dicts(
        query_db("SELECT * FROM requests ORDER BY created_at DESC")
    )
    enrich_dt(requests_list, ["created_at"])

    activities = rows_to_dicts(
        query_db("SELECT * FROM activities ORDER BY time DESC LIMIT 10")
    )
    enrich_dt(activities, ["time"])

    ar = query_db("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)
    notifs, unread = [], 0
    if ar:
        notifs = rows_to_dicts(
            query_db("SELECT * FROM notifications WHERE user_id=? "
                     "ORDER BY created_at DESC LIMIT 30", (ar["id"],))
        )
        enrich_dt(notifs, ["created_at"])
        unread = get_unread_count(ar["id"])

    # Chart data: new user registrations per day (last 14 days)
    chart_raw = query_db("""
        SELECT DATE(created_at) as day, COUNT(*) as cnt
        FROM users
        WHERE role='user' AND created_at >= DATE('now', '-14 days')
        GROUP BY day ORDER BY day ASC
    """)
    chart_labels = [r["day"] for r in chart_raw]
    chart_values = [r["cnt"] for r in chart_raw]

    return render_template(
        "admindashboard.html",
        total_users=total_users,
        new_users_week=new_users_week,
        total_bookings=total_bookings,
        total_properties=total_properties,
        pending_properties=pending_raw,
        all_properties=all_properties,
        recent_users=recent_users,
        users=users,
        bookings=bookings,
        requests=requests_list,
        recent_activities=activities,
        notifications=notifs,
        unread_notifications_count=unread,
        chart_labels=chart_labels,
        chart_values=chart_values,
    )



# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin/property/add", methods=["POST"])
@admin_required
def admin_add_property():
    name = (request.form.get("property_name") or request.form.get("name","")).strip()
    loc  = (request.form.get("property_location") or request.form.get("location","")).strip()
    cat  = (request.form.get("property_category") or request.form.get("category","hotel")).strip()
    desc = (request.form.get("property_description") or request.form.get("description","")).strip()
    try:
        price  = float(request.form.get("property_price")  or request.form.get("price","0"))
        rating = float(request.form.get("property_rating") or request.form.get("rating","4.0"))
    except ValueError:
        price, rating = 0.0, 4.0

    img = ""
    for field in ("property_images","images","image"):
        img = save_upload(field)
        if img: break
    if not img:
        img = (request.form.get("image_url") or request.form.get("image","")).strip()
    if not img:
        img = "https://images.unsplash.com/photo-1544550581-5f7ceaf7f992?w=800&q=80"

    if not name or not loc:
        flash("Name and location are required.", "danger")
        return redirect(url_for("admin_dashboard"))

    execute_db(
        "INSERT INTO properties (name,location,category,description,price,rating,image_url,status)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (name, loc, cat, desc, price, rating, img, "approved"),
    )
    log_activity(f"Admin added '{name}'.", "fas fa-plus-circle")
    flash(f"Property '{name}' added.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/property/<int:pid>/edit", methods=["POST"])
@admin_required
def admin_edit_property(pid):
    prop = query_db("SELECT * FROM properties WHERE id=?", (pid,), one=True)
    if not prop:
        flash("Property not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    name = request.form.get("name", prop["name"]).strip()
    loc  = request.form.get("location", prop["location"]).strip()
    cat  = request.form.get("category", prop["category"]).strip()
    desc = request.form.get("description", prop["description"] or "").strip()
    try:
        price  = float(request.form.get("price",  prop["price"]))
        rating = float(request.form.get("rating", prop["rating"]))
    except ValueError:
        price, rating = prop["price"], prop["rating"]

    img = save_upload("image") or prop["image_url"] or ""

    execute_db(
        "UPDATE properties SET name=?,location=?,category=?,description=?,"
        "price=?,rating=?,image_url=? WHERE id=?",
        (name, loc, cat, desc, price, rating, img, pid),
    )
    log_activity(f"Admin edited '{name}'.", "fas fa-edit")
    flash(f"Property '{name}' updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/property/<int:pid>/approve", methods=["POST"])
@admin_required
def approve_property(pid):
    prop = query_db("SELECT * FROM properties WHERE id=?", (pid,), one=True)
    if prop:
        execute_db("UPDATE properties SET status='approved' WHERE id=?", (pid,))
        if prop["user_id"]:
            execute_db("INSERT INTO notifications (user_id,message,type) VALUES (?,?,?)",
                       (prop["user_id"],
                        f"Your property '{prop['name']}' is now live!",
                        "property_approved"))
        log_activity(f"'{prop['name']}' approved.", "fas fa-check-circle")
        flash(f"'{prop['name']}' approved.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/property/<int:pid>/delete", methods=["POST"])
@admin_required
def delete_property(pid):
    prop = query_db("SELECT name FROM properties WHERE id=?", (pid,), one=True)
    if prop:
        execute_db("DELETE FROM properties WHERE id=?", (pid,))
        log_activity(f"Property '{prop['name']}' deleted.", "fas fa-trash")
        flash(f"'{prop['name']}' deleted.", "success")
    return redirect(url_for("admin_dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Admin – user management
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin/user/<int:uid>/delete", methods=["POST"])
@admin_required
def delete_user(uid):
    user = query_db("SELECT name FROM users WHERE id=?", (uid,), one=True)
    if user:
        execute_db("DELETE FROM users WHERE id=?", (uid,))
        log_activity(f"User '{user['name']}' deleted.", "fas fa-user-times")
        flash(f"User '{user['name']}' deleted.", "success")
    return redirect(url_for("admin_dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Admin – request management
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin/request/<int:rid>/status", methods=["POST"])
@admin_required
def update_request_status(rid):
    execute_db("UPDATE requests SET status=? WHERE id=?",
               (request.form.get("status","pending"), rid))
    flash("Status updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/request/<int:rid>/reply", methods=["POST"])
@admin_required
def reply_request(rid):
    reply = request.form.get("reply","").strip()
    req   = query_db("SELECT * FROM requests WHERE id=?", (rid,), one=True)
    if req and reply:
        execute_db("UPDATE requests SET admin_notes=?,status='in_progress' WHERE id=?",
                   (reply, rid))
        if req["user_id"]:
            execute_db("INSERT INTO notifications (user_id,message,type) VALUES (?,?,?)",
                       (req["user_id"], f"Admin replied: {reply[:120]}", "admin_reply"))
        flash("Reply sent.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/request/<int:rid>/delete", methods=["POST"])
@admin_required
def delete_request(rid):
    execute_db("DELETE FROM requests WHERE id=?", (rid,))
    flash("Request deleted.", "success")
    return redirect(url_for("admin_dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# AI Chatbot – keyword-based Q&A (no external API required)
# ─────────────────────────────────────────────────────────────────────────────
CHATBOT_QA = [
    {
        "keywords": ["book", "booking", "reserve", "reservation", "how to book"],
        "answer": (
            "📋 <strong>How to Book:</strong><br>"
            "1. Browse properties on the home page.<br>"
            "2. Click a property to open its detail page.<br>"
            "3. Choose your check-in / check-out dates, guests & rooms.<br>"
            "4. Click <em>Confirm Booking</em>.<br>"
            "5. Complete payment (UPI / Credit Card / Debit Card).<br>"
            "6. You'll receive a PDF receipt instantly! ✅"
        ),
    },
    {
        "keywords": ["payment", "pay", "upi", "credit card", "debit card", "method"],
        "answer": (
            "💳 <strong>Payment Methods:</strong><br>"
            "• <strong>UPI</strong> – Enter your UPI ID (e.g. name@paytm)<br>"
            "• <strong>Credit Card</strong> – Visa, Mastercard accepted<br>"
            "• <strong>Debit Card</strong> – All major banks supported<br><br>"
            "All payments are SSL-encrypted and fully secure. 🔒"
        ),
    },
    {
        "keywords": ["list", "post property", "add property", "host", "rent out", "landlord"],
        "answer": (
            "🏠 <strong>List Your Property:</strong><br>"
            "1. Sign up / log in to your account.<br>"
            "2. Go to your <em>Dashboard</em> → <em>Post Property</em>.<br>"
            "3. Fill in name, location, category, price & upload a photo.<br>"
            "4. Submit for review – our team approves within 24–48 hours.<br><br>"
            "You can also use the <a href='/request-us' style='color:#1F3C88'>Request Us</a> form for assistance! 📬"
        ),
    },
    {
        "keywords": ["confirm", "confirmation", "receipt", "pdf", "download"],
        "answer": (
            "📄 <strong>Booking Confirmation:</strong><br>"
            "After payment you will see a confirmation page with your Booking ID.<br>"
            "You can download a PDF receipt anytime from your Dashboard → Bookings. ✅"
        ),
    },
    {
        "keywords": ["cancel", "cancellation", "refund"],
        "answer": (
            "❌ <strong>Cancellations & Refunds:</strong><br>"
            "To cancel a booking, go to Dashboard → My Bookings and contact support via the Request form.<br>"
            "Refund timelines depend on the payment method (usually 3–7 business days)."
        ),
    },
    {
        "keywords": ["contact", "support", "help", "email", "phone", "reach"],
        "answer": (
            "📞 <strong>Contact Support:</strong><br>"
            "• Use the <a href='/request-us' style='color:#1F3C88'>Request Us</a> form on our website.<br>"
            "• Or email us at <strong>support@travyo.com</strong><br>"
            "• Our team responds within 24 hours. We're happy to help! 😊"
        ),
    },
    {
        "keywords": ["sign up", "register", "account", "create account"],
        "answer": (
            "👤 <strong>Create an Account:</strong><br>"
            "Click <em>Sign Up</em> in the top navigation, enter your name, email & password.<br>"
            "That's it – you'll be logged in immediately and ready to explore! 🎉"
        ),
    },
    {
        "keywords": ["price", "cost", "cheap", "affordable", "expensive", "rate"],
        "answer": (
            "💰 <strong>Pricing:</strong><br>"
            "Property prices are listed as <em>USD per night</em>.<br>"
            "The total cost is automatically calculated based on your dates, number of rooms & guests.<br>"
            "Filter by category to find options in your budget! 🔍"
        ),
    },
    {
        "keywords": ["hello", "hi", "hey", "good morning", "good afternoon", "good evening", "greet"],
        "answer": (
            "👋 <strong>Hello! Welcome to Travyo!</strong><br>"
            "I'm your travel assistant. Ask me about:<br>"
            "• How to book a property<br>"
            "• Payment options<br>"
            "• Listing your property<br>"
            "• Cancellations &amp; support<br>"
            "What can I help you with today? 😊"
        ),
    },
]
@app.route('/ping')
def ping():
    return "OK", 200
@app.route("/chatbot", methods=["POST"])
def chatbot():
    """Keyword-based chatbot endpoint. Returns a JSON response."""
    data    = request.get_json(silent=True) or {}
    msg     = (data.get("message") or "").strip().lower()

    if not msg:
        return jsonify({"reply": "Please type a message so I can help you! 😊"})

    # Match keywords
    for qa in CHATBOT_QA:
        if any(kw in msg for kw in qa["keywords"]):
            return jsonify({"reply": qa["answer"]})

    # Fallback
    return jsonify({
        "reply": (
            "🤔 I'm not sure about that, but I'm here to help!<br>"
            "Try asking about: <em>booking, payments, listing a property, "
            "cancellations, or support</em>. 😊"
        )
    })


# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────
with app.app_context():
    # Printed to Render's log stream — confirms which DB path is active
    import logging
    logging.basicConfig(level=logging.INFO)
    logging.info("[Travyo] Using database  : %s", DATABASE)
    logging.info("[Travyo] Using uploads at: %s", UPLOAD_FOLDER)
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
