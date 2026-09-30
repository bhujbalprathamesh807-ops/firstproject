# Smart Expense Tracker — FINAL CLEAN BUILD (FREE ACCOUNT + OPTIONAL UPI)

This version is prepared for a Windows + VS Code + Python 3.14 local setup.

## Included
- Required one-time ₹99 Razorpay payment before account access
- Small responsive UPI QR (the supplied QR image is kept as-is)
- Receiver: Prathamesh Bhujbal
- UPI ID: bhujbalprathamesh807@oksbi
- Razorpay Test Mode checkout with ₹99 amount
- Optional payment page; Create Account can be opened directly
- Login / logout
- Dashboard with income, expense and balance
- Add / edit / delete transactions
- Screenshot/receipt OCR auto-fill on Add Transaction (requires internet for the Tesseract.js CDN)
- Search and filters
- Monthly budgets
- Reports and charts
- EMI manager with EMI, interest and total payable calculation
- Record EMI installment directly into Transactions
- Profile and contact page
- SQLite database
- Responsive layout

## IMPORTANT: open the correct folder
After extracting the ZIP, the folder structure is:

Smart_Expense_Tracker_FINAL/
└── smart_expense_tracker/
    ├── manage.py
    ├── requirements.txt
    ├── expenses/
    ├── templates/
    └── static/

In VS Code, open the INNER `smart_expense_tracker` folder — the folder that contains `manage.py`.

## Windows PowerShell — exact commands

### 1. Confirm the folder
Run:

```powershell
dir
```

You must see `manage.py` and `requirements.txt`.

If you do not see them, run:

```powershell
cd smart_expense_tracker
```

Then run `dir` again.

### 2. Create virtual environment

```powershell
py -m venv .venv
```

### 3. Activate it

```powershell
.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

### 4. Install packages

```powershell
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

### 5. Check Django configuration

```powershell
py manage.py check
```

The expected result is:

`System check identified no issues (0 silenced).`

### 6. Create the SQLite database tables

```powershell
py manage.py migrate
```

### 7. Start the website

```powershell
py manage.py runserver
```

Open:

`http://127.0.0.1:8000/`

## If you see "requirements.txt not found"
You are one folder too high. Run:

```powershell
cd smart_expense_tracker
```

Then repeat the install command.

## If you see "manage.py not found"
Run:

```powershell
Get-ChildItem -Recurse -Filter manage.py
```

Go to the directory shown by the command, then run the Django commands there.

## Payment note
The ₹99 payment is required before login/account creation. Razorpay Test Mode order creation and server-side signature verification are included. Never commit the Razorpay secret to source control. For production, switch to Live Mode only after testing and secure deployment. The receipt scanner uses Tesseract.js in the browser, so the browser needs internet access to download the OCR library.

## Razorpay Test Mode setup

1. In Razorpay Dashboard, keep **Test Mode** enabled and use the Test API Key ID and Secret. Razorpay's current Python integration flow requires creating an order on the server and passing that order ID to Checkout; successful payments should be verified server-side. 
2. In PowerShell, after activating `venv`, set the keys for the current terminal:

```powershell
$env:RAZORPAY_KEY_ID="rzp_test_your_key_id"
$env:RAZORPAY_KEY_SECRET="your_test_key_secret"
```

3. Run migrations:

```powershell
python manage.py migrate
```

4. Start Django:

```powershell
python manage.py runserver
```

Never put the Razorpay secret directly in templates or commit it to GitHub. Replace Test credentials with Live credentials only after the full Test Mode flow works.
