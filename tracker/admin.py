from django.contrib import admin
from django.db.models import Count

from .models import Budget, Category, Transaction


class OwnerScopedAdminMixin:
    """Staff users only see and touch their own rows; superusers see everything."""

    owner_field = "user"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(**{self.owner_field: request.user})

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or [])
        if not request.user.is_superuser and self.owner_field not in exclude:
            exclude.append(self.owner_field)
        return exclude

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and not getattr(
            obj, f"{self.owner_field}_id", None
        ):
            setattr(obj, self.owner_field, request.user)
        super().save_model(request, obj, form, change)


@admin.register(Category)
class CategoryAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "type",
        "user",
        "transaction_count",
        "created_at",
    )
    list_filter = ("type", "created_at")
    search_fields = ("name", "user__username")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 30

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(transaction_count=Count("transactions"))
        )

    @admin.display(description="Transactions", ordering="transaction_count")
    def transaction_count(self, obj):
        return obj.transaction_count


@admin.register(Transaction)
class TransactionAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "date",
        "description",
        "category",
        "type",
        "amount",
        "user",
    )
    list_filter = ("type", "category", "date")
    search_fields = ("description", "category__name", "user__username")
    autocomplete_fields = ("user", "category")
    date_hierarchy = "date"
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 30
    list_select_related = ("category", "user")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "category" and not request.user.is_superuser:
            kwargs["queryset"] = Category.objects.filter(user=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("category", "user")


@admin.register(Budget)
class BudgetAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ("category", "month", "amount", "user", "created_at")
    list_filter = ("month", "category")
    search_fields = ("category__name", "user__username")
    autocomplete_fields = ("user", "category")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "month"
    list_select_related = ("category", "user")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "category":
            queryset = Category.objects.filter(type=Category.Type.EXPENSE)
            if not request.user.is_superuser:
                queryset = queryset.filter(user=request.user)
            kwargs["queryset"] = queryset
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
