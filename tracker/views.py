import csv
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Count, ProtectedError
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Budget, Category, Transaction
from .serializers import BudgetSerializer, CategorySerializer, TransactionSerializer
from .services import (
    InvalidFilter,
    InvalidMonth,
    apply_transaction_filters,
    get_budget_statuses,
    get_category_totals,
    get_dashboard,
    get_monthly_summary,
    get_spend_by_category,
    get_trend,
    parse_month,
)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@login_required
def dashboard_view(request):
    today = timezone.localdate()
    return render(
        request,
        "tracker/dashboard.html",
        {
            "page_title": "Dashboard",
            "today": today.isoformat(),
            "current_month": today.strftime("%Y-%m"),
            "display_name": request.user.get_full_name() or request.user.username,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def month_from_query(request):
    try:
        return parse_month(request.query_params.get("month"))
    except InvalidMonth as exc:
        raise ValidationError({"detail": str(exc)}) from exc


def filter_or_400(queryset, params, **kwargs):
    try:
        return apply_transaction_filters(queryset, params, **kwargs)
    except InvalidFilter as exc:
        raise ValidationError({"detail": str(exc)}) from exc


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
class CategoryListCreateView(generics.ListCreateAPIView):
    serializer_class = CategorySerializer

    def get_queryset(self):
        return (
            Category.objects.filter(user=self.request.user)
            .annotate(transaction_count=Count("transactions"))
            .order_by("type", "name")
        )

    def perform_create(self, serializer):
        try:
            serializer.save(user=self.request.user)
        except IntegrityError as exc:
            raise ValidationError(
                {"name": "You already have a category with this name."}
            ) from exc


class CategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CategorySerializer

    def get_queryset(self):
        return Category.objects.filter(user=self.request.user).annotate(
            transaction_count=Count("transactions")
        )

    def perform_destroy(self, instance):
        if instance.transactions.exists():
            raise ValidationError(
                {
                    "detail": (
                        f"'{instance.name}' has transactions and cannot be deleted. "
                        "Remove or re-assign those transactions first."
                    )
                }
            )
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError(
                {"detail": "This category is in use and cannot be deleted."}
            ) from exc


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
class TransactionListCreateView(generics.ListCreateAPIView):
    serializer_class = TransactionSerializer

    def get_queryset(self):
        queryset = Transaction.objects.filter(
            user=self.request.user
        ).select_related("category")
        return filter_or_400(queryset, self.request.query_params)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class TransactionDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TransactionSerializer

    def get_queryset(self):
        return Transaction.objects.filter(user=self.request.user).select_related(
            "category"
        )


class TransactionExportView(APIView):
    """Download the filtered transactions (selected month by default) as CSV."""

    def get(self, request):
        queryset = Transaction.objects.filter(user=request.user).select_related(
            "category"
        )
        queryset = filter_or_400(
            queryset,
            request.query_params,
            default_to_current_month=True,
        )
        queryset = queryset.order_by("date", "id")

        month_param = request.query_params.get("month")
        if month_param:
            label = parse_month(month_param).strftime("%Y-%m")
        elif request.query_params.get("start") or request.query_params.get("end"):
            label = "custom-range"
        else:
            label = timezone.localdate().strftime("%Y-%m")

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="transactions-{label}.csv"'
        )
        response["Cache-Control"] = "no-store"
        # UTF-8 BOM keeps Excel happy with non-ASCII descriptions.
        response.write("\ufeff")

        writer = csv.writer(response)
        writer.writerow(["Date", "Type", "Category", "Description", "Amount"])
        for transaction in queryset.iterator():
            writer.writerow(
                [
                    transaction.date.isoformat(),
                    transaction.get_type_display(),
                    transaction.category.name,
                    transaction.description,
                    f"{transaction.amount:.2f}",
                ]
            )
        return response


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
class BudgetListCreateView(generics.ListCreateAPIView):
    serializer_class = BudgetSerializer

    def get_queryset(self):
        self.month = month_from_query(self.request)
        return (
            Budget.objects.filter(user=self.request.user, month=self.month)
            .select_related("category")
            .order_by("category__name")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        month = getattr(self, "month", None)
        if month is None:
            try:
                month = parse_month(self.request.query_params.get("month"))
            except InvalidMonth:
                month = timezone.localdate().replace(day=1)
            self.month = month
        context["month"] = month
        context["spent_by_category"] = get_spend_by_category(
            self.request.user, month
        )
        return context

    def create(self, request, *args, **kwargs):
        """Create a budget, or update it when it already exists (upsert)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        category = data["category"]
        month = data["month"]
        amount = data["amount"]

        try:
            budget, created = Budget.objects.get_or_create(
                user=request.user,
                category=category,
                month=month,
                defaults={"amount": amount},
            )
        except IntegrityError as exc:
            raise ValidationError(
                {"detail": "A budget already exists for that category and month."}
            ) from exc

        if not created and budget.amount != amount:
            budget.amount = amount
            budget.save(update_fields=["amount", "updated_at"])

        context = self.get_serializer_context()
        context["month"] = month
        context["spent_by_category"] = get_spend_by_category(request.user, month)
        output = BudgetSerializer(budget, context=context)
        return Response(
            output.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class BudgetDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BudgetSerializer

    def get_queryset(self):
        return Budget.objects.filter(user=self.request.user).select_related(
            "category"
        )


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
class MonthlySummaryView(APIView):
    def get(self, request):
        month = month_from_query(request)
        return Response(get_monthly_summary(request.user, month))


class CategoryTotalsView(APIView):
    def get(self, request):
        month = month_from_query(request)
        return Response(
            {
                "month": month.strftime("%Y-%m"),
                "month_label": month.strftime("%B %Y"),
                "categories": get_category_totals(request.user, month),
            }
        )


class TrendView(APIView):
    def get(self, request):
        month = month_from_query(request)
        try:
            rows = get_trend(
                request.user,
                month,
                request.query_params.get("months", 6),
            )
        except InvalidFilter as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            {
                "end_month": month.strftime("%Y-%m"),
                "months": len(rows),
                "trend": rows,
            }
        )


class DashboardAnalyticsView(APIView):
    def get(self, request):
        month = month_from_query(request)
        return Response(get_dashboard(request.user, month))


class ApiRootView(APIView):
    permission_classes = []

    def get(self, request):
        return Response(
            {
                "name": "LedgerLite API",
                "authenticated": bool(
                    request.user and request.user.is_authenticated
                ),
                "endpoints": {
                    "auth": {
                        "register": "/api/auth/register/",
                        "login": "/api/auth/login/",
                        "logout": "/api/auth/logout/",
                        "me": "/api/auth/me/",
                    },
                    "categories": "/api/categories/",
                    "transactions": "/api/transactions/",
                    "transaction_export": "/api/transactions/export/?month=YYYY-MM",
                    "budgets": "/api/budgets/?month=YYYY-MM",
                    "analytics": {
                        "summary": "/api/analytics/summary/?month=YYYY-MM",
                        "categories": "/api/analytics/categories/?month=YYYY-MM",
                        "trend": "/api/analytics/trend/?month=YYYY-MM&months=6",
                        "dashboard": "/api/analytics/dashboard/?month=YYYY-MM",
                    },
                },
                "filters": {
                    "transactions": "month, start, end, category, type, search",
                },
            }
        )
