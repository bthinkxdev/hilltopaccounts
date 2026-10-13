import re
from urllib.parse import quote
from django.conf import settings
from django.db.models import Prefetch
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from apps.accounts.models import Assignment, User
from apps.villas.models import Partition, Photo
from apps.villas.photos import ALLOWED_EXTENSIONS


def _public_partitions():
    """Active partitions of non-archived villas/businesses. Public pages never expose tenant, billing or landlord data."""
    return (Partition.objects.filter(status=Partition.Status.ACTIVE, villa__is_archived=False, villa__business__is_archived=False)
            .select_related('villa', 'villa__business')
            .prefetch_related(Prefetch('photos', queryset=Photo.objects.order_by('created_at', 'id')), Prefetch('villa__photos', queryset=Photo.objects.filter(partition__isnull=True).order_by('created_at', 'id'))))

def _whatsapp_number(phone: str) -> str:
    digits = re.sub(r'\D', '', phone or '')
    if digits.startswith('00'):
        digits = digits[2:]
    elif digits.startswith('0'):
        digits = digits[1:]
        if len(digits) in (8, 9):
            digits = settings.LISTING_COUNTRY_CODE + digits
    elif len(digits) in (8, 9):
        digits = settings.LISTING_COUNTRY_CODE + digits
    return digits

def _whatsapp_url(partition, contact):
    if not contact:
        return None
    rent = f' ({settings.LISTING_CURRENCY} {partition.rent:,.0f}/month)' if partition.rent else ''
    message = f'Hi {settings.LISTING_BRAND}, I am interested in {partition.name} at {partition.villa.name}{rent}. Is it available?'
    return f'https://wa.me/{contact["number"]}?text={quote(message)}'

def _brand(extra=None):
    ctx = {'brand': settings.LISTING_BRAND, 'currency': settings.LISTING_CURRENCY, 'brand_phone': settings.LISTING_FALLBACK_WHATSAPP, 'brand_wa': _whatsapp_number(settings.LISTING_FALLBACK_WHATSAPP), 'instagram': settings.LISTING_INSTAGRAM, 'facebook_name': settings.LISTING_FACEBOOK_NAME, 'facebook_url': settings.LISTING_FACEBOOK_URL}
    ctx.update(extra or {})
    return ctx

def contact_for(partition):
    """The villa's staff first, then the business manager, then an owner: whoever has a phone number."""
    villa = partition.villa
    tiers = [
        User.objects.filter(is_active=True, assignments__role=Assignment.Role.VILLA_STAFF, assignments__villa=villa),
        User.objects.filter(is_active=True, assignments__role=Assignment.Role.BUSINESS_MANAGER, assignments__business=villa.business),
        User.objects.filter(is_active=True, is_owner=True),
    ]
    for users in tiers:
        for user in users.exclude(whatsapp_number='').order_by('id'):
            number = _whatsapp_number(user.whatsapp_number)
            if number:
                return {'name': user.get_full_name() or user.username, 'number': number}
    number = _whatsapp_number(settings.LISTING_FALLBACK_WHATSAPP)
    return {'name': settings.LISTING_BRAND, 'number': number} if number else None

def _photos(partition):
    return list(partition.photos.all()) or list(partition.villa.photos.all())

def listing_list(request):
    partitions = list(_public_partitions().order_by('-created_at'))
    available_only = request.GET.get('show') != 'all'
    items = []
    contacts = {}
    for p in partitions:
        photos = _photos(p)
        occupied = p.is_occupied
        if available_only and occupied:
            continue
        if p.villa_id not in contacts:
            contacts[p.villa_id] = contact_for(p)
        items.append({'p': p, 'photos': photos, 'occupied': occupied, 'wa_url': _whatsapp_url(p, contacts[p.villa_id])})
    available_count = sum(1 for p in partitions if not p.is_occupied)
    return render(request, 'listings/list.html', _brand({'items': items, 'available_only': available_only, 'total': len(partitions), 'available_count': available_count, 'villa_count': len({p.villa_id for p in partitions})}))

def listing_detail(request, pk):
    p = get_object_or_404(_public_partitions(), pk=pk)
    contact = contact_for(p)
    photos = _photos(p)
    og_image = request.build_absolute_uri(reverse('listings:photo', args=[photos[0].pk])) if photos else ''
    return render(request, 'listings/detail.html', _brand({'p': p, 'photos': photos, 'occupied': p.is_occupied, 'contact': contact, 'wa_url': _whatsapp_url(p, contact), 'og_image': og_image}))

def listing_photo(request, pk):
    """Public photo endpoint, limited to photos of listed (active, non-archived) partitions or villas."""
    photo = get_object_or_404(Photo.objects.filter(villa__is_archived=False, villa__business__is_archived=False), pk=pk)
    if photo.partition_id and photo.partition.status != Partition.Status.ACTIVE:
        raise Http404
    extension = photo.image.name.rsplit('.', 1)[-1].lower()
    try:
        handle = photo.image.open('rb')
    except FileNotFoundError as exc:
        raise Http404('Photo file is missing.') from exc
    response = FileResponse(handle, content_type=ALLOWED_EXTENSIONS.get(extension, 'application/octet-stream'))
    response['Cache-Control'] = 'public, max-age=3600'
    return response
