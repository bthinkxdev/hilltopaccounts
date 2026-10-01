from django.core.exceptions import ValidationError

MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}

def _detected_type(header: bytes):
    if header.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return 'image/webp'
    return None

def validate_photo(upload) -> str:
    """Accept only real JPEG/PNG/WebP files up to 5 MB — the content is checked, not just the file name."""
    extension = upload.name.rsplit('.', 1)[-1].lower() if '.' in upload.name else ''
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError('Upload a JPG, PNG or WebP image.')
    if upload.size > MAX_PHOTO_BYTES:
        raise ValidationError('Photos must be 5 MB or smaller.')
    position = upload.tell() if hasattr(upload, 'tell') else 0
    upload.seek(0)
    detected = _detected_type(upload.read(12))
    upload.seek(position)
    if detected is None or detected != ALLOWED_EXTENSIONS[extension]:
        raise ValidationError('This file is not a valid image of the type its name says.')
    return detected
