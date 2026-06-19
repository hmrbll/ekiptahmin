from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from django.utils.html import format_html

from apps.notifications.emails import send_invite_welcome
from apps.notifications.models import EmailLog

from .models import Invite, User

# Hide the AUTHENTICATION AND AUTHORIZATION → Groups section from the admin
# sidebar. Permission groups aren't used in this project — Hemre is the sole
# admin, everyone else is a standard user. The Group model still exists in the
# DB (Django needs it), only the admin UI entry is removed.
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "nickname", "is_active", "is_staff", "email_undeliverable", "date_joined")
    list_filter = ("is_active", "is_staff", "email_undeliverable")
    search_fields = ("email", "nickname", "username")
    ordering = ("-date_joined",)
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Profile", {"fields": ("email", "nickname", "first_name", "last_name", "timezone")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        # Set by the Resend webhook; uncheck `email_undeliverable` to resume
        # sending digests to this address once the mailbox is fixed.
        ("Email delivery", {"fields": ("email_undeliverable", "email_undeliverable_reason", "email_undeliverable_at")}),
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

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        # Make email required in admin even though the model allows blank — when
        # Hemre creates an invite from admin, the welcome email has nowhere to go
        # without an address.
        if db_field.name == "email":
            kwargs["required"] = True
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        is_new = not change
        if is_new and not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        # Send the welcome email exactly once, on creation. Edits to note /
        # expires_at don't re-trigger.
        if is_new and obj.email:
            log = send_invite_welcome(obj)
            if log.status == EmailLog.FAILED:
                messages.error(request, f"Invite email failed ({obj.email}): {log.error}")
            else:
                messages.success(request, f"Invite email sent: {obj.email}")
