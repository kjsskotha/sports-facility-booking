
import os
import hmac
import secrets
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

import pymysql
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort
)

app = Flask(__name__)

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", ""),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("RENDER", "").lower() == "true",
)

if not app.config["SECRET_KEY"]:
    raise RuntimeError("Set SECRET_KEY in Render environment variables.")


def db_connect():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USERNAME") or os.environ.get("MYSQL_USER"),
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
        charset="utf8mb4",
    )


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


def verify_csrf():
    submitted = request.form.get("csrf_token", "")
    saved = session.get("csrf_token", "")
    if not submitted or not saved or not hmac.compare_digest(submitted, saved):
        abort(400, description="Invalid form token. Refresh the page and try again.")


def provider_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("provider_authenticated") is not True:
            return redirect(url_for("provider_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def get_facilities():
    connection = db_connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT facility_id, facility_name, sport, location,
                       hourly_rate, is_available
                FROM facilities
                ORDER BY facility_name
            """)
            return cursor.fetchall()
    finally:
        connection.close()


@app.route("/")
def index():
    # Public page reads facilities only.
    # It never reads bookings or customer records.
    return render_template(
        "index.html",
        facilities=get_facilities(),
        today=date.today().isoformat()
    )


@app.route("/book", methods=["POST"])
def book():
    verify_csrf()

    name = request.form.get("customer_name", "").strip()
    email = request.form.get("email", "").strip()
    facility_raw = request.form.get("facility_id", "").strip()
    date_raw = request.form.get("booking_date", "").strip()
    start_raw = request.form.get("start_time", "").strip()
    end_raw = request.form.get("end_time", "").strip()

    if not name or len(name) > 100:
        flash("Enter a name of up to 100 characters.", "error")
        return redirect(url_for("index"))

    if (
        not email or len(email) > 150 or
        "@" not in email or email.startswith("@") or email.endswith("@")
    ):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("index"))

    try:
        facility_id = int(facility_raw)
        booking_day = date.fromisoformat(date_raw)
        start_time = datetime.strptime(start_raw, "%H:%M").time()
        end_time = datetime.strptime(end_raw, "%H:%M").time()
    except (ValueError, TypeError):
        flash("Enter a valid facility, date and time.", "error")
        return redirect(url_for("index"))

    if booking_day < date.today():
        flash("A past date cannot be booked.", "error")
        return redirect(url_for("index"))

    if end_time <= start_time:
        flash("End time must be later than start time.", "error")
        return redirect(url_for("index"))

    start_dt = datetime.combine(booking_day, start_time)
    end_dt = datetime.combine(booking_day, end_time)

    hours = Decimal(
        str((end_dt - start_dt).total_seconds() / 3600)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    connection = db_connect()

    try:
        with connection.cursor() as cursor:
            # Lock the selected facility while checking availability.
            cursor.execute("""
                SELECT facility_id, hourly_rate, is_available
                FROM facilities
                WHERE facility_id = %s
                FOR UPDATE
            """, (facility_id,))
            facility = cursor.fetchone()

            if not facility or not facility["is_available"]:
                connection.rollback()
                flash("This facility is unavailable.", "error")
                return redirect(url_for("index"))

            # Reject overlapping active bookings.
            cursor.execute("""
                SELECT booking_id
                FROM bookings
                WHERE facility_id = %s
                  AND booking_date = %s
                  AND status = 'Booked'
                  AND start_time < %s
                  AND end_time > %s
                LIMIT 1
            """, (facility_id, booking_day, end_time, start_time))

            if cursor.fetchone():
                connection.rollback()
                flash("That time slot is already booked.", "error")
                return redirect(url_for("index"))

            cursor.execute("""
                INSERT INTO customers (customer_name, email)
                VALUES (%s, %s)
            """, (name, email))
            customer_id = cursor.lastrowid

            total_cost = (
                Decimal(str(facility["hourly_rate"])) * hours
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            cursor.execute("""
                INSERT INTO bookings
                (customer_id, facility_id, booking_date, start_time,
                 end_time, hours, total_cost, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'Booked')
            """, (
                customer_id, facility_id, booking_day,
                start_time, end_time, hours, total_cost
            ))

        connection.commit()

        # Do not reveal booking IDs, email addresses, or booking history.
        flash("Booking submitted successfully.", "success")

    except pymysql.MySQLError:
        connection.rollback()
        app.logger.exception("Database error during booking")
        flash("Could not save the booking. Please try again later.", "error")

    finally:
        connection.close()

    return redirect(url_for("index"))


@app.route("/provider/login", methods=["GET", "POST"])
def provider_login():
    if session.get("provider_authenticated") is True:
        return redirect(url_for("provider_dashboard"))

    if request.method == "POST":
        verify_csrf()

        configured_password = os.environ.get("PROVIDER_PASSWORD", "")
        supplied_password = request.form.get("password", "")

        if configured_password and hmac.compare_digest(
            supplied_password, configured_password
        ):
            session.clear()
            session["provider_authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("provider_dashboard"))

        flash("Invalid provider password.", "error")

    return render_template("provider_login.html")


@app.route("/provider/logout", methods=["POST"])
@provider_required
def provider_logout():
    verify_csrf()
    session.clear()
    return redirect(url_for("provider_login"))


@app.route("/provider")
@app.route("/provider/dashboard")
@provider_required
def provider_dashboard():
    connection = db_connect()

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT b.booking_id, c.customer_name, c.email,
                       f.facility_name, f.sport, f.location,
                       b.booking_date, b.start_time, b.end_time,
                       b.hours, b.total_cost, b.status, b.created_at
                FROM bookings b
                JOIN customers c ON c.customer_id = b.customer_id
                JOIN facilities f ON f.facility_id = b.facility_id
                ORDER BY b.booking_date DESC,
                         b.start_time DESC, b.booking_id DESC
            """)
            bookings = cursor.fetchall()

            cursor.execute("""
                SELECT COUNT(*) AS total_bookings,
                       COALESCE(SUM(status = 'Booked'), 0) AS active_bookings,
                       COALESCE(SUM(status = 'Cancelled'), 0) AS cancelled_bookings,
                       COALESCE(SUM(
                           CASE WHEN status = 'Booked'
                           THEN total_cost ELSE 0 END
                       ), 0) AS active_revenue
                FROM bookings
            """)
            report = cursor.fetchone()

            cursor.execute("""
                SELECT f.facility_name, f.sport,
                       COUNT(CASE WHEN b.status = 'Booked'
                                  THEN 1 END) AS active_bookings,
                       COALESCE(SUM(
                           CASE WHEN b.status = 'Booked'
                           THEN b.hours ELSE 0 END
                       ), 0) AS booked_hours
                FROM facilities f
                LEFT JOIN bookings b ON b.facility_id = f.facility_id
                GROUP BY f.facility_id, f.facility_name, f.sport
                ORDER BY f.facility_name
            """)
            usage_report = cursor.fetchall()

    finally:
        connection.close()

    return render_template(
        "provider_dashboard.html",
        bookings=bookings,
        report=report,
        usage_report=usage_report
    )


@app.route("/provider/cancel/<int:booking_id>", methods=["POST"])
@provider_required
def cancel_booking(booking_id):
    verify_csrf()

    connection = db_connect()

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE bookings
                SET status = 'Cancelled'
                WHERE booking_id = %s AND status = 'Booked'
            """, (booking_id,))
            changed = cursor.rowcount

        connection.commit()

        if changed:
            flash("Booking cancelled.", "success")
        else:
            flash("Booking not found or already cancelled.", "error")

    except pymysql.MySQLError:
        connection.rollback()
        app.logger.exception("Database error while cancelling booking")
        flash("Could not cancel the booking.", "error")

    finally:
        connection.close()

    return redirect(url_for("provider_dashboard"))


@app.errorhandler(400)
def bad_request(error):
    return render_template(
        "error.html",
        message=getattr(error, "description", "Invalid request.")
    ), 400


@app.errorhandler(404)
def not_found(error):
    return render_template(
        "error.html",
        message="Page not found."
    ), 404


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000"))
    )
