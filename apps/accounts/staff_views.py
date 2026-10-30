from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from apps.shared.forms import ReasonForm
from django.db.models import Sum
from apps.billing.models import Payment
from apps.shared.pagination import paginate_queryset
from apps.shared.periods import period_context
from apps.tenancy.models import DepositCollection
from . import selectors
from .models import Assignment, User
from .services import assign_accountant, assign_business_manager, assign_villa_staff, create_user, disable_user, enable_user, remove_assignment
from .staff_forms import AssignmentForm, StaffCreateForm, StaffPasswordChangeForm, StaffWhatsAppForm

def _owner_only(user):
    if not user.is_owner:
        raise PermissionDenied('Only the Admin can manage staff and assignments.')

@login_required
def staff_list(request):
    _owner_only(request.user)
    qs = User.objects.exclude(username='system')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(username__icontains=q)
    page_obj = paginate_queryset(request, qs.order_by('username'))
    period = period_context(request)
    start, end = period['period_start'], period['period_end']
    ids = [u.pk for u in page_obj]
    # What each person collected for the selected month (rent for that month, advances of tenants who moved in then).
    collected = {r['collected_by']: r['t'] for r in Payment.objects.filter(collected_by__in=ids, is_cancelled=False, invoice__billing_period_start__range=(start, end)).order_by().values('collected_by').annotate(t=Sum('amount'))}
    for r in DepositCollection.objects.filter(collected_by__in=ids, tenant__move_in_date__range=(start, end)).order_by().values('collected_by').annotate(t=Sum('amount')):
        collected[r['collected_by']] = collected.get(r['collected_by'], 0) + r['t']
    for u in page_obj:
        u.role_labels = selectors.role_labels_for(u)
        u.collected_in_period = collected.get(u.pk, 0)
    return render(request, 'accounts/staff_list.html', {**period, 'page_obj': page_obj, 'q': q, 'breadcrumbs': [('Staff', None)]})

@login_required
def staff_create(request):
    _owner_only(request.user)
    from apps.businesses.models import Partner
    from apps.villas.models import Villa
    if request.method == 'POST':
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            user = create_user(
                username=data['username'], email=data['email'],
                first_name=data['first_name'], last_name=data['last_name'],
                whatsapp_number=data['whatsapp_number'], created_by=request.user,
                password=data['password1']
            )
            if data.get('is_partner') and data.get('partner') and data.get('villa'):
                from .services import assign_partner
                assign_partner(user=user, villa=data['villa'], assigned_by=request.user, partner=data['partner'])
                messages.success(request, f'Partner account “{user.username}” created and linked to {data["partner"].name} ({data["villa"].name}).')
            else:
                messages.success(request, f'Staff account “{user.username}” created.')
            return redirect('accounts:staff_detail', pk=user.pk)
    else:
        form = StaffCreateForm()
    
    import json
    from apps.businesses.models import PartnerVillaShare
    villas = list(Villa.objects.filter(is_archived=False, is_sold=False).order_by('name'))
    partners = list(Partner.objects.filter(is_active=True).order_by('name'))
    shares = list(PartnerVillaShare.objects.filter(partner__is_active=True).select_related('partner', 'partner__user'))
    villa_partners_map = {}
    for s in shares:
        villa_partners_map.setdefault(str(s.villa_id), []).append({
            'id': str(s.partner_id),
            'name': s.partner.name,
            'percentage': str(s.percentage),
            'has_account': bool(s.partner.user_id),
            'username': s.partner.user.username if s.partner.user else '',
        })

    return render(request, 'accounts/staff_create.html', {
        'form': form,
        'title': 'Add Staff / Partner',
        'submit_label': 'Create Account',
        'cancel_url': reverse('accounts:staff_list'),
        'partners': partners,
        'villas': villas,
        'villa_partners_json': json.dumps(villa_partners_map),
    })

@login_required
def staff_whatsapp_edit(request, pk):
    _owner_only(request.user)
    staff = get_object_or_404(User.objects.exclude(username='system'), pk=pk)
    form = StaffWhatsAppForm(request.POST or None, initial={'whatsapp_number': staff.whatsapp_number})
    if request.method == 'POST' and form.is_valid():
        old = staff.whatsapp_number
        staff.whatsapp_number = form.cleaned_data['whatsapp_number']
        staff._skip_audit_signal = True
        staff.save(update_fields=['whatsapp_number'])
        from apps.audit import services as audit_services
        from apps.audit.models import Action
        audit_services.log(user=request.user, action=Action.USER_UPDATED, obj=staff, old_value={'whatsapp_number': old}, new_value={'whatsapp_number': staff.whatsapp_number})
        messages.success(request, 'WhatsApp number updated.')
        return redirect('accounts:staff_detail', pk=staff.pk)
    return render(request, 'components/form_page.html', {'form': form, 'title': f'WhatsApp number — {staff.username}', 'submit_label': 'Save', 'cancel_url': reverse('accounts:staff_detail', args=[staff.pk])})

@login_required
def staff_password_change(request, pk):
    _owner_only(request.user)
    staff = get_object_or_404(User.objects.exclude(username='system'), pk=pk)
    if request.method == 'POST':
        form = StaffPasswordChangeForm(staff, request.POST)
        if form.is_valid():
            form.save()
            from apps.audit import services as audit_services
            from apps.audit.models import Action
            audit_services.log(user=request.user, action=Action.PASSWORD_CHANGED, obj=staff)
            messages.success(request, f'Password successfully updated for {staff.username}.')
            return redirect('accounts:staff_detail', pk=staff.pk)
    else:
        form = StaffPasswordChangeForm(staff)
    return render(request, 'components/form_page.html', {
        'form': form,
        'title': f'Change Password — {staff.username}',
        'submit_label': 'Change Password',
        'cancel_url': reverse('accounts:staff_detail', args=[staff.pk]),
        'breadcrumbs': [
            ('Staff', reverse('accounts:staff_list')),
            (staff.username, reverse('accounts:staff_detail', args=[staff.pk])),
            ('Change Password', None)
        ]
    })

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
def staff_activate(request, pk):
    _owner_only(request.user)
    staff = get_object_or_404(User.objects.exclude(username='system'), pk=pk)
    if request.method == 'POST':
        enable_user(user=staff, enabled_by=request.user)
        messages.success(request, f'“{staff.username}” reactivated.')
    return redirect(request.POST.get('next') if request.POST.get('next') == reverse('accounts:staff_list') else reverse('accounts:staff_detail', args=[staff.pk]))

@login_required
def assignment_create(request, staff_pk):
    _owner_only(request.user)
    staff = get_object_or_404(User.objects.exclude(username='system'), pk=staff_pk)
    if request.method == 'POST':
        form = AssignmentForm(request.POST, staff=staff)
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
                msg = exc.messages[0] if hasattr(exc, 'messages') and exc.messages else str(exc)
                form.add_error(None, msg)
            else:
                messages.success(request, f'Assignment added for {staff.username}.')
                return redirect('accounts:staff_detail', pk=staff.pk)
    else:
        form = AssignmentForm(staff=staff, initial={'role': Assignment.Role.VILLA_STAFF})
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Assign — {staff.username}', 'submit_label': 'Add Assignment', 'cancel_url': reverse('accounts:staff_detail', args=[staff.pk]), 'assignment_form': True})

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

@login_required
def assignment_toggle_partitions(request, pk):
    _owner_only(request.user)
    assignment = get_object_or_404(Assignment, pk=pk, role=Assignment.Role.VILLA_STAFF)
    assignment.can_add_partitions = not assignment.can_add_partitions
    assignment.save(update_fields=['can_add_partitions'])
    action = 'enabled' if assignment.can_add_partitions else 'disabled'
    messages.success(request, f'Partition creation {action} for {assignment.user.username} at {assignment.villa.name}.')
    return redirect('accounts:staff_detail', pk=assignment.user_id)
