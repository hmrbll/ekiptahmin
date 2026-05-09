from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.html import format_html

from .models import Invite, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "nickname", "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff")
    search_fields = ("email", "nickname", "username")
    ordering = ("-date_joined",)
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Profile", {"fields": ("email", "nickname", "first_name", "last_name", "timezone")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "email", "nickname", "password1", "password2")}),
    )


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ("__str__", "email", "note", "status_badge", "created_at", "expires_at", "invite_link")
    list_filter = ("created_at",)
    search_fields = ("email", "note", "code")
    readonly_fields = ("code", "created_at", "used_at", "used_by", "invite_link")
    fieldsets = (
        (None, {"fields": ("email", "note")}),
        ("Validity", {"fields": ("expires_at",)}),
        ("Auto-generated", {"fields": ("code", "invite_link", "created_by", "created_at")}),
        ("Usage", {"fields": ("used_at", "used_by")}),
    )

    def status_badge(self, obj):
        colors = {"active": "#10b981", "used": "#6b7280", "expired": "#ef4444"}
        labels = {"active": "Active", "used": "Used", "expired": "Expired"}
        s = obj.status
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:8px;font-size:11px">{}</span>',
            colors[s], labels[s],
        )
    status_badge.short_description = "Status"

    def invite_link(self, obj):
        from django.conf import settings
        if not obj.pk:
            return "—"
        url = f"{settings.SITE_URL}/invite/{obj.code}/"
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)
    invite_link.short_description = "Invite link"

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
