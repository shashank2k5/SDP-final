from django.urls import path

from . import api

urlpatterns = [
    path("register/", api.RegisterAPIView.as_view(), name="api_register"),
    path("login/", api.LoginAPIView.as_view(), name="api_login"),
    path("logout/", api.LogoutAPIView.as_view(), name="api_logout"),
    path("me/", api.MeAPIView.as_view(), name="api_me"),
]
