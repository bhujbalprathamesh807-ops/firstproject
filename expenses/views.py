import os
from decimal import Decimal

import razorpay

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError, connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from .models import (
    Budget,
    Category,
    EMI,
    Payment,
    Transaction,
    UserProfile,
)


REGISTRATION_FEE = Decimal("99.00")


# =========================================================
# HOME
# =========================================================

def home(request):
    return render(request, "home.html")


# =========================================================
# DATABASE HEALTH CHECK
# =========================================================

def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

        return JsonResponse({
            "status": "ok",
            "database": "connected",
            "database_engine": connection.vendor,
            "service": "smart-expense-tracker",
        })

    except Exception as exc:
        return JsonResponse({
            "status": "error",
            "database": "not connected",
            "error": str(exc),
            "service": "smart-expense-tracker",
        }, status=500)


# =========================================================
# RAZORPAY CLIENT
# =========================================================

def get_razorpay_client():
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()

    if not key_id or not key_secret:
        return None

    return razorpay.Client(auth=(key_id, key_secret))


# =========================================================
# PAYMENT
# =========================================================

def payment(request):
    # Existing logged-in user should never pay again
    if request.user.is_authenticated:
        return redirect("dashboard")

    client = get_razorpay_client()

    if client is None:
        messages.error(
            request,
            "Payment service is not configured. Please try again later."
        )
        return redirect("home")

    amount_paise = int(REGISTRATION_FEE * 100)

    try:
        order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"expense_access_{request.session.session_key}",
            "payment_capture": 1,
        })

        payment_obj = Payment.objects.create(
            order_id=order["id"],
            amount=REGISTRATION_FEE,
            currency="INR",
            purpose="access",
            status="created",
        )

        request.session["pending_payment_order_id"] = order["id"]
        request.session["payment_next"] = "register"

        context = {
            "razorpay_key_id": os.getenv("RAZORPAY_KEY_ID", "").strip(),
            "order_id": order["id"],
            "amount": amount_paise,
            "amount_display": REGISTRATION_FEE,
            "currency": "INR",
            "payment_id": payment_obj.id,
        }

        return render(request, "payment.html", context)

    except Exception as exc:
        messages.error(
            request,
            f"Unable to start payment: {str(exc)}"
        )
        return redirect("home")


# =========================================================
# PAYMENT SUCCESS
# =========================================================

@csrf_exempt
def payment_success(request):
    if request.method != "POST":
        return redirect("payment")

    razorpay_order_id = request.POST.get(
        "razorpay_order_id", ""
    ).strip()

    razorpay_payment_id = request.POST.get(
        "razorpay_payment_id", ""
    ).strip()

    razorpay_signature = request.POST.get(
        "razorpay_signature", ""
    ).strip()

    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        messages.error(request, "Payment verification details are missing.")
        return redirect("payment")

    client = get_razorpay_client()

    if client is None:
        messages.error(
            request,
            "Payment service is not configured."
        )
        return redirect("payment")

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature,
        })

        payment_obj = get_object_or_404(
            Payment,
            order_id=razorpay_order_id
        )

        payment_obj.payment_id = razorpay_payment_id
        payment_obj.signature = razorpay_signature
        payment_obj.status = "paid"
        payment_obj.save()

        request.session["payment_completed"] = True
        request.session["pending_payment_order_id"] = razorpay_order_id

        messages.success(
            request,
            "₹99 payment successful. You can now create your account."
        )

        return redirect("register")

    except Exception as exc:
        messages.error(
            request,
            f"Payment verification failed: {str(exc)}"
        )
        return redirect("payment")


# =========================================================
# REGISTER
# =========================================================

def register(request):

    # Already logged-in user does not need registration
    if request.user.is_authenticated:
        return redirect("dashboard")

    # New account requires successful ₹99 payment
    payment_completed = request.session.get(
        "payment_completed",
        False
    )

    if not payment_completed:
        return redirect("payment")

    if request.method == "POST":

        username = request.POST.get(
            "username",
            ""
        ).strip()

        email = request.POST.get(
            "email",
            ""
        ).strip()

        password = request.POST.get(
            "password",
            ""
        )

        confirm_password = request.POST.get(
            "confirm_password",
            ""
        )

        if not username or not password:
            messages.error(
                request,
                "Username and password are required."
            )
            return render(
                request,
                "registration/register.html"
            )

        if password != confirm_password:
            messages.error(
                request,
                "Passwords do not match."
            )
            return render(
                request,
                "registration/register.html"
            )

        if User.objects.filter(
            username=username
        ).exists():

            messages.error(
                request,
                "Username already exists. Please choose another username."
            )

            return render(
                request,
                "registration/register.html"
            )

        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
            )

            # Create / activate profile
            profile_obj, _ = UserProfile.objects.get_or_create(
                user=user
            )

            profile_obj.subscription_active = True
            profile_obj.save(
                update_fields=["subscription_active"]
            )

            # Link payment to created user
            order_id = request.session.get(
                "pending_payment_order_id"
            )

            if order_id:
                paid_payment = Payment.objects.filter(
                    order_id=order_id
                ).first()

                if paid_payment:
                    paid_payment.user = user
                    paid_payment.status = "paid"
                    paid_payment.save()

            # Create default categories
            default_categories = [
                "Food",
                "Transport",
                "Shopping",
                "Bills",
                "Entertainment",
                "Health",
                "Education",
                "Salary",
                "Other",
            ]

            for category_name in default_categories:
                Category.objects.get_or_create(
                    user=user,
                    name=category_name,
                )

            login(request, user)

            # Clear payment session
            request.session.pop(
                "payment_completed",
                None
            )

            request.session.pop(
                "pending_payment_order_id",
                None
            )

            request.session.pop(
                "payment_next",
                None
            )

            messages.success(
                request,
                "Account created successfully. Welcome to SmartExpense!"
            )

            return redirect("dashboard")

        except IntegrityError:
            messages.error(
                request,
                "Username already exists. Please choose another username."
            )

        except Exception as exc:
            messages.error(
                request,
                f"Account creation failed: {str(exc)}"
            )

    return render(
        request,
        "registration/register.html"
    )


# =========================================================
# LOGIN
# =========================================================

def login_view(request):

    # Already logged in
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":

        username = request.POST.get(
            "username",
            ""
        ).strip()

        password = request.POST.get(
            "password",
            ""
        )

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:

            # IMPORTANT:
            # Existing users do NOT need ₹99 payment.
            profile_obj, _ = UserProfile.objects.get_or_create(
                user=user
            )

            if not profile_obj.subscription_active:
                profile_obj.subscription_active = True
                profile_obj.save(
                    update_fields=["subscription_active"]
                )

            login(request, user)

            messages.success(
                request,
                "Login successful. Welcome back!"
            )

            next_url = request.GET.get(
                "next",
                ""
            )

            if next_url.startswith("/"):
                return redirect(next_url)

            return redirect("dashboard")

        messages.error(
            request,
            "Invalid username or password."
        )

    return render(
        request,
        "registration/login.html"
    )


# =========================================================
# LOGOUT
# =========================================================

@login_required
def logout_view(request):

    logout(request)

    messages.success(
        request,
        "You have been logged out successfully."
    )

    return redirect("home")


# =========================================================
# DASHBOARD
# =========================================================

@login_required
def dashboard(request):

    transactions = Transaction.objects.filter(
        user=request.user
    ).order_by("-date", "-id")

    total_income = sum(
        transaction.amount
        for transaction in transactions
        if transaction.transaction_type == "income"
    )

    total_expense = sum(
        transaction.amount
        for transaction in transactions
        if transaction.transaction_type == "expense"
    )

    balance = total_income - total_expense

    recent_transactions = transactions[:5]

    context = {
        "transactions": recent_transactions,
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance,
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
    ).order_by("-date", "-id")

    context = {
        "transactions": transaction_list,
    }

    return render(
        request,
        "transactions.html",
        context
    )


@login_required
def add_transaction(request):

    if request.method == "POST":

        amount = request.POST.get(
            "amount",
            "0"
        )

        description = request.POST.get(
            "description",
            ""
        ).strip()

        transaction_type = request.POST.get(
            "transaction_type",
            "expense"
        )

        category_id = request.POST.get(
            "category"
        )

        date = request.POST.get(
            "date"
        )

        category = None

        if category_id:
            category = Category.objects.filter(
                id=category_id,
                user=request.user
            ).first()

        Transaction.objects.create(
            user=request.user,
            amount=amount,
            description=description,
            transaction_type=transaction_type,
            category=category,
            date=date,
        )

        messages.success(
            request,
            "Transaction added successfully."
        )

        return redirect("transactions")

    categories = Category.objects.filter(
        user=request.user
    )

    return render(
        request,
        "add_transaction.html",
        {
            "categories": categories,
        }
    )


@login_required
def edit_transaction(request, pk):

    transaction = get_object_or_404(
        Transaction,
        pk=pk,
        user=request.user
    )

    if request.method == "POST":

        transaction.amount = request.POST.get(
            "amount",
            transaction.amount
        )

        transaction.description = request.POST.get(
            "description",
            transaction.description
        ).strip()

        transaction.transaction_type = request.POST.get(
            "transaction_type",
            transaction.transaction_type
        )

        category_id = request.POST.get(
            "category"
        )

        if category_id:
            transaction.category = Category.objects.filter(
                id=category_id,
                user=request.user
            ).first()

        transaction.date = request.POST.get(
            "date",
            transaction.date
        )

        transaction.save()

        messages.success(
            request,
            "Transaction updated successfully."
        )

        return redirect("transactions")

    categories = Category.objects.filter(
        user=request.user
    )

    return render(
        request,
        "edit_transaction.html",
        {
            "transaction": transaction,
            "categories": categories,
        }
    )


@login_required
def delete_transaction(request, pk):

    transaction = get_object_or_404(
        Transaction,
        pk=pk,
        user=request.user
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

    budget_list = Budget.objects.filter(
        user=request.user
    ).order_by("-id")

    if request.method == "POST":

        category_id = request.POST.get(
            "category"
        )

        amount = request.POST.get(
            "amount"
        )

        category = Category.objects.filter(
            id=category_id,
            user=request.user
        ).first()

        if category and amount:

            Budget.objects.update_or_create(
                user=request.user,
                category=category,
                defaults={
                    "amount": amount,
                },
            )

            messages.success(
                request,
                "Budget saved successfully."
            )

            return redirect("budgets")

    categories = Category.objects.filter(
        user=request.user
    )

    return render(
        request,
        "budgets.html",
        {
            "budgets": budget_list,
            "categories": categories,
        }
    )


# =========================================================
# REPORTS
# =========================================================

@login_required
def reports(request):

    transaction_list = Transaction.objects.filter(
        user=request.user
    )

    total_income = sum(
        transaction.amount
        for transaction in transaction_list
        if transaction.transaction_type == "income"
    )

    total_expense = sum(
        transaction.amount
        for transaction in transaction_list
        if transaction.transaction_type == "expense"
    )

    balance = total_income - total_expense

    context = {
        "transactions": transaction_list,
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance,
    }

    return render(
        request,
        "reports.html",
        context
    )


# =========================================================
# EMI
# =========================================================

@login_required
def emi_list(request):

    emis = EMI.objects.filter(
        user=request.user
    ).order_by("-id")

    if request.method == "POST":

        name = request.POST.get(
            "name",
            ""
        ).strip()

        amount = request.POST.get(
            "amount"
        )

        due_date = request.POST.get(
            "due_date"
        )

        if name and amount:

            EMI.objects.create(
                user=request.user,
                name=name,
                amount=amount,
                due_date=due_date,
            )

            messages.success(
                request,
                "EMI added successfully."
            )

            return redirect("emi")

    return render(
        request,
        "emi.html",
        {
            "emis": emis,
        }
    )


@login_required
def emi_pay(request, pk):

    emi = get_object_or_404(
        EMI,
        pk=pk,
        user=request.user
    )

    if request.method == "POST":

        emi.is_paid = True
        emi.save()

        messages.success(
            request,
            "EMI marked as paid."
        )

    return redirect("emi")


# =========================================================
# PROFILE
# =========================================================

@login_required
def profile(request):

    profile_obj, _ = UserProfile.objects.get_or_create(
        user=request.user
    )

    if request.method == "POST":

        request.user.first_name = request.POST.get(
            "first_name",
            ""
        ).strip()

        request.user.last_name = request.POST.get(
            "last_name",
            ""
        ).strip()

        request.user.email = request.POST.get(
            "email",
            ""
        ).strip()

        request.user.save()

        messages.success(
            request,
            "Profile updated successfully."
        )

        return redirect("profile")

    return render(
        request,
        "profile.html",
        {
            "profile": profile_obj,
        }
    )


# =========================================================
# CONTACT
# =========================================================

@login_required
def contact(request):

    if request.method == "POST":

        messages.success(
            request,
            "Your message has been received."
        )

        return redirect("contact")

    return render(
        request,
        "contact.html"
    )