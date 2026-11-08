# Pocket IQ 2.0

Cloud-hosted personal finance management web application.

## Features
- User registration and login
- User-specific expense tracking
- Daily, weekly and monthly analytics
- Simple interest, compound interest and EMI calculators (existing frontend)
- Financial Health Score based on monthly income, monthly budget, savings rate and budget control
- Multiple savings goals with progress and daily/monthly saving targets
- On-demand weekly PDF reports
- Admin quote management

## Deploy
1. Create a Neon PostgreSQL database and copy its connection string privately.
2. Create a Render Web Service from this GitHub repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Add environment variables `DATABASE_URL`, `POCKETIQ_SECRET`, and `POCKETIQ_ADMIN_PASSWORD`.
6. Deploy and open the Render URL.

The app creates its tables automatically. It does not connect to bank accounts or process payments. It is a student project, not a regulated banking service.
