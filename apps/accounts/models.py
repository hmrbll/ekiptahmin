from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user model — keep it minimal for now, expand later."""

    email = models.EmailField(unique=True)
    nickname = models.CharField(max_length=40, blank=True)
    timezone = models.CharField(max_length=64, default="Europe/Istanbul")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self) -> str:
        return self.nickname or self.email
