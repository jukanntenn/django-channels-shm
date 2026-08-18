"""URL routes: the chat page (+ static assets, DEBUG only)."""

from django.conf import settings
from django.urls import path, re_path
from django.views.static import serve

from chat.views import IndexView

urlpatterns = [
    path("", IndexView.as_view(), name="index"),
]

if settings.DEBUG:
    # uvicorn does not run Django's staticfiles machinery (that is a
    # runserver feature), so the demo serves its own assets explicitly.
    urlpatterns += [
        re_path(
            r"^static/(?P<path>.*)$",
            serve,
            {"document_root": settings.STATICFILES_DIRS[0]},
        ),
    ]
