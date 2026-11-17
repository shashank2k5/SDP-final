from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView

from .forms import LoginForm, RegisterForm


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Welcome back"
        context["page_subtitle"] = "Sign in to keep track of every rupee."
        return context


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegisterForm
    success_url = reverse_lazy("dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        auth_login(self.request, user)
        messages.success(
            self.request,
            f"Welcome to LedgerLite, {user.username}! Start by adding a category.",
        )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Create your account"
        context["page_subtitle"] = "Track spending, build budgets, see the whole picture."
        return context


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("login")

    def post(self, request, *args, **kwargs):
        messages.info(request, "You have been signed out.")
        return super().post(request, *args, **kwargs)


@login_required
def account_redirect(request):
    return redirect("dashboard")
