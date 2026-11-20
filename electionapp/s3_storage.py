"""
s3_storage.py
=============
Cloud-native AWS S3 asset storage & CDN delivery engine for SmartBooth.
- Uploads candidate photos and party symbols to S3 bucket.
- Automatically generates high-efficiency WebP thumbnails for blazing fast UI rendering.
- Sets immutable 1-year browser and CDN caching headers.
- Completely decouples binary image delivery from EC2 Gunicorn WSGI workers.
- Graceful offline fallback: if S3 is unreachable or credentials are absent,
  transparently falls back to local /tenant_assets routes.
"""

import os
import io
import mimetypes
import logging
from typing import Optional, Tuple, Dict, Any
from urllib.parse import quote

logger = logging.getLogger("smartbooth.s3")

# S3 Configuration
S3_BUCKET = os.environ.get("S3_ASSETS_BUCKET", "smartbooth-campaign-assets")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
S3_CUSTOM_DOMAIN = os.environ.get("S3_CUSTOM_DOMAIN", "").strip().rstrip("/")
S3_ENABLED = os.environ.get("S3_ASSETS_ENABLED", "1").lower() in ("1", "true", "yes")

_S3_CLIENT = None
_S3_INITIALIZED = False


def get_s3_client():
    """Lazily initializes and caches boto3 S3 client with standard AWS credential resolution."""
    global _S3_CLIENT, _S3_INITIALIZED
    if _S3_INITIALIZED:
        return _S3_CLIENT

    if not S3_ENABLED or not S3_BUCKET:
        _S3_INITIALIZED = True
        return None

    try:
        import boto3
        from botocore.config import Config
        cfg = Config(
            region_name=AWS_REGION,
            retries={'max_attempts': 3, 'mode': 'standard'},
            connect_timeout=3,
            read_timeout=5
        )
        _S3_CLIENT = boto3.client('s3', config=cfg)
        _S3_INITIALIZED = True
        logger.info("Initialized AWS S3 asset client for bucket '%s' in region '%s'", S3_BUCKET, AWS_REGION)
    except Exception as e:
        logger.warning("AWS S3 client initialization skipped or failed: %s", e)
        _S3_CLIENT = None
        _S3_INITIALIZED = True

    return _S3_CLIENT


def build_s3_url(key: str) -> str:
    """Builds the public CDN or S3 URL for a given S3 key."""
    clean_key = key.lstrip("/")
    if S3_CUSTOM_DOMAIN:
        return f"{S3_CUSTOM_DOMAIN}/{quote(clean_key, safe='/')}"
    return f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{quote(clean_key, safe='/')}"


def generate_thumbnail(image_bytes: bytes, max_size: int = 140, quality: int = 85) -> Optional[bytes]:
    """Generates an optimized, square-cropped WebP avatar thumbnail from raw image bytes."""
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Respect EXIF orientation
            img = ImageOps.exif_transpose(img)
            # Convert to RGB (supports PNG RGBA or palette)
            if img.mode in ('RGBA', 'LA'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1])
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            # Center square crop
            w, h = img.size
            min_side = min(w, h)
            left = (w - min_side) // 2
            top = (h - min_side) // 2
            img_cropped = img.crop((left, top, left + min_side, top + min_side))
            img_cropped = img_cropped.resize((max_size, max_size), Image.Resampling.LANCZOS)

            out_buf = io.BytesIO()
            img_cropped.save(out_buf, format="WEBP", quality=quality, method=6)
            return out_buf.getvalue()
    except Exception as e:
        logger.warning("Failed to generate WebP thumbnail: %s", e)
        return None


def upload_campaign_asset(
    local_path: str,
    tenant_slug: str,
    target_filename: str,
    generate_thumb: bool = True
) -> Dict[str, Optional[str]]:
    """
    Uploads a local campaign asset file (candidate photo or symbol) to S3.
    Optionally generates and uploads a WebP thumbnail.
    
    Returns a dict:
        {
            'url': 'https://...',        # Full image URL
            'thumb_url': 'https://...',  # Avatar thumbnail URL (if generated)
            's3_key': 'tenants/...',
            's3_thumb_key': 'tenants/...'
        }
    """
    client = get_s3_client()
    if not client or not os.path.exists(local_path):
        return {'url': None, 'thumb_url': None, 's3_key': None, 's3_thumb_key': None}

    clean_slug = str(tenant_slug).strip().lower()
    base_key = f"tenants/{clean_slug}/{target_filename}"

    mime_type, _ = mimetypes.guess_type(target_filename)
    if not mime_type:
        mime_type = "application/octet-stream"

    result = {
        'url': None,
        'thumb_url': None,
        's3_key': base_key,
        's3_thumb_key': None
    }

    try:
        with open(local_path, "rb") as f:
            file_bytes = f.read()

        # 1. Upload original/normalized asset
        client.put_object(
            Bucket=S3_BUCKET,
            Key=base_key,
            Body=file_bytes,
            ContentType=mime_type,
            CacheControl="public, max-age=31536000, immutable"
        )
        result['url'] = build_s3_url(base_key)
        logger.info("Uploaded S3 asset: %s", result['url'])

        # 2. Upload WebP thumbnail for candidate photos
        if generate_thumb and mime_type.startswith("image/"):
            thumb_bytes = generate_thumbnail(file_bytes, max_size=140, quality=85)
            if thumb_bytes:
                root, _ = os.path.splitext(target_filename)
                thumb_filename = f"thumb_{root}.webp"
                thumb_key = f"tenants/{clean_slug}/{thumb_filename}"
                client.put_object(
                    Bucket=S3_BUCKET,
                    Key=thumb_key,
                    Body=thumb_bytes,
                    ContentType="image/webp",
                    CacheControl="public, max-age=31536000, immutable"
                )
                result['thumb_url'] = build_s3_url(thumb_key)
                result['s3_thumb_key'] = thumb_key
                logger.info("Uploaded S3 thumbnail: %s", result['thumb_url'])
    except Exception as e:
        logger.warning("Failed to upload asset to S3 (%s): %s", base_key, e)

    return result


def resolve_photo_url(
    tenant_slug: str,
    photo_filename: Optional[str],
    stored_url: Optional[str] = None,
    prefer_thumb: bool = False
) -> Optional[str]:
    """
    Authoritative photo URL resolver for Admin Dashboard and PWA.
    1. Returns stored S3 or relative URL if present.
    2. Builds S3 CDN URL if S3 is active.
    3. Falls back to local asset path or /tenant_assets route.
    """
    clean_slug = str(tenant_slug).strip().lower()
    if stored_url and (stored_url.startswith("http://") or stored_url.startswith("https://") or stored_url.startswith("/")):
        return stored_url

    if not photo_filename:
        return None

    if S3_ENABLED and S3_BUCKET:
        if prefer_thumb:
            root, _ = os.path.splitext(photo_filename)
            thumb_key = f"tenants/{clean_slug}/thumb_{root}.webp"
            return build_s3_url(thumb_key)
        s3_key = f"tenants/{clean_slug}/{photo_filename}"
        return build_s3_url(s3_key)

    # If S3 is disabled or unavailable, fall back to local disk /tenant_assets route
    try:
        from electionapp.config import TENANTS_DIR
        local_dir = os.path.join(TENANTS_DIR, clean_slug, "assets")
        if os.path.exists(os.path.join(local_dir, photo_filename)):
            return f"/tenant_assets/{photo_filename}?tenant={clean_slug}"
    except Exception:
        pass

    return f"/tenant_assets/{photo_filename}?tenant={clean_slug}"


def resolve_symbol_url(
    tenant_slug: str,
    symbol_filename: Optional[str],
    stored_url: Optional[str] = None
) -> Optional[str]:
    """
    Authoritative symbol URL resolver for Admin Dashboard and PWA.
    1. Returns stored S3 or relative URL if present.
    2. Builds S3 CDN URL if S3 is active.
    3. Falls back to local asset path or /tenant_assets route.
    """
    clean_slug = str(tenant_slug).strip().lower()
    if stored_url and (stored_url.startswith("http://") or stored_url.startswith("https://") or stored_url.startswith("/")):
        return stored_url

    if not symbol_filename:
        return None

    if S3_ENABLED and S3_BUCKET:
        s3_key = f"tenants/{clean_slug}/{symbol_filename}"
        return build_s3_url(s3_key)

    try:
        from electionapp.config import TENANTS_DIR
        local_dir = os.path.join(TENANTS_DIR, clean_slug, "assets")
        if os.path.exists(os.path.join(local_dir, symbol_filename)):
            return f"/tenant_assets/{symbol_filename}?tenant={clean_slug}"
    except Exception:
        pass

    return f"/tenant_assets/{symbol_filename}?tenant={clean_slug}"
