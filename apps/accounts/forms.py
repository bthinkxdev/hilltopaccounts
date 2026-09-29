from django.contrib.auth.forms import AuthenticationForm

class LoginForm(AuthenticationForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'autofocus': True, 'autocomplete': 'username', 'placeholder': 'Username'})
        self.fields['password'].widget.attrs.update({'autocomplete': 'current-password', 'placeholder': 'Password'})
