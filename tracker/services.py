"""Analytics and filtering services.

Every number returned by the analytics API is computed here with Django ORM
aggregation (``Sum`` / ``Count`` + ``GROUP BY``); the frontend never derives
financial totals on its own.
"""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import Budget, Category, Transaction

TWOPLACES = Decimal("0.01")
ZERO = Decimal("0.00")

STATUS_LABELS = {
    "ok": "On track",
    "warning": "Approaching limit",
    "over": "Over budget",
}


class InvalidMonth(ValueError):
    """Raised when a month parameter cannot be parsed."""


class InvalidFilter(ValueError):
    """Raised when a transaction filter parameter is malformed."""


# ---------------------------------------------------------------------------
# Month / date helpers
# ---------------------------------------------------------------------------
def parse_month(value=None, *, fallback=None):
    """Return the first day of a month from ``YYYY-MM`` / ``YYYY-MM-DD``.

    An empty value falls back to ``fallback`` (or the current month).
    """
    if value in (None, ""):
        if fallback is not None:
            return parse_month(fallback)
        return timezone.localdate().replace(day=1)

    if isinstance(value, datetime):
        return value.date().replace(day=1)
    if isinstance(value, date):
        return value.replace(day=1)

    text = str(value).strip()
    for fmt in ("%Y-%m", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().replace(day=1)
        except ValueError:
            continue
    raise InvalidMonth("Month must be provided as YYYY-MM (for example 2026-11).")


def parse_iso_date(value, field_name="date"):
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise InvalidFilter(
            f"'{field_name}' must be a valid date in YYYY-MM-DD format."
        ) from exc


def month_bounds(month):
    """Return ``(first_day, first_day_of_next_month)`` for a month."""
    month = parse_month(month)
    if month.month == 12:
        next_month = date(month.year + 1, 1, 1)
    else:
        next_month = date(month.year, month.month + 1, 1)
    return month, next_month


def shift_month(month, offset):
    month = parse_month(month)
    absolute = month.year * 12 + (month.month - 1) + offset
    year, zero_based_month = divmod(absolute, 12)
    return date(year, zero_based_month + 1, 1)


def money(value):
    """Serialize a Decimal amount as a rounded JSON-friendly float."""
    if value is None:
        return 0.0
    return float(Decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP))


def percent(part, whole):
    if not whole:
        return 0.0
    return float(
        (Decimal(part) / Decimal(whole) * Decimal(100)).quantize(
            TWOPLACES, rounding=ROUND_HALF_UP
        )
    )


# ---------------------------------------------------------------------------
# Transaction filtering (shared by list + CSV export)
# ---------------------------------------------------------------------------
def apply_transaction_filters(queryset, params, *, default_to_current_month=False):
    """Filter a transaction queryset by month, date range, category and type.

    Raises ``InvalidFilter`` with a user-safe message for malformed values.
    """
    params = params or {}
    month_value = params.get("month")
    start_value = params.get("start")
    end_value = params.get("end")
    category_value = params.get("category")
    type_value = params.get("type")
    search_value = (params.get("search") or "").strip()

    if default_to_current_month and not any(
        [month_value, start_value, end_value]
    ):
        month_value = timezone.localdate().strftime("%Y-%m")

    if month_value not in (None, ""):
        try:
            month = parse_month(month_value)
        except InvalidMonth as exc:
            raise InvalidFilter(str(exc)) from exc
        start, end = month_bounds(month)
        queryset = queryset.filter(date__gte=start, date__lt=end)

    if start_value not in (None, ""):
        queryset = queryset.filter(date__gte=parse_iso_date(start_value, "start"))
    if end_value not in (None, ""):
        queryset = queryset.filter(date__lte=parse_iso_date(end_value, "end"))

    if category_value not in (None, ""):
        try:
            category_id = int(category_value)
        except (TypeError, ValueError) as exc:
            raise InvalidFilter("'category' must be a numeric category id.") from exc
        queryset = queryset.filter(category_id=category_id)

    if type_value not in (None, ""):
        if type_value not in Category.Type.values:
            raise InvalidFilter("'type' must be either 'income' or 'expense'.")
        queryset = queryset.filter(type=type_value)

    if search_value:
        queryset = queryset.filter(description__icontains=search_value)

    return queryset


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def get_monthly_summary(user, month=None):
    month = parse_month(month)
    start, end = month_bounds(month)

    totals = Transaction.objects.filter(
        user=user, date__gte=start, date__lt=end
    ).aggregate(
        income=Sum(
            "amount", filter=Q(type=Category.Type.INCOME), default=ZERO
        ),
        expense=Sum(
            "amount", filter=Q(type=Category.Type.EXPENSE), default=ZERO
        ),
        transaction_count=Count("id"),
        income_count=Count("id", filter=Q(type=Category.Type.INCOME)),
        expense_count=Count("id", filter=Q(type=Category.Type.EXPENSE)),
    )

    income = totals["income"] or ZERO
    expense = totals["expense"] or ZERO
    net = income - expense

    return {
        "month": month.strftime("%Y-%m"),
        "month_label": month.strftime("%B %Y"),
        "income": money(income),
        "expense": money(expense),
        "net": money(net),
        "savings_rate": percent(net, income) if income else 0.0,
        "transaction_count": totals["transaction_count"] or 0,
        "income_count": totals["income_count"] or 0,
        "expense_count": totals["expense_count"] or 0,
    }


def get_category_totals(user, month=None):
    """Expense totals grouped by category for one month."""
    month = parse_month(month)
    start, end = month_bounds(month)

    rows = (
        Transaction.objects.filter(
            user=user,
            type=Category.Type.EXPENSE,
            date__gte=start,
            date__lt=end,
        )
        .values("category_id", "category__name")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total", "category__name")
    )

    rows = list(rows)
    overall_total = sum((row["total"] for row in rows), ZERO)

    return [
        {
            "category_id": row["category_id"],
            "name": row["category__name"],
            "total": money(row["total"]),
            "count": row["count"],
            "percentage": percent(row["total"], overall_total),
        }
        for row in rows
    ]


def get_spend_by_category(user, month=None):
    """Map ``category_id -> Decimal`` expense total for a month."""
    month = parse_month(month)
    start, end = month_bounds(month)
    rows = (
        Transaction.objects.filter(
            user=user,
            type=Category.Type.EXPENSE,
            date__gte=start,
            date__lt=end,
        )
        .values("category_id")
        .annotate(total=Sum("amount"))
    )
    return {row["category_id"]: row["total"] for row in rows}


def build_budget_status(spent, amount):
    """Compute budget percentage + status. 80% warns, >100% is over budget."""
    spent = Decimal(spent or 0)
    amount = Decimal(amount or 0)
    raw_percent = (spent / amount * Decimal(100)) if amount else ZERO
    rounded_percent = raw_percent.quantize(TWOPLACES, rounding=ROUND_HALF_UP)

    if raw_percent > Decimal(100):
        status = "over"
    elif raw_percent >= Decimal(80):
        status = "warning"
    else:
        status = "ok"

    return {
        "spent": money(spent),
        "percent_used": float(rounded_percent),
        "remaining": money(amount - spent),
        "over_by": money(spent - amount) if spent > amount else 0.0,
        "status": status,
        "status_label": STATUS_LABELS[status],
    }


def get_budget_statuses(user, month=None):
    """Budget rows with server-calculated spend, percentage and status."""
    month = parse_month(month)
    spend_map = get_spend_by_category(user, month)
    budgets = list(
        Budget.objects.filter(user=user, month=month)
        .select_related("category")
        .order_by("category__name")
    )

    rows = []
    budgeted_category_ids = set()
    total_budget = ZERO
    total_spent = ZERO
    warning_count = 0
    over_count = 0

    for budget in budgets:
        budgeted_category_ids.add(budget.category_id)
        spent = spend_map.get(budget.category_id, ZERO)
        status = build_budget_status(spent, budget.amount)
        total_budget += budget.amount
        total_spent += spent
        if status["status"] == "warning":
            warning_count += 1
        elif status["status"] == "over":
            over_count += 1
        rows.append(
            {
                "id": budget.id,
                "category_id": budget.category_id,
                "category_name": budget.category.name,
                "month": budget.month.strftime("%Y-%m"),
                "amount": money(budget.amount),
                "spent": money(spent),
                **status,
            }
        )

    unbudgeted_spent = sum(
        (
            amount
            for category_id, amount in spend_map.items()
            if category_id not in budgeted_category_ids
        ),
        ZERO,
    )

    summary = {
        "month": month.strftime("%Y-%m"),
        "budget_count": len(rows),
        "total_budget": money(total_budget),
        "total_spent": money(total_spent),
        "total_remaining": money(total_budget - total_spent),
        "warning_count": warning_count,
        "over_count": over_count,
        "healthy_count": len(rows) - warning_count - over_count,
        "unbudgeted_spent": money(unbudgeted_spent),
    }

    return rows, summary


def get_trend(user, end_month=None, months=6):
    """Income vs expense for the ``months`` months ending at ``end_month``."""
    try:
        months = int(months)
    except (TypeError, ValueError) as exc:
        raise InvalidFilter("'months' must be a whole number.") from exc
    if months < 1 or months > 24:
        raise InvalidFilter("'months' must be between 1 and 24.")

    end_month = parse_month(end_month)
    first_month = shift_month(end_month, -(months - 1))
    _, end_exclusive = month_bounds(end_month)

    rows = (
        Transaction.objects.filter(
            user=user,
            date__gte=first_month,
            date__lt=end_exclusive,
        )
        .annotate(bucket=TruncMonth("date"))
        .values("bucket")
        .annotate(
            income=Sum(
                "amount", filter=Q(type=Category.Type.INCOME), default=ZERO
            ),
            expense=Sum(
                "amount", filter=Q(type=Category.Type.EXPENSE), default=ZERO
            ),
            count=Count("id"),
        )
        .order_by("bucket")
    )

    by_month = {}
    for row in rows:
        bucket = row["bucket"]
        if isinstance(bucket, datetime):
            bucket = bucket.date()
        bucket = bucket.replace(day=1)
        by_month[bucket] = row

    trend = []
    for offset in range(months):
        bucket = shift_month(first_month, offset)
        row = by_month.get(bucket)
        income = (row["income"] if row else ZERO) or ZERO
        expense = (row["expense"] if row else ZERO) or ZERO
        trend.append(
            {
                "month": bucket.strftime("%Y-%m"),
                "label": bucket.strftime("%b %y"),
                "label_full": bucket.strftime("%B %Y"),
                "income": money(income),
                "expense": money(expense),
                "net": money(income - expense),
                "count": (row["count"] if row else 0) or 0,
            }
        )
    return trend


def get_dashboard(user, month=None, trend_months=6):
    month = parse_month(month)
    budget_rows, budget_summary = get_budget_statuses(user, month)
    return {
        "month": month.strftime("%Y-%m"),
        "month_label": month.strftime("%B %Y"),
        "summary": get_monthly_summary(user, month),
        "categories": get_category_totals(user, month),
        "budgets": budget_rows,
        "budget_summary": budget_summary,
        "trend": get_trend(user, month, trend_months),
        "generated_at": timezone.now().isoformat(),
    }
