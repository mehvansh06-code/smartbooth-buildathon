# ⚡ SmartBooth AI • Hackathon Pitch Deck
### Google DeepMind × Google Cloud Startup Buildathon India 2026 • GUSEC Ahmedabad
**Tagline:** *Empowering 960 Million Voters & 2.5 Million Candidates through Autonomous Multimodal AI*

---

## 🧭 Slide 1: Title & Executive Overview

```
 ╔═════════════════════════════════════════════════════════════════════════════╗
 ║                              SMARTBOOTH AI                                  ║
 ║     The Autonomous Multimodal Electoral Infrastructure for India            ║
 ╚═════════════════════════════════════════════════════════════════════════════╝
```

- **Project:** SmartBooth AI Ecosystem
- **Team:** 
  - **Vansh Mehta** — Chief Executive Officer (`+91 98285 79249`)
  - **Devansh Agarwal** — Chief Technology Officer (`+91 85028 52579`)
- **Core Technology:** Google Vertex AI (Gemini 2.5 Flash & Gemini 3.8 Live API), Google Compute Engine (Mumbai), WebSocket Audio Streaming, FFmpeg Sub-Second Video Stitching, WhatsApp Cloud Platform.
- **Production Deployments:**
  - 🌐 **Pipeline 1 (Household Voter Graph & Parchi):** [https://build.smartbooth.in](https://build.smartbooth.in)
  - 🎥 **Pipeline 2 (Multimodal Video Personalization):** [https://video.smartbooth.in](https://video.smartbooth.in)
  - 🎙️ **Pipeline 3 (Gemini Live Full-Duplex Voice Outreach):** [https://voiceagent.smartbooth.in](https://voiceagent.smartbooth.in)

---

## 🚨 Slide 2: The Problem: Grassroots Electoral Disconnect

India has **968 Million registered voters** and **2.5 Million+ grassroots candidates** (Panchayat, Municipal, Legislative Assembly). During election campaigns, high friction creates massive democratic disconnect:

1. **Severe Phonetic & Dialectical Variance:**
   - Illiterate or semi-literate rural citizens cannot type formal English or Hindi names into search bars.
   - Regional phonetic drift ($w \leftrightarrow v$, $sh \leftrightarrow s$, $ee \leftrightarrow i$, $b \leftrightarrow v$) breaks traditional voter search databases.
2. **Fragmented Household Voting Units:**
   - In Indian booths, citizens do not vote as isolated individuals—**families vote collectively as a household unit**. Traditional portals give individual search results, leaving families confused about booth locations.
3. **Impersonal Mass Spam Getting Zero Engagement:**
   - Blanket robo-calls and mass SMS blasts are marked as spam. Voters ignore text blasts; they demand personal recognition and face-to-face connection with candidates.
4. **Zero Pre-Poll Grievance Feedback Loop:**
   - Candidates do not know their ward's critical pain points (water scarcity, electricity cuts, broken roads) before voting day, leading to missed campaign priorities.

---

## 💡 Slide 3: The Solution: SmartBooth's 3-Pipeline AI Engine

SmartBooth transforms grassroots electoral outreach from static paper slips and mass spam into a **hyper-personalized, conversational, and autonomous multimodal experience**.

```
                           ┌────────────────────────────────────────────────┐
                           │            SMARTBOOTH AI PLATFORM              │
                           └───────────────────────┬────────────────────────┘
                                                   │
        ┌──────────────────────────────────┼──────────────────────────────────┐
        ▼                                  ▼                                  ▼
 ┌──────────────────────┐          ┌──────────────────────┐          ┌──────────────────────┐
 │     PIPELINE 1       │          │     PIPELINE 2       │          │     PIPELINE 3       │
 │ Zero-Search Phonetic │          │ Hyper-Personalized   │          │ Full-Duplex Real-Time│
 │ Household Graph &    │          │ Campaign Video       │          │ Voice Feedback &     │
 │ Digital Parchi       │          │ Synthesis Engine     │          │ Outreach Agent       │
 ├──────────────────────┤          ├──────────────────────┤          ├──────────────────────┤
 │ build.smartbooth.in  │          │ video.smartbooth.in  │          │ voiceagent.smartbooth│
 ├──────────────────────┤          ├──────────────────────┤          ├──────────────────────┤
 │ • Gemini 2.5 Flash   │          │ • Surgical Splice    │          │ • Gemini 3.8 Live    │
 │ • Phonetic Matching  │          │ • Candidate Voice    │          │ • Bidirectional PCM  │
 │ • Co-located Family  │          │ • Devanagari Badges  │          │ • Real-time Redressal│
 │ • WhatsApp 1-Tap     │          │ • < 2s Render Time   │          │ • Dynamic Voting Chinh│
 └──────────────────────┘          └──────────────────────┘          └──────────────────────┘
```

---

## 🏛️ Slide 4: Deep Dive — Pipeline 1: Zero-Search Household Voter Graph & Digital Parchi

### 🌐 Live Production: `https://build.smartbooth.in`

#### What It Does:
Voters and booth karyakartas speak or text dialect-heavy names into WhatsApp or the web portal. The autonomous agent normalizes phonetic drift, finds the registered voter, **reconstructs their entire co-located household**, and delivers digital voter slips directly to their phone.

#### Core Capabilities:
- **Phonetic Normalization Engine:** Resolves severe spelling drift using Gemini 2.5 Flash on Vertex AI (e.g., *"Neelu Dunival"* $\rightarrow$ *"Neelu Dhoniwal"*; *"Smt Rekha w/o Ramesh"* $\rightarrow$ *"Rekha Devi"*).
- **Household Co-location Graph:** Re-indexes raw electoral rolls by `ward_no` and `house_no`, grouping all 4–8 family members onto a unified digital voting card.
- **Booth Discovery & Navigation:** Delivers exact Polling Station (Polling Booth #, Room #, School name, Google Maps pin).
- **WhatsApp Cloud Dispatcher:** Sends official WhatsApp digital voter slips with one tap without requiring app downloads.

---

## 🎥 Slide 5: Deep Dive — Pipeline 2: Hyper-Personalized Campaign Video Engine

### 🌐 Live Production: `https://video.smartbooth.in`

#### What It Does:
Produces broadcast-quality personalized campaign video appeals where the **candidate addresses every single voter by their own name and honorific title** in Hindi/regional dialects, with the candidate's verified election symbol stamped directly onto the video.

#### Core Capabilities:
- **Surgical Acoustic Splice Architecture:** Combines personalized synthesized voice greetings with the candidate's authentic recorded video speech body at calibrated acoustic cuts.
- **Dynamic Devanagari Badge & Ballot Banner Overlay:** Pillow-rendered graphics dynamically stamp the voter's name (`"श्रीमती रेखा जी"`), booth number, and candidate ballot button directly into video frames.
- **Sub-2-Second Rendering Pipeline:** Highly optimized FFmpeg pipeline with multi-threaded encoding that renders and encodes personalized MP4 videos in **< 1.8 seconds**.
- **WhatsApp Streaming Optimized:** Strict compliance gate ensuring 720×1280 @ 30fps H.264, AAC 48kHz, `< 3.3MB` payload, and `+faststart` moov-atom for instant mobile video playback without buffering.

---

## 🎙️ Slide 6: Deep Dive — Pipeline 3: Real-Time Voice Outreach & Grievance Agent

### 🌐 Live Production: `https://voiceagent.smartbooth.in`

#### What It Does:
A real-time, two-way conversational voice agent that calls voters or talks through the web booth. Powered by **Vertex AI Gemini Live API**, the agent speaks natural Hindi, listens to locality problems, cataloging grievances into a live database via **Function Calling**, and delivers a customized election symbol appeal.

#### Core Capabilities:
- **True Full-Duplex Speech-to-Speech:** Zero intermediate STT-TTS transcription latency. Uses bidirectional streaming with 16kHz Linear PCM microphone input and 24kHz audio synthesis.
- **Proactive Opening & Empathetic Greeting:** Instantly greets citizens in respectful conversational Hindi:
  > *"Namaste [Voter First Name] ji! Hum [Candidate Name] ji ki taraf se baat kar rahe hain. Aapke kshetra [Ward] mein paani, bijli, sadak ya safai jaisi kaunsi samasya hai?"*
- **Real-Time Grievance Logging via Tool Calling:** When the voter shares an issue (*"Hamare yahan 3 din se peene ka paani nahi aa raha"*), the agent autonomously executes `record_voter_issue(locality, category, description, urgency)` in real-time into SQLite/PostgreSQL.
- **Dynamic Candidate & Election Symbol Vote Appeal:** Concludes every interaction with an election appeal tuned to the candidate's symbol:
  > *"Kripya [Candidate Name] ji ke chunav chinh [Symbol] ka button dabayein aur unhein bhari maton se jitayein."*
- **Acoustic Echo Guard & Sub-Second Latency:** Robust client-side Web Audio VAD with `< 850ms` turn completion response time.

---

## ⚙️ Slide 7: Technical Architecture & Google Cloud Foundation

```
                                  [ USER TOUCHPOINTS ]
                     WhatsApp Cloud API  •  Web Booth  •  Mobile Web
                                         │
                                         ▼
                     ┌───────────────────────────────────────┐
                     │     Cloudflare Edge CDN & SSL Proxy   │
                     │  (Cache-Busting, WSS Upgrade, DDoS)   │
                     └───────────────────┬───────────────────┘
                                         │
                                         ▼
                     ┌───────────────────────────────────────┐
                     │ Google Cloud Compute Engine (Mumbai)  │
                     │  IP: 8.231.107.200 (Ubuntu 24.04 LTS) │
                     │  • Nginx WebSocket Reverse Proxy      │
                     │  • Gunicorn + FastAPI Async Services  │
                     └───────────────────┬───────────────────┘
                                         │
            ┌────────────────────────────┼────────────────────────────┐
            ▼                            ▼                            ▼
 ┌──────────────────────┐   ┌──────────────────────────┐   ┌──────────────────────┐
 │    DATA LAYER        │   │   GOOGLE VERTEX AI       │   │    MEDIA ENGINE      │
 │ • PostgreSQL 16      │   │ • Gemini 2.5 Flash       │   │ • FFmpeg Audio Cut   │
 │ • SQLite Issue DB    │   │ • Gemini 3.8 Live Bidi   │   │ • Pillow Devanagari  │
 │ • Household Partitions│  │ • Tool Calling Engine    │   │ • 24kHz PCM Playback │
 └──────────────────────┘   └──────────────────────────┘   └──────────────────────┘
```

### Why Google Cloud & Vertex AI?
1. **Gemini 3.8 Live API:** Native speech-to-speech multimodal capabilities allow natural regional Indian dialect comprehension and low-latency audio response without fragmented STT $\rightarrow$ LLM $\rightarrow$ TTS pipelines.
2. **Vertex AI Tool Calling:** Reliable, structured tool execution (`record_voter_issue`) directly during live voice conversations.
3. **Mumbai Region (asia-south1):** Ultra-low latency edge networking (< 30ms round-trip across Indian telecom networks).

---

## 📈 Slide 8: Market Opportunity & Unit Economics

### The Addressable Market
- **2,500,000+ Candidates** contest local elections across India every 5-year cycle (Panchayat, Ward Councilors, Nagar Palika, Vidhan Sabha).
- Average grassroots candidate spend: **₹1,50,000 to ₹15,00,000** ($2,000 to $20,000) on manual printing, paper slips, sound trucks, and karyakarta mobilization.

### Cost Comparison (Paper vs SmartBooth AI)

| Metric | Traditional Campaign | SmartBooth Autonomous AI |
| :--- | :--- | :--- |
| **Outreach Medium** | Printed paper parchi + flyers | WhatsApp Family Parchi + Video + Voice Call |
| **Cost per Voter** | ₹4.50 – ₹7.00 per voter | **₹0.45 – ₹0.90 per voter** (85% cheaper) |
| **Delivery Time** | 4–7 days of door-to-door visits | **Instant (< 2 seconds per household)** |
| **Personalization** | Zero (generic party flyers) | **100% Hyper-personalized (Name, Symbol, Video)** |
| **Issue Feedback** | None (one-way leaflets) | **Automated ward grievance cataloging** |

---

## 🎯 Slide 9: Live Demo Walkthrough (Stage Script)

```
[ Step 1: 0 to 45s ]
→ Open https://build.smartbooth.in
→ Speak in dialect Hindi: "Neelu Dhoniwal, Sarana"
→ System normalizes spelling, displays 5 co-located family members at House #79, 
  and dispatches WhatsApp Digital Parchi instantly.

[ Step 2: 45s to 90s ]
→ Open https://video.smartbooth.in
→ Input voter name: "Rekha Devi" with Tractor symbol.
→ Watch personalized video render in 1.4 seconds with candidate voice & Devanagari badge.

[ Step 3: 90s to 150s ]
→ Open https://voiceagent.smartbooth.in
→ Input Candidate: "Rahul Sharma", Symbol: "Kamal", Voter: "Vansh".
→ Click "Place Call" — Gemini Live immediately greets in natural Hindi:
  "Namaste Vansh ji! Hum Rahul Sharma ji ki taraf se baat kar rahe hain..."
→ Voter answers: "Hamare ward mein 3 din se paani nahi aa raha hai."
→ AI acknowledges, logs grievance live into the Ward Redressal Dashboard, and concludes:
  "Kripya Rahul Sharma ji ke chunav chinh Kamal ka button dabayein aur jitayein!"
```

---

## 🏆 Slide 10: Traction, Validation & Vision

- **Live Pilot Deployment:** Tested on real voter records from **Gram Panchayat Sarana, Ajmer** (active partition `voters_t5`) and Jodhpur urban wards.
- **Production Infrastructure:** 3 live public domains hosted on Google Cloud Compute Engine with Cloudflare security.
- **Future Roadmap:**
  - **Q1 2026:** Autonomous multilingual dialect expansion (Gujarati, Marathi, Bhojpuri, Bengali).
  - **Q2 2026:** AI War Room dashboard for candidates with sentiment heatmaps and booth-level turnout tracking.
  - **Q3 2026:** Autonomous WhatsApp Inbound Agent handling citizen queries 24/7 during election week.

---

## 👥 Slide 11: Team & Contact

```
  ┌───────────────────────────────────────────────────────────────┐
  │                        SMARTBOOTH AI                          │
  │     Building the Democratic Intelligence Layer for India      │
  ├───────────────────────────────┬───────────────────────────────┤
  │ Vansh Mehta                   │ Devansh Agarwal               │
  │ Chief Executive Officer       │ Chief Technology Officer      │
  │ meh.vansh06@gmail.com         │ devansh.agarwal7310@gmail.com │
  │ +91 98285 79249               │ +91 85028 52579               │
  └───────────────────────────────┴───────────────────────────────┘
```
- **GitHub Organization:** `https://github.com/mehvansh06-code`
- **Live Platform Portals:**
  - [https://build.smartbooth.in](https://build.smartbooth.in)
  - [https://video.smartbooth.in](https://video.smartbooth.in)
  - [https://voiceagent.smartbooth.in](https://voiceagent.smartbooth.in)
