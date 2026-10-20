from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from .forms import LoginForm

class AccountLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

class AccountLogoutView(LogoutView):
    pass

@login_required
def owner_password_change(request):
    if not request.user.is_owner:
        raise PermissionDenied('Staff are not permitted to change passwords. Please contact the Admin.')
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            from apps.audit import services as audit_services
            from apps.audit.models import Action
            audit_services.log(user=request.user, action=Action.PASSWORD_CHANGED, obj=user)
            messages.success(request, 'Your password was changed successfully.')
            return redirect('dashboard:home')
    else:
        form = PasswordChangeForm(request.user)
    return render(request, 'components/form_page.html', {
        'form': form,
        'title': 'Change Admin Password',
        'submit_label': 'Save New Password',
        'cancel_url': reverse('dashboard:home'),
        'breadcrumbs': [('Dashboard', reverse('dashboard:home')), ('Change Password', None)]
    })
