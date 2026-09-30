from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.utils import timezone

from .forms import BudgetForm, EMIForm, ProfileForm, TransactionForm
from .models import Budget, Category, ContactMessage, EMI, Payment, Transaction, UserProfile

REGISTRATION_FEE = Decimal("99.00")


def seed_categories(user):
    defaults = [
        ("Food", "🍔", "#f97316", "expense"),
        ("Transport", "🚗", "#0ea5e9", "expense"),
        ("Shopping", "🛍️", "#ec4899", "expense"),
        ("Bills", "💡", "#8b5cf6", "expense"),
        ("Health", "❤️", "#ef4444", "expense"),
        ("Entertainment", "🎬", "#14b8a6", "expense"),
        ("Education", "📚", "#6366f1", "expense"),
        ("Salary", "💼", "#22c55e", "income"),
        ("Other", "✨", "#64748b", "expense"),
    ]
    for name, icon, color, kind in defaults:
        Category.objects.get_or_create(
            name=name,
            owner=user,
            defaults={"icon": icon, "color": color, "kind": kind},
        )


def health_check(request):
    return JsonResponse({"status": "ok", "service": "smart-expense-tracker"})

def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "home.html", {"registration_fee": REGISTRATION_FEE})


def _razorpay_credentials():
    return os.getenv("RAZORPAY_KEY_ID", "").strip(), os.getenv("RAZORPAY_KEY_SECRET", "").strip()


def _create_razorpay_order(amount_paise, receipt):
    key_id, key_secret = _razorpay_credentials()
    if not key_id or not key_secret:
        raise RuntimeError("Razorpay test keys are not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.")
    payload = json.dumps({
        "amount": int(amount_paise),
        "currency": "INR",
        "receipt": receipt,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.razorpay.com/v1/orders",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    auth = (f"{key_id}:{key_secret}").encode("utf-8")
    import base64
    request.add_header("Authorization", "Basic " + base64.b64encode(auth).decode("ascii"))
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_razorpay_payment(payment_id):
    key_id, key_secret = _razorpay_credentials()
    if not key_id or not key_secret:
        return None
    request = urllib.request.Request(
        f"https://api.razorpay.com/v1/payments/{payment_id}",
        method="GET",
    )
    import base64
    auth = (f"{key_id}:{key_secret}").encode("utf-8")
    request.add_header("Authorization", "Basic " + base64.b64encode(auth).decode("ascii"))
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _verify_razorpay_signature(order_id, payment_id, signature):
    _, key_secret = _razorpay_credentials()
    if not key_secret:
        return False
    message = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(key_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def payment(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    next_page = request.GET.get("next", "") or request.session.get("payment_next", "login")
    if next_page not in {"login", "register"}:
        next_page = "login"
    request.session["payment_next"] = next_page

    key_id, key_secret = _razorpay_credentials()
    if not key_id or not key_secret:
        return render(request, "payment.html", {
            "registration_fee": REGISTRATION_FEE,
            "razorpay_configured": False,
        })

    try:
        order = _create_razorpay_order(9900, f"se_access_{timezone.now().strftime('%Y%m%d%H%M%S%f')}")
        Payment.objects.create(order_id=order["id"], amount=REGISTRATION_FEE, purpose="access")
    except Exception as exc:
        messages.error(request, f"Could not create the payment order: {exc}")
        return render(request, "payment.html", {
            "registration_fee": REGISTRATION_FEE,
            "razorpay_configured": False,
            "payment_error": str(exc),
        })

    request.session["pending_payment_order_id"] = order["id"]
    return render(request, "payment.html", {
        "registration_fee": REGISTRATION_FEE,
        "razorpay_configured": True,
        "razorpay_key_id": key_id,
        "razorpay_order_id": order["id"],
    })


def payment_success(request):
    if request.method != "POST":
        return redirect("payment")

    order_id = request.POST.get("razorpay_order_id", "").strip()
    payment_id = request.POST.get("razorpay_payment_id", "").strip()
    signature = request.POST.get("razorpay_signature", "").strip()
    expected_order = request.session.get("pending_payment_order_id")
    if not order_id or order_id != expected_order or not payment_id or not signature:
        messages.error(request, "Payment verification data is missing or invalid.")
        return redirect("payment")

    payment = get_object_or_404(Payment, order_id=order_id, status="created")
    if not _verify_razorpay_signature(order_id, payment_id, signature):
        payment.status = "failed"
        payment.save(update_fields=["status"])
        messages.error(request, "Payment verification failed. No access was granted.")
        return redirect("payment")

    try:
        gateway_payment = _fetch_razorpay_payment(payment_id)
    except Exception:
        gateway_payment = None
    if not gateway_payment or gateway_payment.get("status") != "captured" or int(gateway_payment.get("amount", 0)) != 9900 or gateway_payment.get("currency") != "INR":
        payment.status = "failed"
        payment.save(update_fields=["status"])
        messages.error(request, "The gateway could not confirm the ₹99 payment. No access was granted.")
        return redirect("payment")

    payment.payment_id = payment_id
    payment.signature = signature
    payment.status = "paid"
    payment.paid_at = timezone.now()
    payment.save(update_fields=["payment_id", "signature", "status", "paid_at"])
    request.session["payment_completed"] = True
    request.session["payment_amount"] = str(REGISTRATION_FEE)
    request.session["paid_payment_record_id"] = payment.id
    request.session.pop("pending_payment_order_id", None)
    next_page = request.session.pop("payment_next", "login")
    messages.success(request, "₹99 payment verified successfully. Continue to your account.")
    return redirect(next_page)


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if not request.session.get("payment_completed"):
        request.session["payment_next"] = "register"
        return redirect("payment")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        confirm = request.POST.get("confirm_password", "")

        if not username or not email or not password or not confirm:
            messages.error(request, "Please fill in all required fields.")
            return render(request, "registration/register.html")
        if len(password) < 6:
            messages.error(request, "Password must contain at least 6 characters.")
            return render(request, "registration/register.html")
        if password != confirm:
            messages.error(request, "Passwords do not match.")
            return render(request, "registration/register.html")
        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, "Username already exists. Please choose another username.")
            return render(request, "registration/register.html")
        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "This email is already registered.")
            return render(request, "registration/register.html")

        try:
            user = User.objects.create_user(username=username, email=email, password=password, first_name=first_name)
            seed_categories(user)
            UserProfile.objects.create(user=user, subscription_active=True)
            paid_id = request.session.get("paid_payment_record_id")
            if paid_id:
                Payment.objects.filter(id=paid_id, status="paid").update(user=user)
        except IntegrityError:
            messages.error(request, "Could not create the account. Please try another username.")
            return render(request, "registration/register.html")

        login(request, user)
        request.session.pop("payment_completed", None)
        request.session.pop("payment_amount", None)
        request.session.pop("paid_payment_record_id", None)
        messages.success(request, "Account created successfully. Welcome to SmartExpense!")
        return redirect("dashboard")

    return render(request, "registration/register.html")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if not request.session.get("payment_completed"):
        request.session["payment_next"] = "login"
        return redirect("payment")
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            profile_obj, _ = UserProfile.objects.get_or_create(user=user)
            if not profile_obj.subscription_active:
                profile_obj.subscription_active = True
                profile_obj.save(update_fields=["subscription_active"])
            paid_id = request.session.get("paid_payment_record_id")
            if paid_id:
                Payment.objects.filter(id=paid_id, status="paid").update(user=user)
            login(request, user)
            request.session.pop("payment_completed", None)
            request.session.pop("payment_amount", None)
            request.session.pop("paid_payment_record_id", None)
            messages.success(request, "Payment verified and login successful. Welcome back!")
            return redirect(request.GET.get("next") if request.GET.get("next", "").startswith("/") else "dashboard")
        messages.error(request, "Invalid username or password.")
    return render(request, "registration/login.html")


def logout_view(request):
    if request.method == "POST":
        logout(request)
    return redirect("home")


@login_required
def dashboard(request):
    seed_categories(request.user)
    today = timezone.localdate()
    start = today.replace(day=1)
    month_tx = Transaction.objects.filter(user=request.user, date__gte=start, date__lte=today)
    income = month_tx.filter(transaction_type="income").aggregate(v=Sum("amount"))["v"] or Decimal("0")
    expense = month_tx.filter(transaction_type="expense").aggregate(v=Sum("amount"))["v"] or Decimal("0")
    categories = Category.objects.filter(owner=request.user, kind="expense")
    cat_rows = []
    for category in categories:
        total = month_tx.filter(category=category, transaction_type="expense").aggregate(v=Sum("amount"))["v"] or Decimal("0")
        if total:
            cat_rows.append({"name": category.name, "value": float(total), "icon": category.icon, "color": category.color})
    cat_rows.sort(key=lambda x: x["value"], reverse=True)
    return render(request, "dashboard.html", {
        "income": income, "expense": expense, "balance": income - expense,
        "recent": Transaction.objects.filter(user=request.user)[:6],
        "cat_rows": cat_rows[:6],
        "budget_total": Budget.objects.filter(user=request.user, month=start).aggregate(v=Sum("amount"))["v"] or Decimal("0"),
        "month_name": today.strftime("%B %Y"),
        "emi_count": EMI.objects.filter(user=request.user).count(),
    })


@login_required
def transactions(request):
    qs = Transaction.objects.filter(user=request.user)
    q = request.GET.get("q", "").strip()
    typ = request.GET.get("type", "")
    category = request.GET.get("category", "")
    if q:
        qs = qs.filter(title__icontains=q)
    if typ in {"income", "expense"}:
        qs = qs.filter(transaction_type=typ)
    if category.isdigit():
        qs = qs.filter(category_id=category)
    return render(request, "transactions.html", {
        "transactions": qs.order_by("-date", "-id"),
        "categories": Category.objects.filter(owner=request.user),
        "q": q, "typ": typ, "category": category,
    })


@login_required
def add_transaction(request):
    seed_categories(request.user)
    form = TransactionForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        obj.save()
        messages.success(request, "Transaction added successfully.")
        return redirect("transactions")
    return render(request, "transaction_form.html", {"form": form, "title": "Add Transaction"})


@login_required
def edit_transaction(request, pk):
    obj = get_object_or_404(Transaction, pk=pk, user=request.user)
    form = TransactionForm(request.POST or None, request.FILES or None, instance=obj, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Transaction updated successfully.")
        return redirect("transactions")
    return render(request, "transaction_form.html", {"form": form, "title": "Edit Transaction", "scanner": False})


@login_required
def delete_transaction(request, pk):
    obj = get_object_or_404(Transaction, pk=pk, user=request.user)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Transaction deleted successfully.")
    return redirect("transactions")


@login_required
def budgets(request):
    month = timezone.localdate().replace(day=1)
    form = BudgetForm(request.POST or None, user=request.user, initial={"month": month})
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        obj.month = month
        obj.save()
        messages.success(request, "Budget saved successfully.")
        return redirect("budgets")
    data = []
    for budget in Budget.objects.filter(user=request.user, month=month):
        spent = Transaction.objects.filter(user=request.user, category=budget.category, transaction_type="expense", date__year=month.year, date__month=month.month).aggregate(v=Sum("amount"))["v"] or Decimal("0")
        pct = min(100, float(spent / budget.amount * 100)) if budget.amount else 0
        data.append({"budget": budget, "spent": spent, "pct": pct, "remaining": budget.amount - spent})
    return render(request, "budgets.html", {"items": data, "form": form})


@login_required
def reports(request):
    today = timezone.localdate()
    rows = []
    for category in Category.objects.filter(owner=request.user, kind="expense"):
        total = Transaction.objects.filter(user=request.user, category=category, transaction_type="expense", date__year=today.year).aggregate(v=Sum("amount"))["v"] or Decimal("0")
        if total:
            rows.append({"name": category.name, "value": float(total), "icon": category.icon})
    monthly = []
    for i in range(5, -1, -1):
        y = today.year + (today.month - i - 1) // 12
        m = (today.month - i - 1) % 12 + 1
        total = Transaction.objects.filter(user=request.user, transaction_type="expense", date__year=y, date__month=m).aggregate(v=Sum("amount"))["v"] or Decimal("0")
        monthly.append({"label": date(y, m, 1).strftime("%b"), "value": float(total)})
    return render(request, "reports.html", {"rows": rows, "monthly": monthly})


@login_required
def emi_list(request):
    form = EMIForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        principal = Decimal(obj.principal)
        annual = Decimal(obj.annual_interest_rate)
        n = int(obj.tenure_months)
        if principal <= 0 or n <= 0 or annual < 0:
            form.add_error(None, "Enter a principal greater than 0, a positive tenure, and a non-negative interest rate.")
            emis = EMI.objects.filter(user=request.user)
            return render(request, "emi.html", {"form": form, "emis": emis})
        monthly_rate = annual / Decimal("1200")
        if monthly_rate == 0:
            emi = principal / Decimal(n)
        else:
            factor = (Decimal(1) + monthly_rate) ** n
            emi = principal * monthly_rate * factor / (factor - Decimal(1))
        emi = emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total = (emi * n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        obj.monthly_emi = emi
        obj.total_payable = total
        obj.total_interest = max(total - principal, Decimal("0"))
        obj.save()
        messages.success(request, f"EMI created. Monthly EMI: ₹{emi:,.2f}")
        return redirect("emi")
    emis = EMI.objects.filter(user=request.user)
    return render(request, "emi.html", {"form": form, "emis": emis})


@login_required
def emi_pay(request, pk):
    emi = get_object_or_404(EMI, pk=pk, user=request.user)
    if request.method == "POST" and emi.paid_installments < emi.tenure_months:
        seed_categories(request.user)
        category = Category.objects.get(owner=request.user, name="Other")
        Transaction.objects.create(
            user=request.user,
            title=f"EMI - {emi.loan_name}",
            amount=emi.monthly_emi,
            category=category,
            transaction_type="expense",
            payment_method="Bank",
            date=timezone.localdate(),
            note=f"EMI installment {emi.paid_installments + 1} of {emi.tenure_months}",
        )
        emi.paid_installments += 1
        emi.save(update_fields=["paid_installments"])
        messages.success(request, "EMI installment recorded and added to Transactions.")
    return redirect("emi")


@login_required
def profile(request):
    profile_obj, _ = UserProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, request.FILES or None, instance=profile_obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated successfully.")
        return redirect("profile")
    return render(request, "profile.html", {"form": form, "profile": profile_obj})


def contact(request):
    if request.method == "POST":
        data = {k: request.POST.get(k, "").strip() for k in ("name", "email", "subject", "message")}
        if not all(data.values()):
            messages.error(request, "Please fill in all fields.")
        else:
            ContactMessage.objects.create(**data)
            messages.success(request, "Your message has been sent successfully!")
            return redirect("contact")
    return render(request, "contact.html")
