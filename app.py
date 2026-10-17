
import os
import ssl
from datetime import date
from decimal import Decimal

import pymysql
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "demo-only-change-this")

# Read credentials from Render environment variables.
DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", ""),
    "port": int(os.environ.get("MYSQL_PORT", "17622")),
    "user": os.environ.get("MYSQL_USERNAME", ""),
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": os.environ.get("MYSQL_DATABASE", "defaultdb"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "connect_timeout": 10,
    "read_timeout": 10,
    "write_timeout": 10,
    "autocommit": False,
    "ssl": {"check_hostname": False},
}


def get_db():
    required = ["host", "user", "password", "database"]
    if any(not DB_CONFIG[key] for key in required):
        raise RuntimeError(
            "Database credentials are missing. Set the MYSQL environment variables in Render."
        )
    return pymysql.connect(**DB_CONFIG)


def setup_db():
    """Create the project's relational tables and demo facilities."""
    connection = get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS facilities (
                    facility_id INT AUTO_INCREMENT PRIMARY KEY,
                    facility_name VARCHAR(100) NOT NULL,
                    sport VARCHAR(60) NOT NULL,
                    location VARCHAR(120) NOT NULL,
                    hourly_rate DECIMAL(10,2) NOT NULL,
                    is_available BOOLEAN NOT NULL DEFAULT TRUE
                ) ENGINE=InnoDB
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id INT AUTO_INCREMENT PRIMARY KEY,
                    customer_name VARCHAR(100) NOT NULL,
                    email VARCHAR(150) NOT NULL
                ) ENGINE=InnoDB
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id INT AUTO_INCREMENT PRIMARY KEY,
                    customer_id INT NOT NULL,
                    facility_id INT NOT NULL,
                    booking_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    hours DECIMAL(5,2) NOT NULL,
                    total_cost DECIMAL(10,2) NOT NULL,
                    status ENUM('Booked', 'Cancelled') NOT NULL DEFAULT 'Booked',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (customer_id)
                        REFERENCES customers(customer_id),
                    FOREIGN KEY (facility_id)
                        REFERENCES facilities(facility_id),
                    INDEX idx_facility_date (facility_id, booking_date, status)
                ) ENGINE=InnoDB
            """)

            cursor.execute("SELECT COUNT(*) AS total FROM facilities")
            if cursor.fetchone()["total"] == 0:
                cursor.executemany("""
                    INSERT INTO facilities
                        (facility_name, sport, location, hourly_rate)
                    VALUES (%s, %s, %s, %s)
                """, [
                    ("Badminton Court A", "Badminton", "Sports Block 1", 100),
                    ("Football Ground", "Football", "Main Ground", 300),
                    ("Basketball Court B", "Basketball", "Sports Block 2", 150),
                    ("Tennis Court A", "Tennis", "Outdoor Zone", 200),
                    ("Cricket Practice Net", "Cricket", "Practice Area", 250),
                ])

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@app.route("/")
def index():
    connection = get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT facility_id, facility_name, sport, location, hourly_rate
                FROM facilities
                WHERE is_available = TRUE
                ORDER BY sport, facility_name
            """)
            facilities = cursor.fetchall()

            cursor.execute("""
                SELECT b.booking_id, c.customer_name, f.facility_name,
                       f.sport, b.booking_date, b.start_time, b.end_time,
                       b.hours, b.total_cost, b.status
                FROM bookings b
                JOIN customers c ON b.customer_id = c.customer_id
                JOIN facilities f ON b.facility_id = f.facility_id
                ORDER BY b.booking_id DESC
                LIMIT 30
            """)
            bookings = cursor.fetchall()

            cursor.execute("""
                SELECT COUNT(*) AS total_bookings,
                       COALESCE(SUM(total_cost), 0) AS total_usage_value
                FROM bookings
                WHERE status = 'Booked'
            """)
            report = cursor.fetchone()

        return render_template(
            "index.html",
            facilities=facilities,
            bookings=bookings,
            report=report,
            today=date.today().isoformat()
        )
    finally:
        connection.close()


@app.route("/book", methods=["POST"])
def book():
    customer_name = request.form.get("customer_name", "").strip()
    email = request.form.get("email", "").strip()
    facility_id = request.form.get("facility_id", type=int)
    booking_date = request.form.get("booking_date", "")
    start_time = request.form.get("start_time", "")
    end_time = request.form.get("end_time", "")

    if not all([customer_name, email, facility_id, booking_date,
                start_time, end_time]):
        flash("Please complete every booking field.", "error")
        return redirect(url_for("index"))

    if len(customer_name) > 100 or len(email) > 150 or "@" not in email:
        flash("Enter a valid demo name and email address.", "error")
        return redirect(url_for("index"))

    try:
        chosen_date = date.fromisoformat(booking_date)
        if chosen_date < date.today():
            raise ValueError
        start_minutes = int(start_time[:2]) * 60 + int(start_time[3:5])
        end_minutes = int(end_time[:2]) * 60 + int(end_time[3:5])
        if start_minutes < 0 or end_minutes > 1440 or end_minutes <= start_minutes:
            raise ValueError
    except (ValueError, TypeError):
        flash("Choose a valid date and an end time after the start time.", "error")
        return redirect(url_for("index"))

    hours = Decimal(end_minutes - start_minutes) / Decimal(60)
    connection = get_db()

    try:
        with connection.cursor() as cursor:
            # Lock the facility row so concurrent bookings are checked safely.
            cursor.execute("""
                SELECT hourly_rate, is_available
                FROM facilities
                WHERE facility_id = %s
                FOR UPDATE
            """, (facility_id,))
            facility = cursor.fetchone()

            if not facility or not facility["is_available"]:
                connection.rollback()
                flash("That facility is not available.", "error")
                return redirect(url_for("index"))

            cursor.execute("""
                SELECT booking_id FROM bookings
                WHERE facility_id = %s
                  AND booking_date = %s
                  AND status = 'Booked'
                  AND start_time < %s
                  AND end_time > %s
                FOR UPDATE
            """, (facility_id, booking_date, end_time, start_time))

            if cursor.fetchone():
                connection.rollback()
                flash("That time slot is already booked. Choose another time.", "error")
                return redirect(url_for("index"))

            cursor.execute("""
                INSERT INTO customers (customer_name, email)
                VALUES (%s, %s)
            """, (customer_name, email))
            customer_id = cursor.lastrowid

            total_cost = hours * Decimal(str(facility["hourly_rate"]))

            cursor.execute("""
                INSERT INTO bookings
                    (customer_id, facility_id, booking_date,
                     start_time, end_time, hours, total_cost)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                customer_id, facility_id, booking_date,
                start_time, end_time, hours, total_cost
            ))

        # Customer and booking are saved together in one transaction.
        connection.commit()
        flash("Booking successful! Your booking is saved .", "success")

    except Exception:
        connection.rollback()
        app.logger.exception("Booking failed")
        flash("Booking could not be completed. Check the database connection.", "error")
    finally:
        connection.close()

    return redirect(url_for("index"))


@app.route("/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    connection = get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE bookings SET status = 'Cancelled'
                WHERE booking_id = %s AND status = 'Booked'
            """, (booking_id,))
            changed = cursor.rowcount
        connection.commit()

        if changed:
            flash("Booking cancelled.", "success")
        else:
            flash("Active booking not found.", "error")
    except Exception:
        connection.rollback()
        app.logger.exception("Cancellation failed")
        flash("Could not cancel the booking.", "error")
    finally:
        connection.close()

    return redirect(url_for("index"))


@app.route("/health")
def health():
    """Simple endpoint to check whether the app and database are connected."""
    connection = get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 AS database_connected")
            cursor.fetchone()
        return {"app": "running", "database": "connected"}
    finally:
        connection.close()


# Create the tables when the app starts.
# If credentials are missing or the database is unavailable, log the error;
# the /health endpoint and normal pages will show the connection failure.
try:
    setup_db()
except Exception:
    app.logger.exception("Database initialization failed")


if __name__ == "__main__":
    app.run(debug=True)
