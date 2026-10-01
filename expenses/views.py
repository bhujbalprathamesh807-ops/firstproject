from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

import razorpay

from .models import (
    Budget,
    Category,
    ContactMessage,
    EMI,
    Payment,
    Transaction,
    UserProfile,
)


# =========================================================
# HELPERS
# =========================================================

def get_razorpay_client():
    from django.conf import settings

    key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
    key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")

    if not key_id or not key_secret:
        return None

    return razorpay.Client(auth=(key_id, key_secret))


def ensure_user_profile(user):
    profile, created = UserProfile.objects.get_or_create(
        user=user,
        defaults={
            "monthly_income_target": Decimal("50000.00"),
            "currency": "INR",
            "subscription_active": True,
        },
    )

    if not profile.subscription_active:
        profile.subscription_active = True
        profile.save(update_fields=["subscription_active"])

    return profile


def create_default_categories(user):
    defaults = [
        ("Food", "🍔", "expense"),
        ("Transport", "🚗", "expense"),
        ("Shopping", "🛍️", "expense"),
        ("Bills", "💡", "expense"),
        ("Education", "📚", "expense"),
        ("Health", "🏥", "expense"),
        ("Entertainment", "🎬", "expense"),
        ("Salary", "💰", "income"),
        ("Other", "📦", "expense"),
    ]

    for name, icon, kind in defaults:
        Category.objects.get_or_create(
            name=name,
            owner=user,
            defaults={
                "icon": icon,
                "kind": kind,
            },
        )


# =========================================================
# HEALTH CHECK
# =========================================================

def health_check(request):
    return JsonResponse({
        "status": "ok",
        "service": "Smart Expense Tracker",
    })


# =========================================================
# HOME
# =========================================================

def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    return render(request, "home.html")


# =========================================================
# PAYMENT - ₹99 NEW USER REGISTRATION
# =========================================================

def payment(request):
    # Existing logged-in users do not need to pay again
    if request.user.is_authenticated:
        return redirect("dashboard")

    client = get_razorpay_client()

    if client is None:
        messages.error(
            request,
            "Razorpay is not configured. Please add RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET in environment variables."
        )

        return render(
            request,
            "payment.html",
            {
                "razorpay_key_id": "",
                "order_id": "",
                "amount": 9900,
                "amount_display": "99.00",
                "currency": "INR",
            },
        )

    amount = Decimal("99.00")

    try:
        razorpay_order = client.order.create({
            "amount": 9900,
            "currency": "INR",
            "payment_capture": 1,
        })

        order_id = razorpay_order["id"]

        Payment.objects.create(
            user=None,
            order_id=order_id,
            amount=amount,
            status="created",
            purpose="access",
        )

        # Store payment requirement in session
        request.session["payment_order_id"] = order_id
        request.session["payment_next"] = "register"

        from django.conf import settings

        return render(
            request,
            "payment.html",
            {
                "razorpay_key_id": getattr(
                    settings,
                    "RAZORPAY_KEY_ID",
                    ""
                ),
                "order_id": order_id,
                "amount": 9900,
                "amount_display": "99.00",
                "currency": "INR",
            },
        )

    except Exception as e:
        messages.error(
            request,
            f"Unable to create payment order: {str(e)}"
        )

        return render(
            request,
            "payment.html",
            {
                "razorpay_key_id": "",
                "order_id": "",
                "amount": 9900,
                "amount_display": "99.00",
                "currency": "INR",
            },
        )


# =========================================================
# PAYMENT SUCCESS
# =========================================================

@csrf_exempt
def payment_success(request):

    if request.method != "POST":
        return redirect("payment")

    razorpay_payment_id = request.POST.get("razorpay_payment_id")
    razorpay_order_id = request.POST.get("razorpay_order_id")
    razorpay_signature = request.POST.get("razorpay_signature")

    if not razorpay_payment_id or not razorpay_order_id or not razorpay_signature:
        messages.error(request, "Payment information is incomplete.")
        return redirect("payment")

    payment = Payment.objects.filter(
        order_id=razorpay_order_id
    ).first()

    if not payment:
        messages.error(request, "Payment order was not found.")
        return redirect("payment")

    if payment.status == "paid":
        request.session["payment_completed"] = True
        return redirect("register")

    client = get_razorpay_client()

    if client is None:
        messages.error(request, "Razorpay configuration is missing.")
        return redirect("payment")

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature,
        })

        payment.payment_id = razorpay_payment_id
        payment.signature = razorpay_signature
        payment.status = "paid"
        payment.paid_at = timezone.now()
        payment.save()

        request.session["payment_completed"] = True
        request.session["payment_order_id"] = razorpay_order_id

        messages.success(
            request,
            "Payment successful. You can now create your account."
        )

        return redirect("register")

    except Exception:
        payment.status = "failed"
        payment.save(update_fields=["status"])

        messages.error(
            request,
            "Payment verification failed. Please try again."
        )

        return redirect("payment")


# =========================================================
# REGISTER
# =========================================================

def register(request):

    # User must complete ₹99 payment before registration
    if not request.user.is_authenticated:
        if not request.session.get("payment_completed"):
            return redirect("payment")

    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":

        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if not username or not email or not password:
            messages.error(request, "Please fill all required fields.")
            return render(request, "register.html")

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "register.html")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return render(request, "register.html")

        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already exists.")
            return render(request, "register.html")

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
        )

        create_default_categories(user)
        ensure_user_profile(user)

        # Link successful payment with the new user
        order_id = request.session.get("payment_order_id")

        if order_id:
            Payment.objects.filter(
                order_id=order_id,
                status="paid",
            ).update(user=user)

        login(request, user)

        # Clear payment session
        request.session.pop("payment_completed", None)
        request.session.pop("payment_order_id", None)
        request.session.pop("payment_next", None)

        messages.success(
            request,
            "Account created successfully!"
        )

        return redirect("dashboard")

    return render(request, "register.html")


# =========================================================
# LOGIN
# =========================================================

def login_view(request):

    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":

        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(
            request,
            username=username,
            password=password,
        )

        if user is not None:
            login(request, user)

            ensure_user_profile(user)

            messages.success(
                request,
                "Welcome back!"
            )

            return redirect("dashboard")

        messages.error(
            request,
            "Invalid username or password."
        )

    return render(request, "login.html")


# =========================================================
# LOGOUT
# =========================================================

@login_required
def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("home")


# =========================================================
# DASHBOARD
# =========================================================

@login_required
def dashboard(request):

    transactions = Transaction.objects.filter(
        user=request.user
    )

    total_income = transactions.filter(
        transaction_type="income"
    ).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    total_expense = transactions.filter(
        transaction_type="expense"
    ).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    balance = total_income - total_expense

    recent_transactions = transactions[:10]

    context = {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance,
        "recent_transactions": recent_transactions,
    }

    return render(
        request,
        "dashboard.html",
        context
    )


# =========================================================
# TRANSACTIONS
# =========================================================

@login_required
def transactions(request):

    transaction_list = Transaction.objects.filter(
        user=request.user
    )

    return render(
        request,
        "transactions.html",
        {
            "transactions": transaction_list,
        },
    )


# =========================================================
# ADD TRANSACTION
# =========================================================

@login_required
def add_transaction(request):

    categories = Category.objects.filter(
        owner=request.user
    )

    if request.method == "POST":

        try:
            title = request.POST.get("title", "").strip()
            amount = Decimal(
                request.POST.get("amount", "0")
            )
            category_id = request.POST.get("category")
            transaction_type = request.POST.get(
                "transaction_type",
                "expense"
            )
            payment_method = request.POST.get(
                "payment_method",
                "UPI"
            )
            date = request.POST.get("date") or timezone.localdate()
            note = request.POST.get("note", "").strip()
            receipt_items = request.POST.get(
                "receipt_items",
                ""
            ).strip()
            receipt_number = request.POST.get(
                "receipt_number",
                ""
            ).strip()

            if not title:
                messages.error(
                    request,
                    "Transaction title is required."
                )
                return render(
                    request,
                    "add_transaction.html",
                    {"categories": categories},
                )

            if amount <= 0:
                messages.error(
                    request,
                    "Amount must be greater than zero."
                )
                return render(
                    request,
                    "add_transaction.html",
                    {"categories": categories},
                )

            category = get_object_or_404(
                Category,
                id=category_id,
                owner=request.user,
            )

            transaction = Transaction.objects.create(
                user=request.user,
                title=title,
                amount=amount,
                category=category,
                transaction_type=transaction_type,
                payment_method=payment_method,
                date=date,
                note=note,
                receipt_items=receipt_items,
                receipt_number=receipt_number,
            )

            if request.FILES.get("receipt_image"):
                transaction.receipt_image = request.FILES[
                    "receipt_image"
                ]
                transaction.save()

            messages.success(
                request,
                "Transaction added successfully."
            )

            return redirect("transactions")

        except (InvalidOperation, ValueError):
            messages.error(
                request,
                "Please enter a valid amount."
            )

    return render(
        request,
        "add_transaction.html",
        {
            "categories": categories,
        },
    )


# =========================================================
# EDIT TRANSACTION
# =========================================================

@login_required
def edit_transaction(request, pk):

    transaction = get_object_or_404(
        Transaction,
        pk=pk,
        user=request.user,
    )

    categories = Category.objects.filter(
        owner=request.user
    )

    if request.method == "POST":

        try:
            transaction.title = request.POST.get(
                "title",
                transaction.title
            ).strip()

            amount_value = request.POST.get(
                "amount",
                str(transaction.amount)
            )

            transaction.amount = Decimal(amount_value)

            category_id = request.POST.get("category")

            transaction.category = get_object_or_404(
                Category,
                id=category_id,
                owner=request.user,
            )

            transaction.transaction_type = request.POST.get(
                "transaction_type",
                transaction.transaction_type
            )

            transaction.payment_method = request.POST.get(
                "payment_method",
                transaction.payment_method
            )

            transaction.date = request.POST.get(
                "date"
            ) or transaction.date

            transaction.note = request.POST.get(
                "note",
                ""
            ).strip()

            transaction.receipt_items = request.POST.get(
                "receipt_items",
                ""
            ).strip()

            transaction.receipt_number = request.POST.get(
                "receipt_number",
                ""
            ).strip()

            if request.FILES.get("receipt_image"):
                transaction.receipt_image = request.FILES[
                    "receipt_image"
                ]

            transaction.save()

            messages.success(
                request,
                "Transaction updated successfully."
            )

            return redirect("transactions")

        except (InvalidOperation, ValueError):
            messages.error(
                request,
                "Please enter valid transaction details."
            )

    return render(
        request,
        "edit_transaction.html",
        {
            "transaction": transaction,
            "categories": categories,
        },
    )


# =========================================================
# DELETE TRANSACTION
# =========================================================

@login_required
def delete_transaction(request, pk):

    transaction = get_object_or_404(
        Transaction,
        pk=pk,
        user=request.user,
    )

    if request.method == "POST":
        transaction.delete()

        messages.success(
            request,
            "Transaction deleted successfully."
        )

    return redirect("transactions")


# =========================================================
# BUDGETS
# =========================================================

@login_required
def budgets(request):

    categories = Category.objects.filter(
        owner=request.user,
        kind="expense",
    )

    budget_list = Budget.objects.filter(
        user=request.user
    )

    if request.method == "POST":

        try:
            category_id = request.POST.get("category")
            month = request.POST.get("month")
            amount = Decimal(
                request.POST.get("amount", "0")
            )

            category = get_object_or_404(
                Category,
                id=category_id,
                owner=request.user,
            )

            if not month:
                messages.error(
                    request,
                    "Please select a month."
                )

                return render(
                    request,
                    "budgets.html",
                    {
                        "categories": categories,
                        "budgets": budget_list,
                    },
                )

            if amount <= 0:
                messages.error(
                    request,
                    "Budget amount must be greater than zero."
                )

                return render(
                    request,
                    "budgets.html",
                    {
                        "categories": categories,
                        "budgets": budget_list,
                    },
                )

            Budget.objects.update_or_create(
                user=request.user,
                category=category,
                month=month,
                defaults={
                    "amount": amount,
                },
            )

            messages.success(
                request,
                "Budget saved successfully."
            )

            return redirect("budgets")

        except (InvalidOperation, ValueError):
            messages.error(
                request,
                "Please enter a valid budget amount."
            )

    return render(
        request,
        "budgets.html",
        {
            "categories": categories,
            "budgets": budget_list,
        },
    )


# =========================================================
# REPORTS
# =========================================================

@login_required
def reports(request):

    transactions = Transaction.objects.filter(
        user=request.user
    )

    total_income = transactions.filter(
        transaction_type="income"
    ).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    total_expense = transactions.filter(
        transaction_type="expense"
    ).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    category_expenses = (
        transactions
        .filter(transaction_type="expense")
        .values(
            "category__name"
        )
        .annotate(
            total=Sum("amount")
        )
        .order_by("-total")
    )

    context = {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": total_income - total_expense,
        "category_expenses": category_expenses,
    }

    return render(
        request,
        "reports.html",
        context
    )


# =========================================================
# EMI LIST + ADD
# =========================================================

@login_required
def emi_list(request):

    emi_list_data = EMI.objects.filter(
        user=request.user
    )

    if request.method == "POST":

        try:
            lender = request.POST.get(
                "lender",
                ""
            ).strip()

            loan_name = request.POST.get(
                "loan_name",
                ""
            ).strip()

            principal = Decimal(
                request.POST.get(
                    "principal",
                    "0"
                )
            )

            annual_interest_rate = Decimal(
                request.POST.get(
                    "annual_interest_rate",
                    "0"
                )
            )

            tenure_months = int(
                request.POST.get(
                    "tenure_months",
                    "0"
                )
            )

            start_date = request.POST.get(
                "start_date"
            ) or timezone.localdate()

            if principal <= 0 or tenure_months <= 0:
                messages.error(
                    request,
                    "Please enter valid loan details."
                )

                return render(
                    request,
                    "emi.html",
                    {
                        "emis": emi_list_data,
                    },
                )

            if annual_interest_rate < 0:
                annual_interest_rate = Decimal("0")

            monthly_rate = (
                annual_interest_rate /
                Decimal("1200")
            )

            if monthly_rate == 0:
                monthly_emi = (
                    principal /
                    Decimal(tenure_months)
                )
            else:
                factor = (
                    Decimal("1") +
                    monthly_rate
                ) ** tenure_months

                monthly_emi = (
                    principal *
                    monthly_rate *
                    factor /
                    (factor - Decimal("1"))
                )

            total_payable = (
                monthly_emi *
                Decimal(tenure_months)
            )

            total_interest = (
                total_payable -
                principal
            )

            EMI.objects.create(
                user=request.user,
                lender=lender,
                loan_name=loan_name,
                principal=principal,
                annual_interest_rate=annual_interest_rate,
                tenure_months=tenure_months,
                start_date=start_date,
                monthly_emi=monthly_emi,
                total_interest=total_interest,
                total_payable=total_payable,
            )

            messages.success(
                request,
                "EMI added successfully."
            )

            return redirect("emi")

        except (InvalidOperation, ValueError):
            messages.error(
                request,
                "Please enter valid EMI details."
            )

    return render(
        request,
        "emi.html",
        {
            "emis": emi_list_data,
        },
    )


# =========================================================
# EMI PAY
# =========================================================

@login_required
def emi_pay(request, pk):

    emi = get_object_or_404(
        EMI,
        pk=pk,
        user=request.user,
    )

    if request.method == "POST":

        if emi.paid_installments < emi.tenure_months:
            emi.paid_installments += 1
            emi.save(
                update_fields=[
                    "paid_installments"
                ]
            )

            messages.success(
                request,
                "EMI installment marked as paid."
            )
        else:
            messages.info(
                request,
                "All EMI installments are already paid."
            )

    return redirect("emi")


# =========================================================
# PROFILE
# =========================================================

@login_required
def profile(request):

    profile = ensure_user_profile(
        request.user
    )

    if request.method == "POST":

        monthly_income_target = request.POST.get(
            "monthly_income_target"
        )

        currency = request.POST.get(
            "currency",
            profile.currency
        )

        try:
            if monthly_income_target:
                profile.monthly_income_target = Decimal(
                    monthly_income_target
                )

            profile.currency = currency

            if request.FILES.get("avatar"):
                profile.avatar = request.FILES[
                    "avatar"
                ]

            profile.save()

            messages.success(
                request,
                "Profile updated successfully."
            )

            return redirect("profile")

        except InvalidOperation:
            messages.error(
                request,
                "Please enter a valid income target."
            )

    return render(
        request,
        "profile.html",
        {
            "profile": profile,
        },
    )


# =========================================================
# CONTACT
# =========================================================

def contact(request):

    if request.method == "POST":

        name = request.POST.get(
            "name",
            ""
        ).strip()

        email = request.POST.get(
            "email",
            ""
        ).strip()

        subject = request.POST.get(
            "subject",
            ""
        ).strip()

        message = request.POST.get(
            "message",
            ""
        ).strip()

        if not name or not email or not subject or not message:
            messages.error(
                request,
                "Please fill all fields."
            )

            return render(
                request,
                "contact.html"
            )

        ContactMessage.objects.create(
            name=name,
            email=email,
            subject=subject,
            message=message,
        )

        messages.success(
            request,
            "Your message has been sent successfully."
        )

        return redirect("contact")

    return render(
        request,
        "contact.html"
    )