"""
test_s3_storage.py
==================
Unit and integration tests for S3 asset storage and CDN URL resolution.
"""

import io
import os
import unittest
from unittest.mock import patch, MagicMock
from PIL import Image

from electionapp import s3_storage


class TestS3Storage(unittest.TestCase):

    def test_generate_thumbnail(self):
        """Verify generate_thumbnail produces a valid WebP image with bounded dimensions."""
        # Create a 300x200 test RGB image
        img = Image.new("RGB", (300, 200), color=(255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        raw_bytes = buf.getvalue()

        thumb_bytes = s3_storage.generate_thumbnail(raw_bytes, max_size=140, quality=85)
        self.assertIsNotNone(thumb_bytes)
        self.assertTrue(len(thumb_bytes) > 0)

        # Inspect resulting thumbnail
        with Image.open(io.BytesIO(thumb_bytes)) as out_img:
            self.assertEqual(out_img.format, "WEBP")
            self.assertEqual(out_img.size, (140, 140))

    def test_build_s3_url(self):
        """Verify S3 URL generation formatting."""
        with patch.object(s3_storage, 'S3_BUCKET', 'test-bucket'), \
             patch.object(s3_storage, 'AWS_REGION', 'ap-south-1'), \
             patch.object(s3_storage, 'S3_CUSTOM_DOMAIN', ''):
            url = s3_storage.build_s3_url("tenants/ward1/photo.jpg")
            self.assertEqual(url, "https://test-bucket.s3.ap-south-1.amazonaws.com/tenants/ward1/photo.jpg")

        # Custom domain / CDN
        with patch.object(s3_storage, 'S3_CUSTOM_DOMAIN', 'https://assets.smartbooth.in'):
            url = s3_storage.build_s3_url("tenants/ward1/photo.jpg")
            self.assertEqual(url, "https://assets.smartbooth.in/tenants/ward1/photo.jpg")

    def test_resolve_photo_url_stored_http(self):
        """Stored HTTP/HTTPS URL takes highest precedence."""
        http_url = "https://cdn.example.com/photos/cand.jpg"
        resolved = s3_storage.resolve_photo_url("ward1", "photo.jpg", stored_url=http_url)
        self.assertEqual(resolved, http_url)

    def test_resolve_photo_url_s3_active(self):
        """When S3 is enabled, resolves directly to S3/CDN URL."""
        with patch.object(s3_storage, 'S3_ENABLED', True), \
             patch.object(s3_storage, 'S3_BUCKET', 'test-bucket'), \
             patch.object(s3_storage, 'AWS_REGION', 'ap-south-1'), \
             patch.object(s3_storage, 'S3_CUSTOM_DOMAIN', ''):
            # Full photo
            url = s3_storage.resolve_photo_url("ward1", "photo.jpg", prefer_thumb=False)
            self.assertEqual(url, "https://test-bucket.s3.ap-south-1.amazonaws.com/tenants/ward1/photo.jpg")

            # Thumbnail photo
            thumb_url = s3_storage.resolve_photo_url("ward1", "photo.jpg", prefer_thumb=True)
            self.assertEqual(thumb_url, "https://test-bucket.s3.ap-south-1.amazonaws.com/tenants/ward1/thumb_photo.webp")

    def test_resolve_photo_url_s3_takes_priority_over_local_disk(self):
        """When S3 is enabled, S3/CDN takes priority even if local disk file exists."""
        with patch.object(s3_storage, 'S3_ENABLED', True), \
             patch.object(s3_storage, 'S3_BUCKET', 'test-bucket'), \
             patch.object(s3_storage, 'AWS_REGION', 'ap-south-1'), \
             patch.object(s3_storage, 'S3_CUSTOM_DOMAIN', ''), \
             patch('os.path.exists', return_value=True):
            thumb_url = s3_storage.resolve_photo_url("barmer_zp23", "candidate_photo.png", prefer_thumb=True)
            self.assertEqual(thumb_url, "https://test-bucket.s3.ap-south-1.amazonaws.com/tenants/barmer_zp23/thumb_candidate_photo.webp")

    def test_resolve_photo_url_fallback(self):
        """When S3 is disabled, falls back to local /tenant_assets route."""
        with patch.object(s3_storage, 'S3_ENABLED', False):
            url = s3_storage.resolve_photo_url("ward1", "candidate_photo.png")
            self.assertEqual(url, "/tenant_assets/candidate_photo.png?tenant=ward1")

    def test_upload_campaign_asset_with_mock_client(self):
        """Verify upload_campaign_asset calls put_object with correct caching headers."""
        mock_client = MagicMock()
        with patch.object(s3_storage, 'get_s3_client', return_value=mock_client), \
             patch.object(s3_storage, 'S3_BUCKET', 'test-bucket'), \
             patch.object(s3_storage, 'AWS_REGION', 'ap-south-1'), \
             patch.object(s3_storage, 'S3_CUSTOM_DOMAIN', ''):

            # Create temporary image file
            tmp_path = "/tmp/test_s3_photo.jpg"
            img = Image.new("RGB", (100, 100), color=(0, 255, 0))
            img.save(tmp_path, format="JPEG")

            try:
                res = s3_storage.upload_campaign_asset(tmp_path, "sanchore", "candidate_photo.jpg", generate_thumb=True)
                self.assertIsNotNone(res['url'])
                self.assertIsNotNone(res['thumb_url'])
                self.assertEqual(res['s3_key'], "tenants/sanchore/candidate_photo.jpg")

                # Verify put_object was called twice (original + thumbnail)
                self.assertEqual(mock_client.put_object.call_count, 2)
                calls = mock_client.put_object.call_args_list

                # First call: original
                self.assertEqual(calls[0][1]['Bucket'], 'test-bucket')
                self.assertEqual(calls[0][1]['Key'], 'tenants/sanchore/candidate_photo.jpg')
                self.assertIn('max-age=31536000', calls[0][1]['CacheControl'])

                # Second call: thumbnail
                self.assertEqual(calls[1][1]['Key'], 'tenants/sanchore/thumb_candidate_photo.webp')
                self.assertEqual(calls[1][1]['ContentType'], 'image/webp')
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
