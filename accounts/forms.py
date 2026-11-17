from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User


class StyledFormMixin:
    """Apply Bootstrap-friendly attributes without a third-party form package."""

    def _style_fields(self):
        for name, field in self.fields.items():
            widget = field.widget
            css = "form-control"
            if isinstance(widget, forms.Select):
                css = "form-select"
            elif isinstance(widget, forms.CheckboxInput):
                css = "form-check-input"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css}".strip()
            widget.attrs.setdefault("placeholder", field.label)
            if name == "username":
                widget.attrs.setdefault("autocomplete", "username")
                widget.attrs.setdefault("autofocus", True)
            elif name in {"password", "password1"}:
                widget.attrs.setdefault("autocomplete", "new-password")
            elif name == "password2":
                widget.attrs.setdefault("autocomplete", "new-password")
            elif name == "email":
                widget.attrs.setdefault("autocomplete", "email")


class LoginForm(StyledFormMixin, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields["username"].widget.attrs["placeholder"] = "Your username"
        self.fields["password"].widget.attrs["placeholder"] = "Your password"
        self.fields["password"].widget.attrs["autocomplete"] = "current-password"


class RegisterForm(StyledFormMixin, UserCreationForm):
    email = forms.EmailField(
        required=True,
        label="Email address",
        help_text="Used only for your account; never shared.",
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields["username"].widget.attrs["placeholder"] = "Choose a username"
        self.fields["password1"].widget.attrs["placeholder"] = "At least 8 characters"
        self.fields["password2"].widget.attrs["placeholder"] = "Repeat your password"
        self.fields["password1"].help_text = (
            "At least 8 characters. Avoid common or entirely numeric passwords."
        )

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with that email already exists.")
        return email
