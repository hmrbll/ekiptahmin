from django.contrib.auth import get_user_model, login as auth_login, logout as auth_logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django_ratelimit.decorators import ratelimit
from sesame.tokens import parse_token

from .emails import send_login_magic_link, send_signup_magic_link
from .forms import EmailLoginForm, SignupForm
from .models import Invite

User = get_user_model()


def _get_user_for_token(user_id):
    """parse_token callback that does NOT filter inactive users.

    The default sesame backend filters with `is_active=True`, but for sign-up
    confirmation we need to allow inactive users (they become active here).
    """
    from sesame import settings as sesame_settings
    try:
        return User._default_manager.get(**{sesame_settings.PRIMARY_KEY_FIELD: user_id})
    except User.DoesNotExist:
        return None


def invite_signup(request: HttpRequest, code: str) -> HttpResponse:
    """GET: render sign-up form. POST: create inactive user, send magic link.

    Invite is consumed only when the magic link is confirmed (NOT on form submit),
    so a typo in the email doesn't burn the invite.
    """
    invite = get_object_or_404(Invite, code=code)
    if not invite.is_valid:
        return render(request, "accounts/invite_invalid.html", {"invite": invite}, status=410)

    if request.method == "POST":
        form = SignupForm(request.POST, invite=invite)
        if form.is_valid():
            user = form.save()
            send_signup_magic_link(user, invite=invite)
            return render(request, "accounts/check_email.html", {"email": user.email})
    else:
        form = SignupForm(invite=invite)

    return render(request, "accounts/signup.html", {"form": form, "invite": invite})


@ratelimit(key="post:email", rate="3/h", method="POST", block=False)
@ratelimit(key="ip", rate="10/h", method="POST", block=False)
def login_request(request: HttpRequest) -> HttpResponse:
    """GET: render email form. POST: send magic link.

    Email-enumeration protection: the same "check your email" page is rendered
    whether the address is registered or not — no information leak.
    """
    if getattr(request, "limited", False):
        return render(request, "accounts/rate_limited.html", status=429)

    if request.method == "POST":
        form = EmailLoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            user = User.objects.filter(email=email, is_active=True).first()
            if user:
                send_login_magic_link(user)
            return render(request, "accounts/check_email.html", {"email": email})
    else:
        form = EmailLoginForm()
    return render(request, "accounts/login.html", {"form": form})


def confirm_token(request: HttpRequest) -> HttpResponse:
    """Magic link callback — validate the token and log the user in.

    Sign-up confirmation: activates the user + marks the matching invite as used.
    Login confirmation: simply logs in.

    Accepts inactive users too (required for the sign-up activation step).
    """
    token = request.GET.get("t", "")
    user = parse_token(token, _get_user_for_token)

    if user is None:
        return render(request, "accounts/token_invalid.html", status=410)

    is_signup = not user.is_active
    if is_signup:
        user.is_active = True
        user.save(update_fields=["is_active"])
        # Mark the most recent active invite addressed to this user's email as used.
        invite = (
            Invite.objects.filter(email__iexact=user.email, used_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if invite:
            invite.mark_used(user)

    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    # Tag the redirect with a one-shot event marker so home.html can fire
    # the matching dataLayer push (sign_up vs. login) and then strip the param.
    event = "sign_up" if is_signup else "login"
    return redirect(f"{reverse('home')}?event={event}")


def logout_view(request: HttpRequest) -> HttpResponse:
    auth_logout(request)
    return redirect("home")
