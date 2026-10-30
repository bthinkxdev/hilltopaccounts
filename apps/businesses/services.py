from django.db import transaction
from django.utils import timezone
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.shared.exceptions import DomainError
from .models import Business, PartnerPayout

def void_payout(*, payout: PartnerPayout, voided_by, reason, _action=Action.PARTNER_PAYOUT_VOIDED) -> PartnerPayout:
    """Reverse a recorded partner payout. The row is kept (marked voided, with who/when/why) and drops out of what the
    partner has been paid, so the month shows as unpaid again. It replaces the old hard delete."""
    reason = (reason or '').strip()
    if not reason:
        raise DomainError('A reason is required to void a payout.')
    with transaction.atomic():
        locked = PartnerPayout.all_objects.select_for_update().select_related('partner', 'villa__business').get(pk=payout.pk)
        if locked.is_voided:
            raise DomainError('This payout is already voided.')
        locked.voided_at = timezone.now()
        locked.voided_by = voided_by
        locked.void_reason = reason
        locked.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
        villa = locked.villa
        audit_services.log(user=voided_by, action=_action, obj=locked, business=villa.business if villa else None, villa=villa, old_value={'voided': False, 'partner': locked.partner.name, 'amount': str(locked.amount), 'period': f'{locked.period_year}-{locked.period_month:02d}', 'paid_on': str(locked.paid_on), 'method': locked.payment_method}, new_value={'voided': True}, reason=reason)
    payout.voided_at, payout.voided_by, payout.void_reason = locked.voided_at, voided_by, reason
    return payout

def correct_payout(*, payout: PartnerPayout, voided_by, reason, amount, paid_on=None, payment_method=None, notes=None) -> PartnerPayout:
    """Fix a wrong amount/date/method: void the original and record the replacement for the same partner, villa and
    month in one transaction."""
    if amount is None or amount <= 0:
        raise DomainError('Payout amount must be greater than zero.')
    with transaction.atomic():
        void_payout(payout=payout, voided_by=voided_by, reason=f'corrected: {reason}', _action=Action.PARTNER_PAYOUT_CORRECTED)
        new = PartnerPayout.objects.create(partner_id=payout.partner_id, villa_id=payout.villa_id, period_year=payout.period_year, period_month=payout.period_month, amount=amount, payment_method=payment_method or payout.payment_method, paid_on=paid_on or payout.paid_on, reference=payout.reference, notes=payout.notes if notes is None else notes, created_by=voided_by)
        villa = new.villa
        audit_services.log(user=voided_by, action=Action.PARTNER_PAYOUT_CREATED, obj=new, business=villa.business if villa else None, villa=villa, new_value={'partner': new.partner.name, 'amount': str(new.amount), 'method': new.payment_method, 'paid_on': str(new.paid_on), 'period': f'{new.period_year}-{new.period_month:02d}', 'replaces': payout.pk}, reason=f'Corrected payout: QAR {payout.amount} → QAR {new.amount}. {reason}')
    return new

def create_business(*, name, description='', created_by) -> Business:
    with transaction.atomic():
        business = Business(name=name, description=description, created_by=created_by)
        business.full_clean()
        business.save()
        audit_services.log(user=created_by, action=Action.BUSINESS_CREATED, obj=business, business=business, new_value={'name': business.name, 'description': business.description})
    return business

def update_business(*, business: Business, name=None, description=None, updated_by) -> Business:
    old_value = {'name': business.name, 'description': business.description}
    if name is not None:
        business.name = name
    if description is not None:
        business.description = description
    with transaction.atomic():
        business.full_clean()
        business.save()
        new_value = {'name': business.name, 'description': business.description}
        if new_value != old_value:
            audit_services.log(user=updated_by, action=Action.BUSINESS_UPDATED, obj=business, business=business, old_value=old_value, new_value=new_value)
    return business

def archive_business(*, business: Business, archived_by, reason='') -> Business:
    from apps.tenancy.models import Tenant
    active_tenants = list(Tenant.objects.filter(partition__villa__business=business, status=Tenant.Status.ACTIVE).select_related('partition__villa'))
    if active_tenants:
        if len(active_tenants) == 1:
            t = active_tenants[0]
            raise DomainError(f'{business.name} has an active tenant ({t.name} in {t.partition.name}, {t.partition.villa.name}). Move the tenant out before archiving.')
        raise DomainError(f'{business.name} has {len(active_tenants)} active tenants across its villas. Move all tenants out before archiving.')
    with transaction.atomic():
        business.is_archived = True
        business.save(update_fields=['is_archived'])
        audit_services.log(user=archived_by, action=Action.BUSINESS_ARCHIVED, obj=business, business=business, old_value={'is_archived': False}, new_value={'is_archived': True}, reason=reason)
    return business

def unarchive_business(*, business: Business, unarchived_by) -> Business:
    with transaction.atomic():
        business.is_archived = False
        business.save(update_fields=['is_archived'])
        audit_services.log(user=unarchived_by, action=Action.BUSINESS_UPDATED, obj=business, business=business, old_value={'is_archived': True}, new_value={'is_archived': False}, reason='Unarchived')
    return business
