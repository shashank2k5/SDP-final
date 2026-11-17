from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from .models import Budget, Category, Transaction
from .services import build_budget_status, month_bounds

ZERO = Decimal("0.00")


def _request_user(serializer):
    request = serializer.context.get("request")
    if request is not None and request.user.is_authenticated:
        return request.user
    return None


class CategorySerializer(serializers.ModelSerializer):
    transaction_count = serializers.SerializerMethodField()
    type_display = serializers.CharField(source="get_type_display", read_only=True)

    class Meta:
        model = Category
        fields = (
            "id",
            "name",
            "type",
            "type_display",
            "transaction_count",
            "created_at",
        )
        read_only_fields = ("id", "created_at")

    def get_transaction_count(self, obj):
        annotated = getattr(obj, "transaction_count", None)
        if annotated is None:
            return obj.transactions.count()
        return annotated

    def validate_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("Category name cannot be blank.")
        user = _request_user(self)
        if user is not None:
            duplicates = Category.objects.filter(user=user, name__iexact=name)
            if self.instance is not None:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError(
                    "You already have a category with this name."
                )
        return name

    def validate(self, attrs):
        if self.instance is not None:
            new_type = attrs.get("type", self.instance.type)
            if (
                new_type != self.instance.type
                and self.instance.transactions.exists()
            ):
                raise serializers.ValidationError(
                    {
                        "type": (
                            "The type of a category with existing transactions "
                            "cannot be changed."
                        )
                    }
                )
        return attrs


class TransactionSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        error_messages={
            "min_value": "Amount must be greater than zero.",
            "max_digits": "Amount is too large.",
        },
    )
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_type = serializers.CharField(source="category.type", read_only=True)
    type_display = serializers.CharField(source="get_type_display", read_only=True)

    class Meta:
        model = Transaction
        fields = (
            "id",
            "amount",
            "category",
            "category_name",
            "category_type",
            "date",
            "description",
            "type",
            "type_display",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = _request_user(self)
        field = self.fields["category"]
        field.queryset = (
            Category.objects.filter(user=user) if user else Category.objects.none()
        )

    def validate_amount(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate_date(self, value):
        if value and value > timezone.localdate():
            raise serializers.ValidationError(
                "A transaction date cannot be in the future."
            )
        return value

    def validate_description(self, value):
        return (value or "").strip()

    def validate(self, attrs):
        instance = self.instance
        category = attrs.get("category", instance.category if instance else None)
        txn_type = attrs.get("type", instance.type if instance else None)

        if category is not None and txn_type and category.type != txn_type:
            raise serializers.ValidationError(
                {
                    "type": (
                        f"'{category.name}' is an {category.get_type_display().lower()} "
                        f"category, so the transaction type must be "
                        f"'{category.type}'."
                    )
                }
            )
        return attrs


class BudgetSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        error_messages={
            "min_value": "Budget amount must be greater than zero.",
            "max_digits": "Amount is too large.",
        },
    )
    month = serializers.DateField(
        input_formats=["%Y-%m-%d", "%Y-%m"],
        format="%Y-%m",
        error_messages={
            "invalid": "Use YYYY-MM format, for example 2026-11.",
        },
    )
    category_name = serializers.CharField(source="category.name", read_only=True)
    spent = serializers.SerializerMethodField()
    remaining = serializers.SerializerMethodField()
    over_by = serializers.SerializerMethodField()
    percent_used = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = Budget
        fields = (
            "id",
            "category",
            "category_name",
            "month",
            "amount",
            "spent",
            "remaining",
            "over_by",
            "percent_used",
            "status",
            "status_label",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "spent",
            "remaining",
            "over_by",
            "percent_used",
            "status",
            "status_label",
            "created_at",
            "updated_at",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = _request_user(self)
        field = self.fields["category"]
        field.queryset = (
            Category.objects.filter(user=user, type=Category.Type.EXPENSE)
            if user
            else Category.objects.none()
        )

    def validate_category(self, value):
        if value.type != Category.Type.EXPENSE:
            raise serializers.ValidationError(
                "Budgets can only be set for expense categories."
            )
        return value

    def validate_month(self, value):
        return value.replace(day=1)

    def validate(self, attrs):
        instance = self.instance
        if instance is not None:
            category = attrs.get("category", instance.category)
            month = attrs.get("month", instance.month)
            duplicates = Budget.objects.filter(
                user=instance.user, category=category, month=month
            ).exclude(pk=instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError(
                    {
                        "month": (
                            "A budget already exists for this category and month."
                        )
                    }
                )
        return attrs

    # -- Server-side budget calculations ------------------------------------
    def _computed(self, obj):
        cache = self.context.setdefault("_budget_cache", {})
        if obj.pk not in cache:
            spend_map = self.context.get("spent_by_category")
            context_month = self.context.get("month")
            if spend_map is not None and (
                context_month is None or context_month == obj.month
            ):
                spent = spend_map.get(obj.category_id, ZERO)
            else:
                start, end = month_bounds(obj.month)
                spent = (
                    Transaction.objects.filter(
                        user_id=obj.user_id,
                        category_id=obj.category_id,
                        type=Category.Type.EXPENSE,
                        date__gte=start,
                        date__lt=end,
                    ).aggregate(total=Sum("amount"))["total"]
                    or ZERO
                )
            cache[obj.pk] = build_budget_status(spent, obj.amount)
        return cache[obj.pk]

    def get_spent(self, obj):
        return self._computed(obj)["spent"]

    def get_remaining(self, obj):
        return self._computed(obj)["remaining"]

    def get_over_by(self, obj):
        return self._computed(obj)["over_by"]

    def get_percent_used(self, obj):
        return self._computed(obj)["percent_used"]

    def get_status(self, obj):
        return self._computed(obj)["status"]

    def get_status_label(self, obj):
        return self._computed(obj)["status_label"]
