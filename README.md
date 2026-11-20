# ⚡ SmartBooth Autonomous AI (v2)
### Google DeepMind × Google Cloud Startup Buildathon India 2026 • GUSEC Ahmedabad

> **High-impact Autonomous Electoral Operations & Multimodal AI Platform**  
> Powered by **Gemini 2.5 Flash on Vertex AI**, **Google Compute Engine (Mumbai)**, **Pillow & FFmpeg Media Stitching**, and **WhatsApp Cloud Messaging**.

---

## 📌 Executive Summary & Hackathon Mission

In Indian grassroots elections (Panchayat, Municipal, Legislative Assembly), over **960 Million voters** and **2.5 Million candidates** struggle with high friction:
1. **Zero-UI Provisioning Friction**: Setting up election campaigns, symbols, and karyakarta access takes days.
2. **Severe Phonetic & Dialectical Variance**: Illiterate or rural voters cannot type complex names in English or formal Hindi; dialectical shifts ($v \leftrightarrow w \leftrightarrow b$, $sh \leftrightarrow s$, $ee \leftrightarrow i$) break standard search engines.
3. **Fragmented Household Voting**: In rural booths, individual voters do not go alone—voting decisions happen at the household unit.
4. **Impersonal Outreach**: Blanket mass texting is ignored; voters expect personalized, video-driven recognition from candidates.

**SmartBooth Autonomous AI (v2)** solves this through a fully integrated, zero-friction 4-pipeline engine operated via natural voice and chat, backed by real-time observability and sub-second multimodal video synthesis.

---

## 🌐 Live Cloud Infrastructure (Google Cloud Mumbai)

The entire production stack is deployed and operational on Google Cloud Platform:

| Component | Target / URL | Details |
| :--- | :--- | :--- |
| **Google Cloud VM** | `smartbooth-demo` | Ubuntu 24.04 LTS (x86_64), `asia-south1-a` (Mumbai) |
| **Public IPv4** | `8.231.107.200` | Direct HTTP/Nginx Reverse Proxy |
| **Canonical Domain** | `https://build.smartbooth.in` | Cloudflare SSL + Edge Caching |
| **Stage Demo Console** | `https://build.smartbooth.in/demo` | Real-time dual-pane stage demo cockpit |
| **Systemd Service** | `smartbooth.service` | Gunicorn WSGI workers on `127.0.0.1:5030` |
| **Database** | `voter_platform` (PostgreSQL) | Active partition `voters_t5` (Gram Panchayat Sarana, Ajmer) |
| **LLM Engine** | **Gemini 2.5 Flash** | Vertex AI (`project-de57b711-daa3-47db-9c0`, `us-central1`) |

---

## 🔒 Stage Demo Whitelist Protection Guard

To prevent accidental WhatsApp spam to real citizens during live pitch testing, a strict security whitelist is enforced in `electionapp/services/buildathon_agent.py`:

```python
# Whitelist for Live Stage Delivery
WHATSAPP_DEMO_WHITELIST = ["9828579249", "8502852579"]
```

- **Vansh (CEO)**: `+91 98285 79249` (Whitelisted for live WhatsApp messages)
- **Devansh (CTO)**: `+91 85028 52579` (Whitelisted for live WhatsApp messages)
- *All other numbers automatically receive simulated delivery state `WHITELIST_GUARD_ACTIVE` with full trace logging.*

---

## 🚀 Quick Start for CTO Devansh (Local Environment)

### 1. Clone the Dedicated Repository
```bash
git clone https://github.com/mehvansh06-code/smartbooth-buildathon.git
cd smartbooth-buildathon
```

### 2. Create & Activate Python Virtual Environment
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Ensure your `.env` contains:
```ini
PORT=5030
PGDATABASE=voter_platform
PGUSER=postgres
PGPASSWORD=postgres
PGHOST=localhost
PGPORT=5432
GOOGLE_GENAI_USE_VERTEXAI=True
GOOGLE_CLOUD_PROJECT=project-de57b711-daa3-47db-9c0
GOOGLE_CLOUD_LOCATION=us-central1
WHATSAPP_DEMO_WHITELIST=9828579249,8502852579
PRIMARY_WA_GATEWAY=aoc
AOC_API_KEY=cc3uNWmCxr9utFa2V716rBYarq8IPd
AOC_SENDER_NUMBER=919351147539
AOC_TEMPLATE_NAME=info_update_short
```

### 5. Restore Demo Dataset (Gram Panchayat Sarana - Tenant 5)
If running a local PostgreSQL instance:
```bash
# Decompress and restore Sarana GP partition (voters_t5)
gzip -dc voters_t5.sql.gz | psql -U postgres -d voter_platform

# Sync Tenant 5 metadata (Candidate: Neelu Dhoniwal, Symbol: Tractor)
python update_sarana.py
```

### 6. Run the Application
```bash
python run_local.py
```
- Open browser to **Demo Console**: `http://127.0.0.1:5030/demo`
- Open root portal: `http://127.0.0.1:5030/?tenant=sarana`

### 7. Run Test Verification Suite
```bash
pytest tests/test_video_stitching.py tests/test_voice_agent.py
```
*(All tests should execute with 100% pass rate).*

---

## 🏛️ Architecture: The 4 Core AI Pipelines

```
                             ┌──────────────────────────────────────────────┐
                             │    SmartBooth Autonomous AI Cockpit (/demo)   │
                             └───────────────────────┬──────────────────────┘
                                                     │
           ┌───────────────────────┬─────────────────┴───────────────┬───────────────────────┐
           ▼                       ▼                                 ▼                       ▼
    [ Pipeline 1 ]          [ Pipeline 2 ]                    [ Pipeline 3 ]          [ Pipeline 4 ]
Zero-UI Provisioning      Zero-Search Phonetic             One-Tap Family Slip      Multimodal Video Engine
      & Magic Link            Household Graph                  Dispatcher             & Outreach Calls
           │                       │                                 │                       │
 ┌─────────┴─────────┐   ┌─────────┴─────────┐             ┌─────────┴─────────┐   ┌─────────┴─────────┐
 │ Gemini 2.5 Flash  │   │ Gemini Phonetic   │             │ Co-located Family │   │ ElevenLabs Audio  │
 │ Tool Calling      │   │ Normalization     │             │ Aggregator        │   │ FFmpeg Concatenat │
 │ DB Tenant Created │   │ SQL Household Map │             │ AOC WhatsApp API  │   │ Badge & Symbol OV │
 └───────────────────┘   └───────────────────┘             └───────────────────┘   └───────────────────┘
```

---

### Pipeline 1: Zero-UI Campaign Provisioning Engine

**Objective**: Allow a candidate or organizer to speak or text: *"Create Sarana campaign for Neelu Dhoniwal with Tractor symbol"*. The agent autonomously writes to PostgreSQL, provisions credentials, and sends the admin magic link via WhatsApp.

- **Primary Tool**: `create_campaign_and_send_link` in `electionapp/services/buildathon_agent.py`
- **Endpoint**: `POST /api/agent/command`
- **Request Payload**:
  ```json
  {
    "prompt": "Create a new campaign for Neelu Dhoniwal in Gram Panchayat Sarana, Ajmer with Tractor symbol for phone 9828579249"
  }
  ```
- **Response**:
  ```json
  {
    "status": "SUCCESS",
    "reply": "अभियान 'Gram Panchayat Sarana' (प्रत्याशी: नीलू धोनीवाल, चुनाव चिन्ह: ट्रैक्टर) सफलतापूर्वक सक्रिय कर दिया गया है।",
    "traces": [...]
  }
  ```

---

### Pipeline 2: Zero-Search Phonetic Household Voter Graph

**Objective**: Rural voters speak names with phonetic drift (`"नीलू"`, `"नेलू"`, `"दुनीवाल"`, `"धोनीवाल"`). The agent maps variations, queries the `voters_t5` database partition, pulls the entire co-located family at the same `house_no`, and synthesizes a spoken conversational Hindi response.

- **Primary Tool**: `lookup_household_votes` in `electionapp/services/buildathon_agent.py`
- **Endpoint**: `POST /api/agent/lookup`
- **Request Payload**:
  ```json
  {
    "query": "Neelu Dhoniwal",
    "gram_panchayat": "Sarana"
  }
  ```
- **Response**:
  ```json
  {
    "status": "SUCCESS",
    "phonetic_normalization": {
      "raw_input": "Neelu Dhoniwal",
      "resolved_name_en": "Neelu Dhoniwal",
      "resolved_name_hi": "नीलू धोनीवाल"
    },
    "matched_primary_voter": {
      "name_hi": "नीलू दुनीवाल",
      "ward_no": 7,
      "house_no": "79"
    },
    "household_id": "w7_h79",
    "total_household_votes": 5,
    "spoken_hindi_response": "नीलू धोनीवाल जी, आपके परिवार (मकान नं 79) में कुल 5 वोट हैं। आपका मतदान केंद्र बूथ #14 (राजकीय उच्च माध्यमिक विद्यालय, सराना) है। क्या मैं परिवार के सभी सदस्यों की डिजिटल वोटर पर्ची आपके व्हाट्सएप पर भेज दूँ?"
  }
  ```

---

### Pipeline 3: One-Tap Digital Family Voter Slip Dispatcher

**Objective**: Dispatches pre-formatted digital voter slips for every registered member of that household directly to the citizen's WhatsApp.

- **Primary Tool**: `send_family_voter_slips` in `electionapp/services/buildathon_agent.py`
- **Endpoint**: `POST /api/agent/dispatch`
- **Request Payload**:
  ```json
  {
    "household_id": "w7_h79",
    "phone": "9828579249"
  }
  ```
- **Response**:
  ```json
  {
    "status": "DISPATCHED",
    "household_id": "w7_h79",
    "recipient": "9828579249",
    "is_whitelisted_demo_number": true,
    "slips_generated": 5,
    "delivery_state": "SENT",
    "receipt": {
      "message_id": "wamid_demo_1732080000",
      "status": "DELIVERED"
    }
  }
  ```

---

### Pipeline 4: Multimodal Autonomous Video Personalization & Outreach

**Objective**:
1. **Outreach Calling Agent**: Triggers a simulated Gemini Live sales outreach call to candidate while **simultaneously delivering their digital voter slip on WhatsApp upon call connection** (the "Aha!" moment).
2. **Dynamic Video Synthesis**: Takes raw voter name (e.g. `"SMT REKHA DEVI W/O RAMESH"`), normalizes salutation (`"Rekha ji"`), synthesizes a personalized audio greeting, overlays the candidate's Tractor symbol and Booth #14 badge, and concatenates it with `master_video.mp4` using FFmpeg in **< 2 seconds**.

- **Primary Service**: `electionapp/services/video_stitching.py`
- **Endpoints**:
  - `POST /api/agent/call`
  - `POST /api/agent/generate_video`
- **Video Generation Request**:
  ```json
  {
    "voter_name": "SMT REKHA DEVI W/O RAMESH",
    "candidate_name": "नीलू धोनीवाल",
    "booth_info": "Booth #14, Rajkiya Vidyalaya Sarana"
  }
  ```
- **Response**:
  ```json
  {
    "status": "SUCCESS",
    "salutation": "Rekha ji",
    "video_url": "/static/videos/personalized_sample.mp4",
    "duration_seconds": 15.6,
    "latency_ms": 1420.5
  }
  ```

---

## 📊 Live Trace Stream & Observability

Every action executed by the autonomous agent across all 4 pipelines logs an event to an in-memory ring buffer (`TRACE_BUFFER`) displayed in real-time on the right pane of `/demo`:

- **Endpoint**: `GET /api/agent/traces`
- **Event Schema**:
  ```json
  {
    "id": "trace_1732081234",
    "timestamp": "11:42:15.320",
    "event_type": "TOOL_CALL",
    "tool_name": "lookup_household_votes",
    "input": { "query": "Rekha Devi", "gp": "Sarana" },
    "output": { "total_household_votes": 5, "household_id": "w7_h79" },
    "latency_ms": 340.2,
    "metadata": { "phonetic_match": "'Rekha Devi' -> 'रेखा देवी'" }
  }
  ```

---

## 🛠️ Deployment & Production Maintenance (For CTO Devansh)

### Deploying Updates to Google Cloud VM
To update the live VM `smartbooth-demo` (`8.231.107.200`):

```bash
# 1. SSH into the Google Cloud VM
ssh -i ~/.ssh/google_compute_engine mehva@8.231.107.200

# 2. Navigate to project root
cd /home/mehva/smartbooth

# 3. Pull latest code or sync files
# (If using git once configured on VM, or sync via scp)

# 4. Restart services
sudo systemctl restart smartbooth.service
sudo systemctl status smartbooth.service --no-pager

# 5. Check live Gunicorn and Nginx logs
sudo journalctl -u smartbooth.service -f -n 50
```

---

## 👥 Hackathon Team Credentials & Roles

- **Vansh (CEO & Lead)**: `meh.vansh06@gmail.com` | `+91 98285 79249`
- **Devansh (CTO & Co-founder)**: `devanshkalla77@gmail.com` | `+91 85028 52579`
- **Hackathon**: Startup Buildathon India 2026 (Google DeepMind × Google Cloud × GUSEC)

*SmartBooth — Transforming Democratic Participation with Autonomous AI.*
