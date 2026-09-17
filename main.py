import csv
import io
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
DB = BASE / "attendance.db"

app = FastAPI(title="Staff Attendance", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS employees(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        position TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS attendance(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        work_date TEXT NOT NULL,
        check_in TEXT,
        check_out TEXT,
        status TEXT DEFAULT 'Present',
        UNIQUE(employee_id, work_date),
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    )""")
    conn.commit()
    conn.close()

init_db()

class Scan(BaseModel):
    employee_code: str
    action: str = "auto"

@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/api/employees")
def employees():
    conn = db()
    rows = conn.execute("SELECT * FROM employees WHERE active=1 ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/employees")
def add_employee(employee_code: str = Form(...), name: str = Form(...),
                  position: str = Form(""), phone: str = Form("")):
    employee_code = employee_code.strip()
    name = name.strip()
    if not employee_code or not name:
        raise HTTPException(400, "Employee code and name are required")
    conn = db()
    try:
        cur = conn.execute(
            "INSERT INTO employees(employee_code,name,position,phone,created_at) VALUES(?,?,?,?,?)",
            (employee_code, name, position.strip(), phone.strip(), datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()
        row = conn.execute("SELECT * FROM employees WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Employee code already exists")
    finally:
        conn.close()

@app.delete("/api/employees/{employee_id}")
def delete_employee(employee_id: int):
    conn = db()
    conn.execute("UPDATE employees SET active=0 WHERE id=?", (employee_id,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/scan")
def scan(data: Scan):
    code = data.employee_code.strip()
    if not code:
        raise HTTPException(400, "QR code is empty")
    conn = db()
    emp = conn.execute("SELECT * FROM employees WHERE employee_code=? AND active=1", (code,)).fetchone()
    if not emp:
        conn.close()
        raise HTTPException(404, "Employee not found")
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    time = now.strftime("%H:%M:%S")
    row = conn.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?", (emp["id"], date)).fetchone()

    if not row:
        # Default work start: 08:00
        status = "Late" if now.hour > 8 or (now.hour == 8 and now.minute > 0) else "Present"
        conn.execute("INSERT INTO attendance(employee_id,work_date,check_in,status) VALUES(?,?,?,?)",
                     (emp["id"], date, time, status))
        conn.commit()
        action = "Check-in"
    elif not row["check_out"]:
        conn.execute("UPDATE attendance SET check_out=? WHERE id=?", (time, row["id"]))
        conn.commit()
        action = "Check-out"
        status = row["status"]
    else:
        conn.close()
        return {"success": True, "action": "Already completed", "employee": dict(emp), "date": date,
                "check_in": row["check_in"], "check_out": row["check_out"], "status": row["status"]}
    conn.close()
    return {"success": True, "action": action, "employee": dict(emp), "date": date,
            "time": time, "status": status}

@app.get("/api/attendance")
def attendance(date: str | None = None):
    date = date or datetime.now().strftime("%Y-%m-%d")
    conn = db()
    rows = conn.execute("""
        SELECT e.employee_code,e.name,e.position,a.work_date,a.check_in,a.check_out,
               COALESCE(a.status,'Absent') status
        FROM employees e
        LEFT JOIN attendance a ON a.employee_id=e.id AND a.work_date=?
        WHERE e.active=1 ORDER BY e.name
    """, (date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/export")
def export(date: str | None = None):
    date = date or datetime.now().strftime("%Y-%m-%d")
    rows = attendance(date)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee ID","Name","Position","Date","Check-in","Check-out","Status"])
    for r in rows:
        writer.writerow([r["employee_code"],r["name"],r["position"],r["work_date"],
                         r["check_in"] or "",r["check_out"] or "",r["status"]])
    data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return StreamingResponse(data, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="attendance-{date}.csv"'})
