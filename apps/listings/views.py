import re
from urllib.parse import quote
from django.db.models import Prefetch
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from apps.accounts.models import Assignment, User
from apps.villas.models import Partition, Photo
from apps.villas.photos import ALLOWED_EXTENSIONS

DEFAULT_COUNTRY_CODE = '974'

def _public_partitions():
    """Active partitions of non-archived villas/businesses. Public pages never expose tenant, billing or landlord data."""
    return (Partition.objects.filter(status=Partition.Status.ACTIVE, villa__is_archived=False, villa__business__is_archived=False)
            .select_related('villa', 'villa__business')
            .prefetch_related(Prefetch('photos', queryset=Photo.objects.order_by('created_at', 'id')), Prefetch('villa__photos', queryset=Photo.objects.filter(partition__isnull=True).order_by('created_at', 'id'))))

def _whatsapp_number(phone: str) -> str:
    digits = re.sub(r'\D', '', phone or '')
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) == 8:
        digits = DEFAULT_COUNTRY_CODE + digits
    return digits

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
    return None

def _photos(partition):
    return list(partition.photos.all()) or list(partition.villa.photos.all())

def listing_list(request):
    partitions = list(_public_partitions().order_by('-created_at'))
    available_only = request.GET.get('show') != 'all'
    items = []
    for p in partitions:
        photos = _photos(p)
        occupied = p.is_occupied
        if available_only and occupied:
            continue
        items.append({'p': p, 'photos': photos, 'occupied': occupied})
    return render(request, 'listings/list.html', {'items': items, 'available_only': available_only, 'total': len(partitions)})

def listing_detail(request, pk):
    p = get_object_or_404(_public_partitions(), pk=pk)
    contact = contact_for(p)
    message = f'Hi, I am interested in {p.name} at {p.villa.name}. Is it available?'
    wa_url = f'https://wa.me/{contact["number"]}?text={quote(message)}' if contact else None
    return render(request, 'listings/detail.html', {'p': p, 'photos': _photos(p), 'occupied': p.is_occupied, 'contact': contact, 'wa_url': wa_url})

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
