from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


class SignupForm(forms.Form):
    """Invite-only sign-up. Email is fixed by the invite (admin-set), so the
    user only picks a nickname. The email field is rendered read-only in the
    template and ignored on POST."""

    nickname = forms.CharField(
        max_length=40,
        widget=forms.TextInput(attrs={
            "autocomplete": "nickname",
            "autofocus": True,
            "placeholder": "Ekipte nasıl görünmek istersin?",
        }),
    )

    def __init__(self, *args, invite=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invite = invite

    def clean(self):
        cleaned = super().clean()
        if not self.invite or not (self.invite.email or "").strip():
            raise forms.ValidationError(
                "Bu davetiyenin email adresi tanımlı değil. Lütfen yöneticiyle iletişime geç."
            )
        email = self.invite.email.lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Bu email zaten kayıtlı. Giriş yapmak için login sayfasını kullan."
            )
        return cleaned

    def clean_nickname(self):
        nick = self.cleaned_data["nickname"].strip()
        if len(nick) < 2:
            raise forms.ValidationError("Nickname en az 2 karakter olmalı.")
        return nick

    def save(self) -> User:
        email = self.invite.email.lower().strip()
        nickname = self.cleaned_data["nickname"]
        user = User.objects.create(
            email=email,
            username=email,           # mirror email into username (Django uniqueness)
            nickname=nickname,
            is_active=False,          # activated only after magic-link confirmation
        )
        user.set_unusable_password()  # passwordless flow — magic links only
        user.save()
        return user


class EmailLoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            "autocomplete": "email",
            "autofocus": True,
            "placeholder": "ornek@mail.com",
        }),
    )

    def clean_email(self):
        return self.cleaned_data["email"].lower().strip()
