"""The single view: renders the chat page."""

from __future__ import annotations

from django.views.generic import TemplateView


class IndexView(TemplateView):
    template_name = "chat/index.html"
