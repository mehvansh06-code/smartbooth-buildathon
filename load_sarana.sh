(echo "SET session_replication_role = 'replica';" && zcat /tmp/voters_t5.sql.gz) | sudo -u postgres psql -d voter_platform -v ON_ERROR_STOP=0
sudo -u postgres psql -d voter_platform << 'EOF'
UPDATE tenants 
SET candidate_name = 'Neelu Dhoniwal (नीलू धोनीवाल)', 
    status = 'ACTIVE',
    config = jsonb_set(COALESCE(config, '{}'::jsonb), '{symbol_name_en}', '"Tractor"'),
    config = jsonb_set(COALESCE(config, '{}'::jsonb), '{symbol_name_hi}', '"ट्रैक्टर"')
WHERE tenant_id = 5;

SELECT tenant_id, campaign_slug, candidate_name, status, (SELECT count(*) FROM voters_t5) as electors FROM tenants WHERE tenant_id = 5;
EOF
