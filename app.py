import os, sqlite3, csv, io, calendar, re
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from xml.sax.saxutils import escape as xml_escape
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("POCKETIQ_SECRET", "local-only-change-this-secret")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=os.environ.get("RENDER") == "true")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pocketiq.db")

if USE_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg import errors as pg_errors

class PGConnection:
    """Small compatibility layer so the same app can use local SQLite or hosted Postgres."""
    def __init__(self, url):
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
        self.conn = psycopg.connect(url, row_factory=dict_row, prepare_threshold=None)
    def __enter__(self):
        self.conn.__enter__(); return self
    def __exit__(self, typ, val, tb): return self.conn.__exit__(typ, val, tb)
    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("?", "%s"), params)
    def executemany(self, sql, seq):
        return self.conn.cursor().executemany(sql.replace("?", "%s"), seq)

def connect():
    if USE_POSTGRES:
        return PGConnection(DATABASE_URL)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    with connect() as con:
        if USE_POSTGRES:
            con.execute("""CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS expenses (
                id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL CHECK(amount > 0),
                category TEXT NOT NULL, expense_date TEXT NOT NULL, created_at TEXT NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS quotes (
                id BIGSERIAL PRIMARY KEY, quote TEXT NOT NULL, author TEXT DEFAULT '')""")
            count = con.execute("SELECT COUNT(*) AS n FROM quotes").fetchone()["n"]
        else:
            con.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0), category TEXT NOT NULL, expense_date TEXT NOT NULL,
                created_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)""")
            con.execute("""CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, quote TEXT NOT NULL, author TEXT DEFAULT '')""")
            count = con.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        # Additive schema migrations: existing users, expenses and quotes are preserved.
        if USE_POSTGRES:
            con.execute("""CREATE TABLE IF NOT EXISTS finance_profiles (
                user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                monthly_income DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(monthly_income >= 0),
                monthly_budget DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(monthly_budget >= 0),
                updated_at TEXT NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS savings_goals (
                id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL, target_amount DOUBLE PRECISION NOT NULL CHECK(target_amount > 0),
                saved_amount DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(saved_amount >= 0),
                target_date TEXT NOT NULL, created_at TEXT NOT NULL)""")
        else:
            con.execute("""CREATE TABLE IF NOT EXISTS finance_profiles (
                user_id INTEGER PRIMARY KEY, monthly_income REAL NOT NULL DEFAULT 0 CHECK(monthly_income >= 0),
                monthly_budget REAL NOT NULL DEFAULT 0 CHECK(monthly_budget >= 0), updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)""")
            con.execute("""CREATE TABLE IF NOT EXISTS savings_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL,
                target_amount REAL NOT NULL CHECK(target_amount > 0), saved_amount REAL NOT NULL DEFAULT 0 CHECK(saved_amount >= 0),
                target_date TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)""")
        if count == 0:
            con.executemany("INSERT INTO quotes (quote,author) VALUES (?,?)", [
                ("Beware of little expenses; a small leak can sink a great ship.", "Benjamin Franklin"),
                ("Do not save what is left after spending; spend what is left after saving.", "Warren Buffett"),
                ("A small saving today can become a big opportunity tomorrow.", "Pocket IQ 2.0"),
                ("A budget is telling your money where to go instead of wondering where it went.", "John C. Maxwell"),
                ("Save consistently. Your future self will thank you.", "Pocket IQ 2.0")])

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"): return jsonify(ok=False, error="Please log in again."), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def current_user():
    if not session.get("user_id"): return None
    with connect() as con:
        return con.execute("SELECT id,name,email FROM users WHERE id=?", (session["user_id"],)).fetchone()

def get_financial_health(user_id, today=None):
    """Transparent 100-point score: savings rate (50) + budget control (50)."""
    today = today or date.today()
    month_start = today.replace(day=1).isoformat()
    with connect() as con:
        profile = con.execute("SELECT monthly_income,monthly_budget FROM finance_profiles WHERE user_id=?", (user_id,)).fetchone()
        month_rows = con.execute("SELECT amount FROM expenses WHERE user_id=? AND expense_date BETWEEN ? AND ?",
                                 (user_id, month_start, today.isoformat())).fetchall()
    income = float(profile["monthly_income"]) if profile else 0.0
    budget = float(profile["monthly_budget"]) if profile else 0.0
    spent = sum(float(r["amount"]) for r in month_rows)
    if income <= 0 or budget <= 0:
        return {"configured": False, "score": None, "income": income, "budget": budget,
                "spent": spent, "savings_rate": None, "budget_used": None,
                "savings_points": None, "budget_points": None,
                "suggestions": ["Enter your monthly income and spending budget to calculate your score."]}
    savings_rate = (income - spent) / income * 100
    # Full savings points at 20% saved; zero when spending equals/exceeds income.
    savings_points = max(0.0, min(50.0, savings_rate / 20.0 * 50.0))
    budget_used = spent / budget * 100
    # Full budget points at <=80% used; linearly falls to zero at 100%.
    budget_points = 50.0 if budget_used <= 80 else max(0.0, (100.0 - budget_used) / 20.0 * 50.0)
    score = int(round(savings_points + budget_points))
    suggestions = []
    if savings_rate < 20:
        suggestions.append("Aim to save at least 20% of your monthly income; review non-essential spending.")
    if budget_used > 80:
        suggestions.append("You have used more than 80% of your monthly budget. Check your remaining planned expenses.")
    if not suggestions:
        suggestions.append("Good work: keep saving consistently and review your budget each month.")
    return {"configured": True, "score": score, "income": income, "budget": budget,
            "spent": spent, "savings_rate": round(savings_rate, 1),
            "budget_used": round(budget_used, 1), "savings_points": round(savings_points, 1),
            "budget_points": round(budget_points, 1), "suggestions": suggestions}

def goal_view(row):
    target = float(row["target_amount"]); saved = float(row["saved_amount"])
    remaining = max(0.0, target - saved)
    try:
        days = max(0, (date.fromisoformat(row["target_date"]) - date.today()).days)
    except (ValueError, TypeError):
        days = 0
    months = max(1, (days + 29) // 30)
    return {**dict(row), "target_amount": target, "saved_amount": saved,
            "remaining": remaining, "progress": min(100.0, saved / target * 100),
            "daily_needed": remaining / days if days > 0 else remaining,
            "monthly_needed": remaining / months}

@app.route("/")
def index():
    if not session.get("user_id"): return redirect(url_for("login"))
    user = current_user()
    if not user: session.clear(); return redirect(url_for("login"))
    with connect() as con:
        expenses = con.execute("SELECT * FROM expenses WHERE user_id=? ORDER BY expense_date DESC, id DESC LIMIT 100", (user["id"],)).fetchall()
        quotes = con.execute("SELECT * FROM quotes ORDER BY id").fetchall()
        profile = con.execute("SELECT monthly_income,monthly_budget FROM finance_profiles WHERE user_id=?", (user["id"],)).fetchone()
        goals = con.execute("SELECT * FROM savings_goals WHERE user_id=? ORDER BY target_date,id", (user["id"],)).fetchall()
    health = get_financial_health(user["id"])
    return render_template("index.html", user=user, expenses=expenses, today=date.today().isoformat(),
                           quotes=quotes, profile=profile, goals=[goal_view(g) for g in goals], health=health)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name=request.form.get("name", "").strip(); email=request.form.get("email", "").strip().lower(); password=request.form.get("password", "")
        if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(password)<8:
            flash("Enter your name, a valid email and a password of at least 8 characters.", "error")
            return render_template("auth.html", mode="register")
        try:
            with connect() as con:
                if USE_POSTGRES:
                    row=con.execute("INSERT INTO users (name,email,password_hash,created_at) VALUES (?,?,?,?) RETURNING id", (name,email,generate_password_hash(password),datetime.now().isoformat(timespec="seconds"))).fetchone(); user_id=row["id"]
                else:
                    cur=con.execute("INSERT INTO users (name,email,password_hash,created_at) VALUES (?,?,?,?)", (name,email,generate_password_hash(password),datetime.now().isoformat(timespec="seconds"))); user_id=cur.lastrowid
            session.clear(); session["user_id"]=user_id; session["user_name"]=name
            return redirect(url_for("index"))
        except Exception as exc:
            if (USE_POSTGRES and isinstance(exc, pg_errors.UniqueViolation)) or (not USE_POSTGRES and isinstance(exc, sqlite3.IntegrityError)):
                flash("An account with that email already exists. Please log in.", "error")
            else: raise
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email=request.form.get("email", "").strip().lower(); password=request.form.get("password", "")
        with connect() as con: user=con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear(); session["user_id"]=user["id"]; session["user_name"]=user["name"]; return redirect(url_for("index"))
        flash("Email or password is incorrect.", "error")
    return render_template("auth.html", mode="login")

@app.get("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

@app.post("/api/expenses")
@login_required
def add_expense():
    data=request.get_json(silent=True) or request.form
    try:
        title=str(data.get("title", "")).strip(); amount=float(data.get("amount", 0)); category=str(data.get("category", "Other")).strip(); expense_date=str(data.get("expense_date", date.today().isoformat())); date.fromisoformat(expense_date)
        if not title or amount<=0: return jsonify(ok=False,error="Enter a description and an amount greater than zero."),400
        with connect() as con:
            if USE_POSTGRES:
                row=con.execute("INSERT INTO expenses (user_id,title,amount,category,expense_date,created_at) VALUES (?,?,?,?,?,?) RETURNING *", (session["user_id"],title,amount,category,expense_date,datetime.now().isoformat(timespec="seconds"))).fetchone()
            else:
                cur=con.execute("INSERT INTO expenses (user_id,title,amount,category,expense_date,created_at) VALUES (?,?,?,?,?,?)", (session["user_id"],title,amount,category,expense_date,datetime.now().isoformat(timespec="seconds"))); row=con.execute("SELECT * FROM expenses WHERE id=? AND user_id=?", (cur.lastrowid,session["user_id"])).fetchone()
        return jsonify(ok=True,expense=dict(row))
    except (ValueError, TypeError): return jsonify(ok=False,error="Please enter valid expense details."),400

@app.delete("/api/expenses/<int:expense_id>")
@login_required
def delete_expense(expense_id):
    with connect() as con: con.execute("DELETE FROM expenses WHERE id=? AND user_id=?",(expense_id,session["user_id"]))
    return jsonify(ok=True)

@app.get("/api/analytics")
@login_required
def analytics():
    period=request.args.get("period","day")
    if period not in ("day","week","month"): period="day"
    today=date.today(); start=today
    if period=="week": start=today-timedelta(days=today.weekday())
    elif period=="month": start=today.replace(day=1)
    with connect() as con:
        rows=con.execute("SELECT expense_date,amount,category FROM expenses WHERE user_id=? AND expense_date BETWEEN ? AND ?",(session["user_id"],start.isoformat(),today.isoformat())).fetchall()
    category_totals={}
    for row in rows: category_totals[row["category"]]=category_totals.get(row["category"],0.0)+float(row["amount"])
    if period=="day":
        labels=list(category_totals) or ["No expenses"]; values=[round(v,2) for v in category_totals.values()] or [0]; title="Today's spending by category"
    elif period=="week":
        labels=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]; totals={k:0.0 for k in labels}
        for row in rows: totals[date.fromisoformat(row["expense_date"]).strftime("%a")]+=float(row["amount"])
        values=[round(totals[k],2) for k in labels]; title="This week's spending"
    else:
        labels=[str(i) for i in range(1,calendar.monthrange(today.year,today.month)[1]+1)]; totals={k:0.0 for k in labels}
        for row in rows: totals[str(date.fromisoformat(row["expense_date"]).day)]+=float(row["amount"])
        values=[round(totals[k],2) for k in labels]; title="This month's spending by day"
    return jsonify(period=period,title=title,labels=labels,values=values,categories=list(category_totals) or ["No expenses"],category_values=[round(v,2) for v in category_totals.values()] or [0],total=round(sum(float(r["amount"]) for r in rows),2),count=len(rows))

@app.post("/api/finance-profile")
@login_required
def save_finance_profile():
    data = request.get_json(silent=True) or request.form
    try:
        income = float(data.get("monthly_income", 0))
        budget = float(data.get("monthly_budget", 0))
        if income <= 0 or budget <= 0 or income > 1e12 or budget > 1e12:
            return jsonify(ok=False, error="Enter monthly income and budget greater than zero."), 400
        now = datetime.now().isoformat(timespec="seconds")
        with connect() as con:
            if USE_POSTGRES:
                con.execute("""INSERT INTO finance_profiles(user_id,monthly_income,monthly_budget,updated_at)
                    VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                    monthly_income=EXCLUDED.monthly_income,monthly_budget=EXCLUDED.monthly_budget,updated_at=EXCLUDED.updated_at""",
                    (session["user_id"], income, budget, now))
            else:
                con.execute("""INSERT INTO finance_profiles(user_id,monthly_income,monthly_budget,updated_at)
                    VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                    monthly_income=excluded.monthly_income,monthly_budget=excluded.monthly_budget,updated_at=excluded.updated_at""",
                    (session["user_id"], income, budget, now))
        return jsonify(ok=True, health=get_financial_health(session["user_id"]))
    except (ValueError, TypeError):
        return jsonify(ok=False, error="Please enter valid amounts."), 400

@app.get("/api/financial-health")
@login_required
def financial_health():
    return jsonify(ok=True, health=get_financial_health(session["user_id"]))

@app.get("/api/goals")
@login_required
def list_goals():
    with connect() as con:
        rows = con.execute("SELECT * FROM savings_goals WHERE user_id=? ORDER BY target_date,id", (session["user_id"],)).fetchall()
    return jsonify(ok=True, goals=[goal_view(r) for r in rows])

@app.post("/api/goals")
@login_required
def create_goal():
    data = request.get_json(silent=True) or {}
    try:
        name = str(data.get("name", "")).strip()
        target = float(data.get("target_amount", 0))
        saved = float(data.get("saved_amount", 0))
        target_date = date.fromisoformat(str(data.get("target_date", ""))).isoformat()
        if not name or len(name) > 80 or target <= 0 or saved < 0 or saved > target or target > 1e12:
            return jsonify(ok=False, error="Check the goal name and amounts. Saved amount cannot exceed the target."), 400
        with connect() as con:
            if USE_POSTGRES:
                row = con.execute("""INSERT INTO savings_goals(user_id,name,target_amount,saved_amount,target_date,created_at)
                    VALUES(?,?,?,?,?,?) RETURNING *""", (session["user_id"],name,target,saved,target_date,datetime.now().isoformat(timespec="seconds"))).fetchone()
            else:
                cur = con.execute("""INSERT INTO savings_goals(user_id,name,target_amount,saved_amount,target_date,created_at)
                    VALUES(?,?,?,?,?,?)""", (session["user_id"],name,target,saved,target_date,datetime.now().isoformat(timespec="seconds")))
                row = con.execute("SELECT * FROM savings_goals WHERE id=? AND user_id=?", (cur.lastrowid,session["user_id"])).fetchone()
        return jsonify(ok=True, goal=goal_view(row))
    except (ValueError, TypeError):
        return jsonify(ok=False, error="Enter a valid target date and amounts."), 400

@app.put("/api/goals/<int:goal_id>")
@login_required
def update_goal(goal_id):
    data = request.get_json(silent=True) or {}
    try:
        name = str(data.get("name", "")).strip()
        target = float(data.get("target_amount", 0))
        saved = float(data.get("saved_amount", 0))
        target_date = date.fromisoformat(str(data.get("target_date", ""))).isoformat()
        if not name or len(name) > 80 or target <= 0 or saved < 0 or saved > target or target > 1e12:
            return jsonify(ok=False, error="Check the goal name and amounts. Saved amount cannot exceed the target."), 400
        with connect() as con:
            con.execute("""UPDATE savings_goals SET name=?,target_amount=?,saved_amount=?,target_date=?
                WHERE id=? AND user_id=?""", (name,target,saved,target_date,goal_id,session["user_id"]))
            row = con.execute("SELECT * FROM savings_goals WHERE id=? AND user_id=?", (goal_id,session["user_id"])).fetchone()
        if not row: return jsonify(ok=False,error="Goal not found."),404
        return jsonify(ok=True,goal=goal_view(row))
    except (ValueError, TypeError):
        return jsonify(ok=False, error="Enter a valid target date and amounts."), 400

@app.delete("/api/goals/<int:goal_id>")
@login_required
def delete_goal(goal_id):
    with connect() as con:
        cur = con.execute("DELETE FROM savings_goals WHERE id=? AND user_id=?", (goal_id,session["user_id"]))
    return jsonify(ok=True, deleted=True)

@app.get("/reports/weekly.pdf")
@login_required
def weekly_report():
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    prev_start = week_start - timedelta(days=7)
    elapsed_days = (today - week_start).days
    prev_end = prev_start + timedelta(days=elapsed_days)
    with connect() as con:
        rows = con.execute("""SELECT expense_date,amount,category,title FROM expenses
            WHERE user_id=? AND expense_date BETWEEN ? AND ? ORDER BY expense_date""",
            (session["user_id"],week_start.isoformat(),min(today,week_end).isoformat())).fetchall()
        prev_rows = con.execute("""SELECT amount FROM expenses WHERE user_id=? AND expense_date BETWEEN ? AND ?""",
            (session["user_id"],prev_start.isoformat(),prev_end.isoformat())).fetchall()
        goals = con.execute("SELECT * FROM savings_goals WHERE user_id=? ORDER BY target_date", (session["user_id"],)).fetchall()
        user = con.execute("SELECT name,email FROM users WHERE id=?", (session["user_id"],)).fetchone()
    total = sum(float(r["amount"]) for r in rows)
    prev_total = sum(float(r["amount"]) for r in prev_rows)
    by_day = { (week_start + timedelta(days=i)).isoformat(): 0.0 for i in range(7) }
    by_category = {}
    for r in rows:
        by_day[r["expense_date"]] = by_day.get(r["expense_date"],0.0) + float(r["amount"])
        by_category[r["category"]] = by_category.get(r["category"],0.0) + float(r["amount"])
    top_category = max(by_category, key=by_category.get) if by_category else "No expenses"
    health = get_financial_health(session["user_id"], today)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="PocketTitle", parent=styles["Title"], textColor=colors.HexColor("#164b91"), fontSize=22, spaceAfter=6))
    story = [Paragraph("Pocket IQ 2.0 — Weekly Financial Report", styles["PocketTitle"]),
             Paragraph(f"Prepared for {xml_escape(str(user['name']))} · {week_start.strftime('%d %b %Y')} – {min(today,week_end).strftime('%d %b %Y')}", styles["Normal"]),
             Spacer(1, 10)]
    change = "No previous-week data" if not prev_rows else (f"{((total-prev_total)/prev_total*100):+.1f}% vs previous week" if prev_total else "Previous week was ₹0.00")
    summary_data = [["This week's expenses", "Previous week", "Highest category"], [f"₹{total:,.2f}", f"₹{prev_total:,.2f}", top_category]]
    summary = Table(summary_data, colWidths=[55*mm,50*mm,65*mm])
    summary.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf1f8")),("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#164b91")),("GRID",(0,0),(-1,-1),.4,colors.HexColor("#ccd5df")),("PADDING",(0,0),(-1,-1),8),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold")]))
    story += [summary, Spacer(1,5), Paragraph(f"Week-over-week change: {change}", styles["Normal"]), Spacer(1,12), Paragraph("Daily expense breakdown", styles["Heading2"])]
    daily_data = [["Day","Date","Expenses"]]
    for i in range((today - week_start).days + 1):
        d = week_start + timedelta(days=i)
        daily_data.append([d.strftime("%A"),d.strftime("%d %b"),f"₹{by_day.get(d.isoformat(),0):,.2f}"])
    daily = Table(daily_data, colWidths=[65*mm,50*mm,55*mm])
    daily.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf1f8")),("GRID",(0,0),(-1,-1),.4,colors.HexColor("#ccd5df")),("PADDING",(0,0),(-1,-1),7),("ALIGN",(2,1),(2,-1),"RIGHT")]))
    story += [daily, Spacer(1,12), Paragraph("Category summary",styles["Heading2"])]
    cat_data = [["Category","Amount"]] + [[xml_escape(str(k)),f"₹{v:,.2f}"] for k,v in sorted(by_category.items(),key=lambda x:x[1],reverse=True)]
    if len(cat_data)==1: cat_data.append(["No expenses recorded","₹0.00"])
    cats = Table(cat_data,colWidths=[100*mm,70*mm])
    cats.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf1f8")),("GRID",(0,0),(-1,-1),.4,colors.HexColor("#ccd5df")),("PADDING",(0,0),(-1,-1),7)]))
    story += [cats, Spacer(1,12), Paragraph("Financial Health Score",styles["Heading2"])]
    story.append(Paragraph(f"{health['score']}/100" if health["configured"] else "Not calculated — add monthly income and budget in your dashboard.",styles["Normal"]))
    story.append(Spacer(1,8))
    story.append(Paragraph("Savings goal progress",styles["Heading2"]))
    if goals:
        for g in goals:
            gv=goal_view(g)
            story.append(Paragraph(f"{xml_escape(str(gv['name']))}: ₹{gv['saved_amount']:,.2f} of ₹{gv['target_amount']:,.2f} ({gv['progress']:.1f}%)",styles["Normal"]))
    else: story.append(Paragraph("No savings goals created yet.",styles["Normal"]))
    story += [Spacer(1,8), Paragraph("Suggestions",styles["Heading2"])]
    tips = health["suggestions"][:]
    if by_category: tips.append(f"Review your {top_category} spending, your highest category this week.")
    if not rows: tips.append("Record expenses regularly to make your weekly report more useful.")
    for tip in tips: story.append(Paragraph("• "+xml_escape(str(tip)),styles["Normal"]))
    doc.build(story)
    response = make_response(buffer.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="Pocket IQ 2.0-weekly-report-{today.isoformat()}.pdf"'
    return response

@app.get("/api/quote")
@login_required
def random_quote():
    with connect() as con:
        q=con.execute("SELECT quote,author FROM quotes ORDER BY RANDOM() LIMIT 1").fetchone()
    return jsonify(dict(q) if q else {"quote":"Save a little today for a brighter tomorrow.","author":"Pocket IQ 2.0"})

ADMIN_PASSWORD=os.environ.get("POCKETIQ_ADMIN_PASSWORD", "pocketiq123" if os.environ.get("RENDER") != "true" else "")
@app.route("/admin",methods=["GET","POST"])
def admin():
    if request.method=="POST" and request.form.get("action")=="login":
        if ADMIN_PASSWORD and request.form.get("password")==ADMIN_PASSWORD: session["quote_admin"]=True; return redirect(url_for("admin"))
        flash("Incorrect admin password.","error")
    if not session.get("quote_admin"): return render_template("admin_login.html")
    with connect() as con: quotes=con.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()
    return render_template("admin.html",quotes=quotes)
@app.post("/admin/quotes")
def save_quote():
    if not session.get("quote_admin"): return redirect(url_for("admin"))
    quote_id=request.form.get("quote_id"); quote=request.form.get("quote","").strip(); author=request.form.get("author","").strip()
    if quote:
        with connect() as con:
            if quote_id: con.execute("UPDATE quotes SET quote=?,author=? WHERE id=?",(quote,author,quote_id))
            else: con.execute("INSERT INTO quotes (quote,author) VALUES (?,?)",(quote,author))
    return redirect(url_for("admin"))
@app.post("/admin/quotes/<int:quote_id>/delete")
def delete_quote(quote_id):
    if not session.get("quote_admin"): return redirect(url_for("admin"))
    with connect() as con: con.execute("DELETE FROM quotes WHERE id=?",(quote_id,))
    return redirect(url_for("admin"))
@app.post("/admin/upload")
def upload_quotes():
    if not session.get("quote_admin"): return redirect(url_for("admin"))
    file=request.files.get("quote_file")
    if not file or not file.filename: flash("Choose a CSV or TXT file.","error"); return redirect(url_for("admin"))
    try:
        text=file.read().decode("utf-8-sig"); rows=[]
        if file.filename.lower().endswith(".csv"):
            for row in csv.DictReader(io.StringIO(text)):
                quote=(row.get("quote") or "").strip(); author=(row.get("author") or "").strip()
                if quote: rows.append((quote,author))
        elif file.filename.lower().endswith(".txt"): rows=[(line.strip(),"") for line in text.splitlines() if line.strip()]
        else: raise ValueError("Only CSV and TXT files are supported.")
        if not rows: raise ValueError("No quotes found in the file.")
        with connect() as con: con.executemany("INSERT INTO quotes (quote,author) VALUES (?,?)",rows)
        flash(f"Added {len(rows)} quotes.","success")
    except (UnicodeDecodeError,ValueError) as e: flash(str(e),"error")
    return redirect(url_for("admin"))
@app.get("/admin/logout")
def admin_logout(): session.pop("quote_admin",None); return redirect(url_for("index"))

@app.get("/health")
def health(): return jsonify(status="ok", database="postgres" if USE_POSTGRES else "sqlite")

init_db()
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=os.environ.get("RENDER")!="true")
