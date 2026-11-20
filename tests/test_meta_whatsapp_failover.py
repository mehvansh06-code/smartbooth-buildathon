"""
tests/test_meta_whatsapp_failover.py
Automated Verification Suite for Multi-API WhatsApp Cascade Failover & Circuit Breaker.
"""

import os
import unittest
import tempfile
import time
from unittest.mock import patch, MagicMock
import json

import meta_whatsapp
from meta_whatsapp import WhatsAppProvider, WhatsAppProviderPool


class FakeCredit:
    """Mock credit object mimicking electionapp.whatsapp_quota.Reservation."""
    def __init__(self, allowance=1):
        self.granted = allowance
        self.remaining = allowance
        self.spent = 0
        self.refunded = 0

    def spend(self):
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        self.spent += 1
        return True

    def refund_one(self):
        if self.spent > 0:
            self.spent -= 1
            self.remaining += 1
            self.refunded += 1


class TestMetaWhatsAppMultiApiFailover(unittest.TestCase):

    def setUp(self):
        # Reset media cache and pool between tests
        import os
        os.environ["PRIMARY_WA_GATEWAY"] = "auto"
        meta_whatsapp._MEDIA_CACHE.clear()
        self.pool = WhatsAppProviderPool()
        # Create a fresh isolated pool with 2 test providers
        self.p1 = WhatsAppProvider(
            provider_id="primary_test",
            phone_number_id="1111111111",
            access_token="TOKEN_P1",
            template_name="info_update_suchna_1",
            priority=1
        )
        self.p2 = WhatsAppProvider(
            provider_id="backup_test",
            phone_number_id="2222222222",
            access_token="TOKEN_P2",
            template_name="info_update_suchna_1",
            priority=2
        )
        self.pool.providers = [self.p1, self.p2]
        meta_whatsapp.provider_pool = self.pool

        # Create dummy slip image
        self.tmp_img = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        self.tmp_img.write(b'FAKE_JPEG_IMAGE_BYTES_FOR_TEST')
        self.tmp_img.close()

    def tearDown(self):
        if os.path.exists(self.tmp_img.name):
            try:
                os.remove(self.tmp_img.name)
            except Exception:
                pass

    def test_provider_circuit_breaker_logic(self):
        """Tests that a failed provider enters cooldown and is bypassed."""
        self.assertTrue(self.p1.is_available())
        self.p1.record_failure("Rate limit reached 429", cooldown_seconds=600)
        self.assertFalse(self.p1.is_available())
        self.assertEqual(self.p1.failure_count, 1)
        self.assertGreater(self.p1.cooldown_until, time.time())

        # Success clears cooldown
        self.p1.record_success()
        self.assertTrue(self.p1.is_available())
        self.assertEqual(self.p1.failure_count, 0)
        self.assertEqual(self.p1.sent_today, 1)

    def test_media_cache_provider_isolation(self):
        """Tests that media IDs are isolated per provider so accounts never cross-contaminate."""
        with patch.object(meta_whatsapp._session, 'post') as mock_post:
            resp_p1 = MagicMock(status_code=200)
            resp_p1.json.return_value = {'id': 'media_p1_12345'}
            mock_post.return_value = resp_p1

            m1 = meta_whatsapp.upload_media(self.tmp_img.name, provider=self.p1)
            self.assertEqual(m1, 'media_p1_12345')

            # Now upload for P2: must perform a new upload for P2
            resp_p2 = MagicMock(status_code=200)
            resp_p2.json.return_value = {'id': 'media_p2_67890'}
            mock_post.return_value = resp_p2

            m2 = meta_whatsapp.upload_media(self.tmp_img.name, provider=self.p2)
            self.assertEqual(m2, 'media_p2_67890')
            self.assertNotEqual(m1, m2)

    def test_automatic_cascade_failover_on_rate_limit(self):
        """
        When Primary API hits Meta rate limit (HTTP 429 / 131048),
        it must seamlessly fail over to Backup API and return success.
        """
        credit = FakeCredit(1)

        # Mock media uploads
        with patch('meta_whatsapp.upload_media', return_value='media_ok'):
            with patch.object(meta_whatsapp._session, 'post') as mock_post:
                # 1st call (Primary): Fails with Meta 429 Rate Limit
                fail_resp = MagicMock(status_code=429)
                fail_resp.json.return_value = {
                    'error': {'code': 131048, 'message': 'Spam rate limit hit'}
                }

                # 2nd call (Backup): Succeeds with 200 OK
                success_resp = MagicMock(status_code=200)
                success_resp.json.return_value = {
                    'messages': [{'id': 'wamid.BACKUP_SUCCESS_001'}]
                }

                mock_post.side_effect = [fail_resp, success_resp]

                ok, res, err = meta_whatsapp.send_voter_slip_card(
                    phone='9414127202',
                    voter_name='राकेश जी',
                    part_no='144',
                    sl_no='82',
                    booth_name='रा.उ.मा.वि.',
                    image_path=self.tmp_img.name,
                    credit=credit
                )

                self.assertTrue(ok)
                self.assertIn('BACKUP_SUCCESS_001', res['message_id'])
                self.assertIn('backup_test', res['provider'])
                self.assertIsNone(err)

                # Primary must now be tripped into cooldown
                self.assertFalse(self.p1.is_available())
                # Backup succeeded
                self.assertEqual(self.p2.sent_today, 1)
                # Exactly 1 credit consumed, not double charged
                self.assertEqual(credit.spent, 1)

    def test_circuit_breaker_instant_bypass(self):
        """
        Once Primary is tripped into cooldown, subsequent sends must
        INSTANTLY bypass Primary (0ms delay) and send straight to Backup.
        """
        # Trip Primary into cooldown
        self.p1.record_failure("Tripped from previous error", cooldown_seconds=900)
        self.assertFalse(self.p1.is_available())

        credit = FakeCredit(1)
        with patch('meta_whatsapp.upload_media', return_value='media_ok'):
            with patch.object(meta_whatsapp._session, 'post') as mock_post:
                success_resp = MagicMock(status_code=200)
                success_resp.json.return_value = {
                    'messages': [{'id': 'wamid.INSTANT_BYPASS_002'}]
                }
                mock_post.return_value = success_resp

                ok, res, err = meta_whatsapp.send_voter_slip_card(
                    phone='9414127202',
                    voter_name='रमेश जी',
                    part_no='144',
                    sl_no='83',
                    booth_name='रा.उ.मा.वि.',
                    image_path=self.tmp_img.name,
                    credit=credit
                )

                self.assertTrue(ok)
                # Primary was completely bypassed; only backup provider was called for message send
                called_urls = [call[0][0] for call in mock_post.call_args_list]
                self.assertTrue(any('2222222222/messages' in u for u in called_urls))
                self.assertFalse(any('1111111111' in u for u in called_urls))
                self.assertIn('backup_test', res['provider'])

    def test_no_failover_on_recipient_not_on_whatsapp(self):
        """
        If recipient does not use WhatsApp (Error 131026), failover must NOT trigger
        because it is a voter-level error, not a provider error. Credit must be refunded.
        """
        credit = FakeCredit(1)

        with patch('meta_whatsapp.upload_media', return_value='media_ok'):
            with patch.object(meta_whatsapp._session, 'post') as mock_post:
                not_on_wa_resp = MagicMock(status_code=400)
                not_on_wa_resp.json.return_value = {
                    'error': {'code': 131026, 'message': 'Recipient is not a valid WhatsApp user'}
                }
                mock_post.return_value = not_on_wa_resp

                ok, res, err = meta_whatsapp.send_voter_slip_card(
                    phone='9414127202',
                    voter_name='सुरेश जी',
                    part_no='144',
                    sl_no='84',
                    booth_name='रा.उ.मा.वि.',
                    image_path=self.tmp_img.name,
                    credit=credit
                )

                self.assertFalse(ok)
                self.assertIn('131026', err)
                # Must only attempt Primary once and STOP (call count = 1)
                self.assertEqual(mock_post.call_count, 1)
                # Primary should NOT be marked as failed/cooldown
                self.assertTrue(self.p1.is_available())
                # Credit must be refunded
                self.assertEqual(credit.spent, 0)
                self.assertEqual(credit.refunded, 1)

    def test_total_provider_failure_refunds_credit(self):
        """If all providers in the pool fail, exactly 0 credits are billed."""
        credit = FakeCredit(1)

        with patch('meta_whatsapp.upload_media', return_value='media_ok'):
            with patch.object(meta_whatsapp._session, 'post') as mock_post:
                mock_post.side_effect = Exception("Fatal connection reset")

                ok, res, err = meta_whatsapp.send_voter_slip_card(
                    phone='9414127202',
                    voter_name='दिनेश जी',
                    part_no='144',
                    sl_no='85',
                    booth_name='रा.उ.मा.वि.',
                    image_path=self.tmp_img.name,
                    credit=credit
                )

                self.assertFalse(ok)
                self.assertIn('All WhatsApp providers failed', err)
                self.assertEqual(credit.spent, 0)
                self.assertEqual(credit.refunded, 1)


    def test_banned_provider_is_marked_banned_and_fails_over_to_backup(self):
        """
        When Primary API is banned/restricted by Meta (e.g. Error 131031 or 131057),
        it must be marked as BANNED, put in 24h cooldown, and silently route to Backup.
        """
        credit = FakeCredit(1)

        with patch('meta_whatsapp.upload_media', return_value='media_ok'):
            with patch.object(meta_whatsapp._session, 'post') as mock_post:
                # 1st call: Meta returns 131057 (Account has been restricted)
                ban_resp = MagicMock(status_code=400)
                ban_resp.json.return_value = {
                    'error': {'code': 131057, 'message': 'Account has been restricted from sending messages'}
                }

                # 2nd call: Backup succeeds
                success_resp = MagicMock(status_code=200)
                success_resp.json.return_value = {
                    'messages': [{'id': 'wamid.BACKUP_AFTER_BAN_999'}]
                }

                mock_post.side_effect = [ban_resp, success_resp]

                ok, res, err = meta_whatsapp.send_voter_slip_card(
                    phone='9414127202',
                    voter_name='दिलीप जी',
                    part_no='144',
                    sl_no='86',
                    booth_name='रा.उ.मा.वि.',
                    image_path=self.tmp_img.name,
                    credit=credit
                )

                self.assertTrue(ok)
                self.assertIn('BACKUP_AFTER_BAN_999', res['message_id'])
                self.assertIn('backup_test', res['provider'])

                # Primary must be marked as BANNED
                self.assertTrue(self.p1.is_banned)
                self.assertFalse(self.p1.is_available())
                self.assertEqual(self.p1.to_dict()['status'], 'BANNED')

    def test_slogan_and_message_restructure(self):
        """Verifies that slogan is formatted into Slot 3 and candidate signature in Slot 5, and duplicate notice is stripped."""
        credit = FakeCredit(1)
        with patch('meta_whatsapp.upload_media', return_value='media_ok'):
            with patch.object(meta_whatsapp._session, 'post') as mock_post:
                resp = MagicMock(status_code=200)
                resp.json.return_value = {'messages': [{'id': 'wamid.SLOGAN_TEST_123'}]}
                mock_post.return_value = resp

                # Case A: With Slogan & redundant duplicate notice in campaign message
                ok, res, err = meta_whatsapp.send_voter_slip_card(
                    phone='9414127202',
                    voter_name='अभिषेक यादव',
                    part_no='58',
                    sl_no='134',
                    ward_no='3',
                    booth_name='58 - रा.उ.मा.वि. नृसिंहपुरा, ',
                    image_path=self.tmp_img.name,
                    candidate_name='अविनाश जी (सरपंच प्रत्याशी)',
                    campaign_message='ग्राम पंचायत नृसिंहपुरा से सरपंच पद हेतु प्रत्याशी अविनाश जी को विजयी बनाएं। आपकी डिजिटल मतदाता पर्ची नीचे संलग्न है।',
                    slogan='विकास, निष्ठा और जनसेवा ही संकल्प',
                    credit=credit
                )

                self.assertTrue(ok)
                # Find the payload sent to Meta Graph API
                sent_payload = None
                for call_item in mock_post.call_args_list:
                    kwargs = call_item[1]
                    if 'data' in kwargs:
                        data_val = kwargs['data']
                        if isinstance(data_val, bytes):
                            data_val = data_val.decode('utf-8')
                        parsed = json.loads(data_val)
                        if 'template' in parsed:
                            sent_payload = parsed
                            break
                self.assertIsNotNone(sent_payload)
                params = sent_payload['template']['components'][1]['parameters']
                # Param 1: Voter info
                self.assertEqual(params[0]['text'], 'अभिषेक यादव (वार्ड 3 · क्रम #134)')
                # Param 2: Cleaned booth without trailing comma
                self.assertEqual(params[1]['text'], 'मतदान केंद्र: 58 - रा.उ.मा.वि. नृसिंहपुरा')
                # Param 3: Slogan hook
                self.assertEqual(params[2]['text'], 'विकास, निष्ठा और जनसेवा ही संकल्प — आपकी मतदाता पर्ची संलग्न है।')
                # Param 4: Campaign message with duplicate notice stripped
                self.assertEqual(params[3]['text'], 'ग्राम पंचायत नृसिंहपुरा से सरपंच पद हेतु प्रत्याशी अविनाश जी को विजयी बनाएं।')
                # Param 5: Candidate signature with em-dash
                self.assertEqual(params[4]['text'], '— अविनाश जी (सरपंच प्रत्याशी)')

    def test_no_slogan_fallback(self):
        """Verifies clean fallback when slogan is empty."""
        credit = FakeCredit(1)
        with patch('meta_whatsapp.upload_media', return_value='media_ok'):
            with patch.object(meta_whatsapp._session, 'post') as mock_post:
                resp = MagicMock(status_code=200)
                resp.json.return_value = {'messages': [{'id': 'wamid.NO_SLOGAN_123'}]}
                mock_post.return_value = resp

                ok, res, err = meta_whatsapp.send_voter_slip_card(
                    phone='9414127202',
                    voter_name='अभिषेक यादव',
                    part_no='58',
                    sl_no='134',
                    booth_name='रा.उ.मा.वि.',
                    image_path=self.tmp_img.name,
                    candidate_name='अविनाश जी',
                    slogan='',
                    credit=credit
                )

                self.assertTrue(ok)
                sent_payload = None
                for call_item in mock_post.call_args_list:
                    kwargs = call_item[1]
                    if 'data' in kwargs:
                        data_val = kwargs['data']
                        if isinstance(data_val, bytes):
                            data_val = data_val.decode('utf-8')
                        parsed = json.loads(data_val)
                        if 'template' in parsed:
                            sent_payload = parsed
                            break
                self.assertIsNotNone(sent_payload)
                params = sent_payload['template']['components'][1]['parameters']
                # Param 3: Fallback notice
                self.assertEqual(params[2]['text'], 'आपकी डिजिटल मतदाता सूचना पर्ची (Voter Slip) संलग्न है।')
                # Param 5: Candidate signature with em-dash
                self.assertEqual(params[4]['text'], '— अविनाश जी')

    def test_meta_failure_falls_back_to_aoc_gateway(self):
        """
        When all Meta Cloud API providers fail (e.g. 135000 Generic user error),
        the system must seamlessly fall back to GreenTick / AOC direct media gateway,
        deliver the card image + caption, and retain the claimed credit.
        """
        credit = FakeCredit(1)

        with patch('meta_whatsapp.upload_media', return_value='media_fail_test'):
            with patch.object(meta_whatsapp._session, 'post') as mock_post:
                # Meta calls fail with 135000 Generic user error
                meta_fail_resp = MagicMock(status_code=400)
                meta_fail_resp.json.return_value = {
                    'error': {'code': 135000, 'message': '(#135000) Generic user error'}
                }
                meta_fail_resp.text = '{"error": {"code": 135000}}'

                # AOC call succeeds
                aoc_success_resp = MagicMock(status_code=200)
                aoc_success_resp.json.return_value = {
                    'id': 'aoc_msg_12345',
                    'message': 'Message Sent Successfully!',
                    'error': None
                }

                def side_effect(url, *args, **kwargs):
                    if 'aoc-portal.com' in url:
                        return aoc_success_resp
                    return meta_fail_resp

                mock_post.side_effect = side_effect

                ok, res, err = meta_whatsapp.send_voter_slip_card(
                    phone='9414127202',
                    voter_name='दिनेश जी',
                    part_no='144',
                    sl_no='85',
                    booth_name='रा.उ.मा.वि.',
                    image_path=self.tmp_img.name,
                    candidate_name='हनुमानराम जी',
                    credit=credit
                )

                self.assertTrue(ok)
                self.assertIsNone(err)
                self.assertEqual(res.get('provider'), 'aoc_greentick')
                self.assertEqual(res.get('message_id'), 'aoc_msg_12345')
                self.assertEqual(credit.spent, 1)
                self.assertEqual(credit.refunded, 0)


    def test_banned_meta_phone_number_is_bypassed_and_routes_to_aoc(self):
        """
        Verifies that numbers placed in BANNED_PHONE_NUMBER_IDS
        are prevented from entering the provider pool.
        """
        banned_id = '9999999999999999'
        with patch.object(meta_whatsapp, 'BANNED_PHONE_NUMBER_IDS', {banned_id}):
            with patch.dict(os.environ, {
                'META_WA_PHONE_NUMBER_ID': banned_id,
                'META_WA_ACCESS_TOKEN': 'TOKEN_BANNED',
                'META_WA_SENDER_DISPLAY': '+91 99999 99999'
            }):
                pool = WhatsAppProviderPool()
                self.assertEqual(len(pool.providers), 0, "Banned provider must not be loaded into pool")

    def test_aoc_primary_dispatch_bypasses_meta_completely(self):
        """
        When PRIMARY_WA_GATEWAY is 'aoc' (or no valid Meta providers exist),
        SmartBooth dispatches directly via GreenTick / AOC API (+91 89051 23636).
        """
        meta_whatsapp.provider_pool.providers.clear()
        credit = FakeCredit(1)

        with patch.object(meta_whatsapp._session, 'post') as mock_post:
            aoc_resp = MagicMock(status_code=200)
            aoc_resp.json.return_value = {
                'id': 'aoc_direct_999',
                'message': 'Message Sent Successfully!',
                'error': None
            }
            mock_post.return_value = aoc_resp

            ok, res, err = meta_whatsapp.send_voter_slip_card(
                phone='9414127202',
                voter_name='सुरेश जी',
                part_no='12',
                sl_no='45',
                booth_name='रा.प्रा.वि.',
                image_path=self.tmp_img.name,
                candidate_name='विकास जी',
                credit=credit
            )

            self.assertTrue(ok)
            self.assertIsNone(err)
            self.assertEqual(res.get('provider'), 'aoc_greentick')
            self.assertEqual(res.get('message_id'), 'aoc_direct_999')

            # Verify POST was sent to AOC URL, not Meta Graph API
            self.assertEqual(mock_post.call_count, 1)
            call_url = mock_post.call_args[0][0]
            self.assertIn('api.aoc-portal.com', call_url)
            self.assertNotIn('graph.facebook.com', call_url)

            # Verify sender is valid AOC sender, not 99982 79249
            payload = json.loads(mock_post.call_args[1]['data'].decode('utf-8'))
            self.assertIn(payload['from'], {'919351147539', '917340693636', '917727983636', '917357598929', '918905123636', '917300179679'})
            self.assertNotEqual(payload['from'], '919998279249')
            self.assertIn(payload['templateName'], {'info_update_short', 'info_update_new_spech', 'info_update_new_01', 'info_update_digital', 'info_update_new_0101', 'info_update_0001'})


if __name__ == '__main__':
    unittest.main()

