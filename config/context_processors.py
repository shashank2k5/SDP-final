from django.conf import settings


def site_config(request):
    """Expose presentation settings to every template."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "CURRENCY_CODE": settings.CURRENCY_CODE,
        "CURRENCY_LOCALE": settings.CURRENCY_LOCALE,
    }
