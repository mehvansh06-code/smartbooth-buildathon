import psycopg2, json

conn = psycopg2.connect(dbname="voter_platform", user="postgres", password="postgres", host="localhost")
conn.autocommit = True
cur = conn.cursor()

config_data = {
    "symbol_name_en": "Tractor",
    "symbol_name_hi": "ट्रैक्टर",
    "symbol_filename": "tractor.png",
    "theme_color": "#16a34a",
    "district": "Ajmer",
    "gram_panchayat": "Sarana",
    "booth_info": "Booth #14, Rajkiya Uchch Madhyamik Vidyalaya, Sarana"
}

cur.execute("""
    UPDATE tenants 
    SET candidate_name = 'Neelu Dhoniwal (नीलू धोनीवाल)', 
        status = 'ACTIVE',
        config = %s
    WHERE tenant_id = 5
""", (json.dumps(config_data),))

cur.execute("SELECT tenant_id, campaign_slug, candidate_name, status, config FROM tenants WHERE tenant_id = 5")
print("Tenant 5:", cur.fetchone())

cur.close()
conn.close()
print("Tenant 5 updated!")
