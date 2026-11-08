POCKETIQ — FREE CLOUD DEPLOYMENT EDITION

RECOMMENDED FREE SETUP
- Render Free: hosts the Flask website and gives you a public *.onrender.com URL.
- Neon Free: hosted PostgreSQL database for user accounts, expenses and quotes.
- GitHub Free: stores the project code so Render can deploy it.
Estimated cost: $0/month within the providers’ free allowances.

WHY THIS COMBINATION
Render is the website host, not the database. Do not store the live SQLite file on
Render Free: its filesystem is temporary and is erased on restarts/redeploys/spin-down.
Neon provides managed Postgres with 0.5 GB storage, 100 CU-hours/project/month and
5 GB egress on its current free plan. Compute sleeps after 5 minutes idle.
This should be ample for a small class project with roughly 50–60 light users, but
free tiers have limits and are not a guaranteed production service.

DEPLOYMENT STEPS (FOLLOW IN ORDER)
1) Create a free GitHub account: https://github.com/
2) Create a new PRIVATE repository named Pocket IQ 2.0 (recommended because the code is yours).
3) Upload all files and folders from this extracted folder to the repository root.
   Make sure app.py, requirements.txt, templates/ and static/ are at the top level.
4) Create a free Neon account: https://neon.tech/ and create a project.
5) In Neon, open the project dashboard, choose Connect, and copy the PostgreSQL
   connection string. Use the pooled connection string if the console offers one.
   Keep it secret; do not paste it in public or send it in chat.
6) Create a free Render account: https://render.com/ and choose New > Web Service.
7) Connect GitHub and select your Pocket IQ 2.0 repository.
8) Set these Render fields:
   Runtime: Python 3
   Build command: pip install -r requirements.txt
   Start command: gunicorn app:app
   Instance type: Free
9) In Render > Environment, add these variables:
   DATABASE_URL = the Neon PostgreSQL connection string
   POCKETIQ_SECRET = a long random secret generated on your computer
   POCKETIQ_ADMIN_PASSWORD = a new strong admin password you choose
10) Deploy. Wait for the deploy to finish, then open the *.onrender.com URL.
11) Register a test account, add an expense, log out, and log back in.
    Open the same URL on your phone and sign in to verify the same account data appears.

GENERATE A SECRET (Windows PowerShell)
Run: py -c "import secrets; print(secrets.token_hex(32))"
Copy the output into Render's POCKETIQ_SECRET environment variable. Do not share it.

ADMIN QUOTES
Open https://YOUR-RENDER-URL/admin and use the POCKETIQ_ADMIN_PASSWORD you set
in Render. Use this area to add/edit/upload saving quotes.

IMPORTANT LIMITATIONS
- Render Free web services sleep after 15 minutes without requests and can take about
  one minute to wake up. Free services have ephemeral local filesystems.
- Neon Free allows 0.5 GB database storage, 100 compute-unit hours per project/month,
  and 5 GB monthly egress. Compute scales to zero after 5 minutes idle.
- If free quotas are exceeded, the database may suspend until the next monthly period
  or until usage is reduced. Back up your database regularly using Neon's export tools.
- Supabase Free is also a valid alternative database (500 MB, 5 GB egress, 50,000 MAU),
  but free projects may pause after a week of low activity. This app does not need
  Supabase Auth because it already has its own email/password login.
- Do not store bank card numbers, bank passwords, government IDs or highly sensitive
  financial details. This is a student project, not a regulated banking service.
- Use unique passwords. The app hashes user passwords, but you are responsible for
  protecting the hosting/database accounts and keeping backups.
- New cloud database starts empty. Existing expenses in the local pocketiq.db file
  are not automatically copied to Neon. Keep the old project/database as a backup.

LOCAL TESTING
py -m pip install -r requirements.txt
py app.py
Open http://127.0.0.1:5000
Without DATABASE_URL, the app uses a local SQLite database. With DATABASE_URL set,
it uses PostgreSQL (Neon).


POCKETIQ FEATURE UPDATE
-----------------------
The updated application also includes:
- Financial Health Score: user-specific monthly income and budget settings,
  with a transparent 50-point savings-rate + 50-point budget-control formula.
- Savings Goal Planner: create, edit, delete and track multiple personal goals.
- Weekly PDF Report: on-demand PDF with daily expenses, category totals,
  highest-spending category, like-for-like previous-week comparison, goal progress,
  health score and suggestions.

The database creates two additional tables automatically without deleting
existing data: finance_profiles and savings_goals. This migration runs at app
startup for both SQLite and PostgreSQL. Do not manually drop or recreate tables.

The PDF feature requires reportlab; it is included in requirements.txt.
After pulling the updated code, run:
    py -m pip install -r requirements.txt

Then run locally:
    py app.py
Open http://127.0.0.1:5000 and sign in. Set monthly income and budget in the
Financial Health Score section, create a savings goal, and download the weekly
PDF report. On Render, push the updated files to GitHub and deploy; Render will
install reportlab from requirements.txt during the build.
