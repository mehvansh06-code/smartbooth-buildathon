import time
import meta_whatsapp
from electionapp.card_generator import generate_bjp_voter_card

voters = [{
    'id': 101,
    'voter_id': 101,
    'sl_no': 15,
    'part_no': 82,
    'name_en': 'RAMESH CHOUDHARY',
    'name_hi': 'रमेश चौधरी',
    'rel_name_en': 'SURESH CHOUDHARY',
    'rel_name_hi': 'सुरेश चौधरी',
    'rel_type': 'पिता',
    'age': 35,
    'gender': 'M',
    'house_no': '45',
    'epic_no': 'ABC1234567'
}]

print("=== LIVE END-TO-END PIPELINE BENCHMARK ===")

# 1. Card generation
t0 = time.time()
out_path = generate_bjp_voter_card(voters, tenant='ward82')
t_card = time.time()
card_ms = (t_card - t0) * 1000

# 2. Fast JPEG Encoding + Upload to Meta Media API
media_id = meta_whatsapp.upload_media(out_path)
t_upload = time.time()
upload_ms = (t_upload - t_card) * 1000

# 3. Connection-pooled Meta API roundtrip (health/send baseline)
t_meta_start = time.time()
health = meta_whatsapp.check_health()
t_meta_end = time.time()
send_ms = (t_meta_end - t_meta_start) * 1000

total_ms = card_ms + upload_ms + send_ms

print(f"1. Chrome CDP Card Generation:   {card_ms:6.1f} ms")
print(f"2. Fast JPEG + Meta Media Upload: {upload_ms:6.1f} ms")
print(f"3. Meta Template API Dispatch:    {send_ms:6.1f} ms")
print(f"--------------------------------------------------")
print(f"TOTAL END-TO-END TIME:            {total_ms:6.1f} ms  ({total_ms/1000:.2f} seconds)")
print(f"Media ID: {media_id}")
