import os
import sqlite3
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ─────────────────────────────────────────────────────────────────────────────
# App Configuration
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "travyo-super-secret-key-2024")

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DATABASE      = os.path.join(BASE_DIR, "travyo.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024   # 16 MB


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
    db = get_db()
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid


# ─────────────────────────────────────────────────────────────────────────────
# Database initialisation (runs once at startup)
# ─────────────────────────────────────────────────────────────────────────────
def init_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL,
        email       TEXT    NOT NULL UNIQUE,
        password    TEXT    NOT NULL,
        role        TEXT    NOT NULL DEFAULT 'user',
        is_active   INTEGER NOT NULL DEFAULT 1,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
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
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        property_id  INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
        check_in     DATE,
        check_out    DATE,
        guests       INTEGER NOT NULL DEFAULT 1,
        rooms        INTEGER NOT NULL DEFAULT 1,
        total_price  REAL    NOT NULL DEFAULT 0,
        status       TEXT    NOT NULL DEFAULT 'confirmed',
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS notifications (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
        message     TEXT    NOT NULL,
        type        TEXT    DEFAULT 'general',
        is_read     INTEGER NOT NULL DEFAULT 0,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
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
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        message    TEXT NOT NULL,
        icon       TEXT DEFAULT 'fas fa-info-circle',
        time       DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Seed admin account
    admin = db.execute("SELECT id FROM users WHERE email='admin@travyo.com'").fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
            ("Admin User", "admin@travyo.com",
             generate_password_hash("admin123"), "admin")
        )
        db.execute(
            "INSERT INTO activities (message, icon) VALUES (?, ?)",
            ("Admin account created.", "fas fa-user-shield")
        )

    # Seed sample approved properties
    cnt = db.execute("SELECT COUNT(*) FROM properties WHERE status='approved'").fetchone()[0]
    if cnt == 0:
        sample = [
            ("Sunset Villa Maldives", "Maldives", "villa",
             "Stunning over-water villa with private pool.",
             350, 4.9,
             "https://images.unsplash.com/photo-1544550581-5f7ceaf7f992?w=800&q=80"),
            ("Tokyo Business Hotel", "Tokyo, Japan", "hotel",
             "Modern hotel in the heart of Tokyo.",
             120, 4.6,
             "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?w=800&q=80"),
            ("Bali Beach Bungalow", "Bali, Indonesia", "resort",
             "Cozy bungalow steps from the ocean.",
             95, 4.7,
             "https://images.unsplash.com/photo-1506929562872-bb421503ef21?w=800&q=80"),
            ("Swiss Alps Chalet", "Interlaken, Switzerland", "chalet",
             "Luxury chalet with panoramic mountain views.",
             280, 4.8,
             "https://images.unsplash.com/photo-1469854523086-cc02fe5d8800?w=800&q=80"),
            ("Dubai Sky Tower", "Dubai, UAE", "hotel",
             "High-rise luxury with Burj Khalifa views.",
             220, 4.5,
             "https://images.unsplash.com/photo-1512453979798-5ea266f8880c?w=800&q=80"),
            ("Santorini Cliffside Suite", "Santorini, Greece", "villa",
             "Iconic white-and-blue suite overlooking the caldera.",
             400, 5.0,
             "https://images.unsplash.com/photo-1570077188670-e3a8d69ac5ff?w=800&q=80"),
        ]
        for s in sample:
            db.execute(
                "INSERT INTO properties (name, location, category, description, price, rating, image_url, status)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (*s, "approved")
            )
        db.execute("INSERT INTO activities (message, icon) VALUES (?, ?)",
                   ("Sample properties seeded.", "fas fa-building"))

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
        if "user_id" not in session or session.get("role") != "admin":
            flash("Admin access only.", "danger")
            return redirect(url_for("user_login"))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def get_unread_count(user_id):
    row = query_db(
        "SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0",
        (user_id,), one=True
    )
    return row["c"] if row else 0

def log_activity(message, icon="fas fa-info-circle"):
    execute_db("INSERT INTO activities (message, icon) VALUES (?,?)", (message, icon))

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


# ─────────────────────────────────────────────────────────────────────────────
# Public routes
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    all_properties = rows_to_dicts(
        query_db("SELECT * FROM properties WHERE status='approved' ORDER BY created_at DESC")
    )
    return render_template("index.html", all_properties=all_properties)

@app.route("/login", methods=["GET", "POST"])
def user_login():
    if "user_id" in session:
        return redirect(url_for("dashboard") if session.get("role") == "user"
                        else url_for("admin_dashboard"))

    error = None
    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user     = query_db("SELECT * FROM users WHERE email=?", (email,), one=True)

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"]       = user["id"]
            session["role"]          = user["role"]
            session["name"]          = user["name"]
            session["user_logged_in"] = True
            log_activity(f"{user['name']} logged in.", "fas fa-sign-in-alt")
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))
        error = "Invalid email or password."

    return render_template("login.html", error=error)

@app.route("/signup", methods=["GET", "POST"])
def user_register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not name or not email or not password:
            error = "All fields are required."
        else:
            existing = query_db("SELECT id FROM users WHERE email=?", (email,), one=True)
            if existing:
                error = "Email already registered. Please log in."
            else:
                hashed  = generate_password_hash(password)
                user_id = execute_db(
                    "INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                    (name, email, hashed, "user")
                )
                execute_db(
                    "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
                    (user_id, f"Welcome to Travyo, {name}! Your account is ready.", "welcome")
                )
                log_activity(f"New user registered: {name}.", "fas fa-user-plus")

                session.clear()
                session["user_id"]        = user_id
                session["role"]           = "user"
                session["name"]           = name
                session["user_logged_in"] = True
                flash("Account created successfully!", "success")
                return redirect(url_for("dashboard"))

    return render_template("signup.html", error=error)

@app.route("/logout")
def user_logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))

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
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))

    uid = session["user_id"]

    bookings_raw = rows_to_dicts(query_db("""
        SELECT b.*, p.name AS property_name
        FROM bookings b
        JOIN properties p ON p.id = b.property_id
        WHERE b.user_id = ?
        ORDER BY b.created_at DESC
    """, (uid,)))

    featured_properties = rows_to_dicts(
        query_db("SELECT * FROM properties WHERE status='approved' ORDER BY rating DESC LIMIT 6")
    )

    cats_raw = query_db(
        "SELECT category, COUNT(*) as cnt FROM properties WHERE status='approved' GROUP BY category"
    )
    category_counts = {r["category"]: r["cnt"] for r in cats_raw}

    notifs = rows_to_dicts(
        query_db("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (uid,))
    )
    enrich_dt(notifs, ["created_at"])
    unread = get_unread_count(uid)

    user_row = query_db("SELECT email FROM users WHERE id=?", (uid,), one=True)
    user_email = user_row["email"] if user_row else ""

    return render_template(
        "userdashboard.html",
        user_name=session.get("name"),
        user_email=user_email,
        user_bookings=bookings_raw,
        featured_properties=featured_properties,
        category_counts=category_counts,
        notifications=notifs,
        unread_notifications_count=unread,
    )

@app.route("/update-profile", methods=["POST"])
@login_required
def update_profile():
    uid   = session["user_id"]
    name  = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    if name and email:
        execute_db("UPDATE users SET name=?, email=? WHERE id=?", (name, email, uid))
        session["name"] = name
        flash("Profile updated.", "success")
    else:
        flash("Name and email are required.", "danger")
    return redirect(url_for("dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Post property (user)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/post-property", methods=["GET", "POST"])
@login_required
def post_property():
    if request.method == "POST":
        uid         = session["user_id"]
        name        = request.form.get("name", "").strip()
        location    = request.form.get("location", "").strip()
        category    = request.form.get("category", "hotel").strip()
        description = request.form.get("description", "").strip()
        try:
            price = float(request.form.get("price", "0"))
        except ValueError:
            price = 0.0

        image_url = ""
        if "image" in request.files:
            file = request.files["image"]
            if file and file.filename and allowed_file(file.filename):
                ts       = datetime.now().strftime("%Y%m%d%H%M%S%f")
                filename = f"{ts}_{secure_filename(file.filename)}"
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                image_url = url_for("static", filename=f"uploads/{filename}")

        if not image_url:
            image_url = "https://images.unsplash.com/photo-1560518883-ce09059eeffa?w=800&q=80"

        execute_db(
            "INSERT INTO properties (name, location, category, description, price, image_url, status, user_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (name, location, category, description, price, image_url, "pending", uid)
        )
        execute_db(
            "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
            (uid,
             f"Your property '{name}' has been submitted for review.",
             "property_submission")
        )
        admin_row = query_db("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)
        if admin_row:
            execute_db(
                "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
                (admin_row["id"],
                 f"New property submitted: '{name}' by {session.get('name')}.",
                 "property_submission")
            )
        log_activity(f"Property '{name}' submitted by {session.get('name')}.", "fas fa-home")
        flash("Property submitted for review!", "success")
        return redirect(url_for("dashboard"))

    return render_template("post_property.html")


# ─────────────────────────────────────────────────────────────────────────────
# Property detail & booking
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/property/<int:property_id>")
def property_detail(property_id):
    prop = query_db(
        "SELECT * FROM properties WHERE id=? AND status='approved'",
        (property_id,), one=True
    )
    if not prop:
        flash("Property not found.", "danger")
        return redirect(url_for("index"))
    return render_template("property_detail.html", property=dict(prop))

@app.route("/property/<int:property_id>/book", methods=["POST"])
@login_required
def book_property(property_id):
    uid       = session["user_id"]
    check_in  = request.form.get("check_in")
    check_out = request.form.get("check_out")
    guests    = int(request.form.get("guests", 1))
    rooms     = int(request.form.get("rooms", 1))

    prop = query_db(
        "SELECT * FROM properties WHERE id=? AND status='approved'", (property_id,), one=True
    )
    if not prop:
        flash("Property not found.", "danger")
        return redirect(url_for("index"))

    nights = 1
    try:
        ci     = datetime.strptime(check_in, "%Y-%m-%d")
        co     = datetime.strptime(check_out, "%Y-%m-%d")
        nights = max(1, (co - ci).days)
    except Exception:
        pass

    total = prop["price"] * nights * rooms
    execute_db(
        "INSERT INTO bookings (user_id, property_id, check_in, check_out, guests, rooms, total_price, status)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (uid, property_id, check_in, check_out, guests, rooms, total, "confirmed")
    )
    execute_db(
        "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
        (uid,
         f"Booking confirmed for '{prop['name']}' ({check_in} → {check_out}). Total: ${total:.2f}",
         "booking_confirmation")
    )
    log_activity(f"{session.get('name')} booked '{prop['name']}'.", "fas fa-calendar-check")
    flash("Booking confirmed!", "success")
    return redirect(url_for("dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Request page & AJAX endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/request")
def request_page():
    return render_template("request_page.html")

@app.route("/request-property", methods=["POST"])
def request_property():
    name  = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()

    if not name or not email:
        return jsonify({"status": "error", "message": "Name and email are required."})

    uid = session.get("user_id")
    execute_db(
        "INSERT INTO requests (user_id, name, email, phone, request_type, subject, message)"
        " VALUES (?,?,?,?,?,?,?)",
        (uid, name, email, phone, "property_request",
         "Property Listing Request",
         f"User {name} ({email}, {phone}) wants to list a property.")
    )
    admin_row = query_db("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)
    if admin_row:
        execute_db(
            "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
            (admin_row["id"],
             f"New property listing request from {name} ({email}).",
             "request")
        )
    log_activity(f"Property request from {name}.", "fas fa-envelope")
    return jsonify({"status": "success",
                    "message": "Request submitted! We'll contact you shortly."})


# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/notifications/mark-read/<int:notification_id>", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    execute_db(
        "UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
        (notification_id, session["user_id"])
    )
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/notifications/mark-all-read", methods=["POST"])
def mark_all_notifications_read():
    uid = session.get("user_id")
    if uid:
        execute_db("UPDATE notifications SET is_read=1 WHERE user_id=?", (uid,))
    return redirect(request.referrer or url_for("dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Admin dashboard
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin")
@admin_required
def admin_dashboard():
    total_users = query_db("SELECT COUNT(*) as c FROM users WHERE role='user'", one=True)["c"]

    pending_raw = rows_to_dicts(query_db("""
        SELECT p.*, u.name AS user_name, u.email AS user_email
        FROM properties p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.status = 'pending'
        ORDER BY p.created_at DESC
    """))
    enrich_dt(pending_raw, ["created_at"])

    all_properties = rows_to_dicts(query_db("""
        SELECT p.*, u.name AS user_name
        FROM properties p
        LEFT JOIN users u ON u.id = p.user_id
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
        JOIN users u ON u.id = b.user_id
        JOIN properties p ON p.id = b.property_id
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

    admin_row = query_db("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)
    notifs, unread = [], 0
    if admin_row:
        notifs = rows_to_dicts(
            query_db("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
                     (admin_row["id"],))
        )
        enrich_dt(notifs, ["created_at"])
        unread = get_unread_count(admin_row["id"])

    return render_template(
        "admindashboard.html",
        total_users=total_users,
        pending_properties=pending_raw,
        all_properties=all_properties,
        recent_users=recent_users,
        users=users,
        bookings=bookings,
        requests=requests_list,
        recent_activities=activities,
        notifications=notifs,
        unread_notifications_count=unread,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Admin – property management
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin/property/add", methods=["POST"])
@admin_required
def admin_add_property():
    # Accept both `property_name` and `name` form fields (template uses `name`)
    name        = (request.form.get("property_name") or request.form.get("name", "")).strip()
    location    = (request.form.get("property_location") or request.form.get("location", "")).strip()
    category    = (request.form.get("property_category") or request.form.get("category", "hotel")).strip()
    description = (request.form.get("property_description") or request.form.get("description", "")).strip()
    try:
        price  = float(request.form.get("property_price") or request.form.get("price", "0"))
        rating = float(request.form.get("property_rating") or request.form.get("rating", "4.0"))
    except ValueError:
        price, rating = 0.0, 4.0

    image_url = ""
    # Try file upload first (field name is "property_images" in modal, "images" also accepted)
    for field in ("property_images", "images", "image"):
        if field in request.files:
            file = request.files[field]
            if file and file.filename and allowed_file(file.filename):
                ts       = datetime.now().strftime("%Y%m%d%H%M%S%f")
                filename = f"{ts}_{secure_filename(file.filename)}"
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                image_url = url_for("static", filename=f"uploads/{filename}")
                break

    # Fall back to URL field
    if not image_url:
        image_url = (request.form.get("image_url") or request.form.get("image", "")).strip()
    if not image_url:
        image_url = "https://images.unsplash.com/photo-1544550581-5f7ceaf7f992?w=800&q=80"

    if not name or not location:
        flash("Property name and location are required.", "danger")
        return redirect(url_for("admin_dashboard"))

    execute_db(
        "INSERT INTO properties (name, location, category, description, price, rating, image_url, status)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (name, location, category, description, price, rating, image_url, "approved")
    )
    log_activity(f"Admin added property '{name}'.", "fas fa-plus-circle")
    flash(f"Property '{name}' added successfully.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/property/<int:property_id>/approve", methods=["POST"])
@admin_required
def approve_property(property_id):
    prop = query_db("SELECT * FROM properties WHERE id=?", (property_id,), one=True)
    if prop:
        execute_db("UPDATE properties SET status='approved' WHERE id=?", (property_id,))
        if prop["user_id"]:
            execute_db(
                "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
                (prop["user_id"],
                 f"Your property '{prop['name']}' has been approved and is now live!",
                 "property_approved")
            )
        log_activity(f"Property '{prop['name']}' approved.", "fas fa-check-circle")
        flash(f"Property '{prop['name']}' approved.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/property/<int:property_id>/delete", methods=["POST"])
@admin_required
def delete_property(property_id):
    prop = query_db("SELECT name FROM properties WHERE id=?", (property_id,), one=True)
    if prop:
        execute_db("DELETE FROM properties WHERE id=?", (property_id,))
        log_activity(f"Property '{prop['name']}' deleted.", "fas fa-trash")
        flash(f"Property '{prop['name']}' deleted.", "success")
    return redirect(url_for("admin_dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Admin – user management
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = query_db("SELECT name FROM users WHERE id=?", (user_id,), one=True)
    if user:
        execute_db("DELETE FROM users WHERE id=?", (user_id,))
        log_activity(f"User '{user['name']}' deleted.", "fas fa-user-times")
        flash(f"User '{user['name']}' deleted.", "success")
    return redirect(url_for("admin_dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Admin – requests management
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin/request/<int:request_id>/status", methods=["POST"])
@admin_required
def update_request_status(request_id):
    status = request.form.get("status", "pending")
    execute_db("UPDATE requests SET status=? WHERE id=?", (status, request_id))
    flash(f"Request status updated to '{status}'.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/request/<int:request_id>/reply", methods=["POST"])
@admin_required
def reply_request(request_id):
    reply   = request.form.get("reply", "").strip()
    req_row = query_db("SELECT * FROM requests WHERE id=?", (request_id,), one=True)
    if req_row and reply:
        execute_db(
            "UPDATE requests SET admin_notes=?, status='in_progress' WHERE id=?",
            (reply, request_id)
        )
        if req_row["user_id"]:
            execute_db(
                "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
                (req_row["user_id"],
                 f"Admin replied to your request: {reply[:120]}",
                 "admin_reply")
            )
        flash("Reply sent.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/request/<int:request_id>/delete", methods=["POST"])
@admin_required
def delete_request(request_id):
    execute_db("DELETE FROM requests WHERE id=?", (request_id,))
    flash("Request deleted.", "success")
    return redirect(url_for("admin_dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
