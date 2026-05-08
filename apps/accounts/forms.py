from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


class SignupForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            "autocomplete": "email",
            "autofocus": True,
            "placeholder": "ornek@mail.com",
        }),
    )
    nickname = forms.CharField(
        max_length=40,
        widget=forms.TextInput(attrs={
            "autocomplete": "nickname",
            "placeholder": "Ekipte nasıl görünmek istersin?",
        }),
    )

    def __init__(self, *args, invite=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invite = invite

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Bu email zaten kayıtlı. Giriş yapmak için login sayfasını kullan."
            )
        if self.invite and self.invite.email and self.invite.email.lower() != email:
            raise forms.ValidationError(
                f"Davet '{self.invite.email}' adresine gönderildi. "
                "Aynı email ile devam etmen gerekiyor."
            )
        return email

    def clean_nickname(self):
        nick = self.cleaned_data["nickname"].strip()
        if len(nick) < 2:
            raise forms.ValidationError("Nickname en az 2 karakter olmalı.")
        return nick

    def save(self) -> User:
        email = self.cleaned_data["email"]
        nickname = self.cleaned_data["nickname"]
        user = User.objects.create(
            email=email,
            username=email,           # username'i email'e set et (Django uniqueness için)
            nickname=nickname,
            is_active=False,          # magic link onayına kadar aktif değil
        )
        user.set_unusable_password()  # şifre kullanmıyoruz
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
