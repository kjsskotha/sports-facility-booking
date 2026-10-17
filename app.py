
from flask import Flask, request, redirect, render_template_string
import sqlite3

app = Flask(__name__)
DB = "sports.db"

def connect_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def setup_db():
    con = connect_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS facilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sport TEXT NOT NULL,
            location TEXT NOT NULL,
            hourly_rate REAL NOT NULL
        )
    """)
    count = con.execute(
        "SELECT COUNT(*) FROM facilities"
    ).fetchone()[0]

    if count == 0:
        con.executemany("""
            INSERT INTO facilities
            (name, sport, location, hourly_rate)
            VALUES (?, ?, ?, ?)
        """, [
            ("Court A", "Badminton", "Block 1", 100),
            ("Ground A", "Football", "Main Ground", 300),
            ("Court B", "Basketball", "Block 2", 150)
        ])

    con.commit()
    con.close()

PAGE = """
<!DOCTYPE html>
<html>
<head>
<title>Sports Facility Booking</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f1f5f9;
    color: #172033;
}
header {
    background: #102a43;
    color: white;
    padding: 25px;
    text-align: center;
}
main {
    max-width: 1000px;
    margin: 25px auto;
    padding: 15px;
}
.card {
    background: white;
    padding: 22px;
    margin-bottom: 20px;
    border-radius: 12px;
    box-shadow: 0 3px 12px #00000010;
}
input, button {
    padding: 11px;
    margin: 5px 3px;
    border-radius: 6px;
    border: 1px solid #cbd5e1;
}
button {
    background: #087f8c;
    color: white;
    cursor: pointer;
}
table {
    width: 100%;
    border-collapse: collapse;
}
th, td {
    text-align: left;
    padding: 12px 8px;
    border-bottom: 1px solid #e2e8f0;
}
th { background: #eaf4f4; }
a { color: #b42318; }
.note { color: #64748b; }
</style>
</head>
<body>
<header>
    <h1>Sports Facility Booking</h1>
    <p>Manage sports facilities in one place</p>
</header>
<main>
    <section class="card">
        <h2>Add a Facility</h2>
        <form action="/add" method="post">
            <input name="name" placeholder="Facility name" required>
            <input name="sport" placeholder="Sport" required>
            <input name="location" placeholder="Location" required>
            <input name="hourly_rate" type="number"
                   min="0" step="0.01"
                   placeholder="Rate per hour" required>
            <button type="submit">Add Facility</button>
        </form>
    </section>

    <section class="card">
        <h2>Sports Facilities</h2>
        <p class="note">Demo facilities and sample data</p>
        <table>
            <tr>
                <th>ID</th><th>Facility</th><th>Sport</th>
                <th>Location</th><th>Rate/hour</th><th>Actions</th>
            </tr>
            {% for f in facilities %}
            <tr>
                <td>{{ f.id }}</td>
                <td>{{ f.name }}</td>
                <td>{{ f.sport }}</td>
                <td>{{ f.location }}</td>
                <td>₹{{ "%.2f"|format(f.hourly_rate) }}</td>
                <td>
                    <a href="/edit/{{ f.id }}">Edit</a> |
                    <form action="/delete/{{ f.id }}" method="post"
                          style="display:inline"
                          onsubmit="return confirm('Delete this facility?')">
                        <button type="submit">Delete</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
    </section>
</main>
</body>
</html>
"""

EDIT_PAGE = """
<!DOCTYPE html>
<html>
<head><title>Edit Facility</title></head>
<body style="font-family:Arial; padding:30px">
<h2>Update Facility</h2>
<form method="post">
    <p>Name: <input name="name" value="{{ f.name }}" required></p>
    <p>Sport: <input name="sport" value="{{ f.sport }}" required></p>
    <p>Location: <input name="location" value="{{ f.location }}" required></p>
    <p>Rate/hour: <input name="hourly_rate" type="number"
       min="0" step="0.01" value="{{ f.hourly_rate }}" required></p>
    <button type="submit">Save Changes</button>
</form>
<p><a href="/">Back to dashboard</a></p>
</body>
</html>
"""

@app.route("/")
def home():
    con = connect_db()
    facilities = con.execute(
        "SELECT * FROM facilities ORDER BY id"
    ).fetchall()
    con.close()
    return render_template_string(PAGE, facilities=facilities)

@app.route("/add", methods=["POST"])
def add():
    name = request.form["name"].strip()
    sport = request.form["sport"].strip()
    location = request.form["location"].strip()
    rate = float(request.form["hourly_rate"])

    if not name or not sport or not location or rate < 0:
        return "Invalid facility details", 400

    con = connect_db()
    con.execute("""
        INSERT INTO facilities (name, sport, location, hourly_rate)
        VALUES (?, ?, ?, ?)
    """, (name, sport, location, rate))
    con.commit()
    con.close()
    return redirect("/")

@app.route("/edit/<int:facility_id>", methods=["GET", "POST"])
def edit(facility_id):
    con = connect_db()

    if request.method == "POST":
        rate = float(request.form["hourly_rate"])
        if rate < 0:
            con.close()
            return "Invalid rate", 400

        con.execute("""
            UPDATE facilities
            SET name=?, sport=?, location=?, hourly_rate=?
            WHERE id=?
        """, (
            request.form["name"].strip(),
            request.form["sport"].strip(),
            request.form["location"].strip(),
            rate, facility_id
        ))
        con.commit()
        con.close()
        return redirect("/")

    f = con.execute(
        "SELECT * FROM facilities WHERE id=?",
        (facility_id,)
    ).fetchone()
    con.close()

    if f is None:
        return "Facility not found", 404

    return render_template_string(EDIT_PAGE, f=f)

@app.route("/delete/<int:facility_id>", methods=["POST"])
def delete(facility_id):
    con = connect_db()
    con.execute(
        "DELETE FROM facilities WHERE id=?",
        (facility_id,)
    )
    con.commit()
    con.close()
    return redirect("/")

setup_db()

if __name__ == "__main__":
    app.run(debug=True)
