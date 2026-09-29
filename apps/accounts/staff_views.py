from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from apps.shared.forms import ReasonForm
from apps.shared.pagination import paginate_queryset
from . import selectors
from .models import Assignment, User
from .services import assign_accountant, assign_business_manager, assign_villa_staff, create_user, disable_user, remove_assignment
from .staff_forms import AssignmentForm, StaffCreateForm

def _owner_only(user):
    if not user.is_owner:
        raise PermissionDenied('Only the Owner can manage staff and assignments.')

@login_required
def staff_list(request):
    _owner_only(request.user)
    qs = User.objects.exclude(username='system')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(username__icontains=q)
    page_obj = paginate_queryset(request, qs.order_by('username'))
    for u in page_obj:
        u.role_labels = selectors.role_labels_for(u)
    return render(request, 'accounts/staff_list.html', {'page_obj': page_obj, 'q': q, 'breadcrumbs': [('Staff', None)]})

@login_required
def staff_create(request):
    _owner_only(request.user)
    if request.method == 'POST':
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            user = create_user(username=data['username'], email=data['email'], first_name=data['first_name'], last_name=data['last_name'], created_by=request.user, password=data['password1'])
            messages.success(request, f'Staff account “{user.username}” created.')
            return redirect('accounts:staff_detail', pk=user.pk)
    else:
        form = StaffCreateForm()
    return render(request, 'components/form_page.html', {'form': form, 'title': 'Add Staff', 'submit_label': 'Create Account', 'cancel_url': reverse('accounts:staff_list')})

@login_required
def staff_detail(request, pk):
    _owner_only(request.user)
    staff = get_object_or_404(User.objects.exclude(username='system'), pk=pk)
    assignments = Assignment.objects.filter(user=staff).select_related('business', 'villa')
    return render(request, 'accounts/staff_detail.html', {'staff': staff, 'assignments': assignments, 'role_labels': selectors.role_labels_for(staff), 'breadcrumbs': [('Staff', reverse('accounts:staff_list')), (staff.username, None)]})

@login_required
def staff_deactivate(request, pk):
    _owner_only(request.user)
    staff = get_object_or_404(User.objects.exclude(username='system'), pk=pk)
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            disable_user(user=staff, disabled_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, f'“{staff.username}” deactivated.')
            return redirect('accounts:staff_detail', pk=staff.pk)
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Deactivate {staff.username}?', 'message': 'They will no longer be able to sign in. Their history and assignments are preserved.', 'cancel_url': reverse('accounts:staff_detail', args=[staff.pk])})

@login_required
def assignment_create(request, staff_pk):
    _owner_only(request.user)
    staff = get_object_or_404(User.objects.exclude(username='system'), pk=staff_pk)
    if request.method == 'POST':
        form = AssignmentForm(request.POST)
        if form.is_valid():
            role = form.cleaned_data['role']
            business = form.cleaned_data.get('business')
            villa = form.cleaned_data.get('villa')
            try:
                if role == Assignment.Role.BUSINESS_MANAGER:
                    assign_business_manager(user=staff, business=business, assigned_by=request.user)
                elif role == Assignment.Role.VILLA_STAFF:
                    assign_villa_staff(user=staff, villa=villa, assigned_by=request.user)
                else:
                    assign_accountant(user=staff, business=business, villa=villa, assigned_by=request.user)
            except ValidationError as exc:
                form.add_error(None, 'That assignment already exists.' if 'unique' in str(exc).lower() else str(exc))
            else:
                messages.success(request, f'Assignment added for {staff.username}.')
                return redirect('accounts:staff_detail', pk=staff.pk)
    else:
        form = AssignmentForm()
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Assign — {staff.username}', 'submit_label': 'Add Assignment', 'cancel_url': reverse('accounts:staff_detail', args=[staff.pk])})

@login_required
def assignment_remove(request, pk):
    _owner_only(request.user)
    assignment = get_object_or_404(Assignment, pk=pk)
    staff = assignment.user
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            remove_assignment(assignment=assignment, removed_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, 'Assignment removed.')
            return redirect('accounts:staff_detail', pk=staff.pk)
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': 'Remove this assignment?', 'message': 'Access tied to this assignment is revoked immediately.', 'cancel_url': reverse('accounts:staff_detail', args=[staff.pk])})
