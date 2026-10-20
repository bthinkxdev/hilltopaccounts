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
    return (Partition.objects.filter(status=Partition.Status.ACTIVE, villa__is_archived=False, villa__business__is_archived=False, villa__is_sold=False)
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

ROOM_TYPES = [
    {'key': 'studio', 'label': 'Studio', 'tagline': 'Comfortable & Private', 'feature': '1 Piece + Kitchen + Toilet', 'tone': 'green'},
    {'key': '1bhk', 'label': '1 BHK', 'tagline': 'Spacious & Cozy', 'feature': '1 Bedroom + Hall + Kitchen + Toilet', 'tone': 'blue'},
    {'key': '2bhk', 'label': '2 BHK', 'tagline': 'Perfect for Families', 'feature': '2 Bedrooms + Hall + Kitchen + 2 Toilets', 'tone': 'sand'},
]
_GUESS = [('2bhk', re.compile(r'\b2\s*-?\s*bhk\b|two[\s-]*bed', re.I)), ('1bhk', re.compile(r'\b1\s*-?\s*bhk\b|one[\s-]*bed', re.I)), ('studio', re.compile(r'\bstudio\b', re.I))]

def room_type_of(partition) -> str:
    """The partition's room type; if staff left it unset, guess from the name or description."""
    if partition.room_type:
        return partition.room_type
    text = f'{partition.name} {partition.description}'
    for key, pattern in _GUESS:
        if pattern.search(text):
            return key
    return ''

def _build_items(partitions, *, available_only=False, room_type='', limit=None):
    items, contacts = [], {}
    for p in partitions:
        occupied = p.is_occupied
        if available_only and occupied:
            continue
        if room_type and room_type_of(p) != room_type:
            continue
        if p.villa_id not in contacts:
            contacts[p.villa_id] = contact_for(p)
        items.append({'p': p, 'photos': _photos(p), 'occupied': occupied, 'wa_url': _whatsapp_url(p, contacts[p.villa_id])})
        if limit and len(items) >= limit:
            break
    return items

def listing_home(request):
    partitions = list(_public_partitions().order_by('-created_at'))
    available = [p for p in partitions if not p.is_occupied]
    types = [{**t, 'count': sum(1 for p in available if room_type_of(p) == t['key'])} for t in ROOM_TYPES]
    items = _build_items(partitions, available_only=True, limit=8)
    return render(request, 'listings/home.html', _brand({'items': items, 'types': types, 'available_count': len(available), 'total': len(partitions)}))

def listing_list(request):
    partitions = list(_public_partitions().order_by('-created_at'))
    available_only = request.GET.get('show') != 'all'
    room_type = request.GET.get('type', '')
    if room_type not in {t['key'] for t in ROOM_TYPES}:
        room_type = ''
    items = _build_items(partitions, available_only=available_only, room_type=room_type)
    return render(request, 'listings/list.html', _brand({'items': items, 'available_only': available_only, 'room_type': room_type, 'room_types': ROOM_TYPES, 'total': len(partitions), 'available_count': sum(1 for p in partitions if not p.is_occupied)}))

def listing_detail(request, pk):
    p = get_object_or_404(_public_partitions(), pk=pk)
    contact = contact_for(p)
    photos = _photos(p)
    og_image = request.build_absolute_uri(reverse('listings:photo', args=[photos[0].pk])) if photos else ''
    return render(request, 'listings/detail.html', _brand({'p': p, 'photos': photos, 'occupied': p.is_occupied, 'contact': contact, 'wa_url': _whatsapp_url(p, contact), 'og_image': og_image}))

def listing_photo(request, pk):
    """Public photo endpoint, limited to photos of listed (active, non-archived) partitions or villas."""
    photo = get_object_or_404(Photo.objects.filter(villa__is_archived=False, villa__business__is_archived=False, villa__is_sold=False), pk=pk)
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
