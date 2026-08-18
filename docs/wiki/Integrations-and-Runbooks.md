# 🔌 Integrations & Runbooks

This guide covers operational best practices and critical gotchas for our main smart home integrations: **Frigate NVR**, **Zigbee2MQTT**, **go2rtc Streaming**, and **Lovelace Dashboards**.

---

## 📹 Frigate NVR & AI Computer Vision

Frigate processes RTSP camera streams using local hardware acceleration:

```mermaid
flowchart LR
    subgraph Cameras["📹 PoE Security Cameras"]
        RTSP["RTSP Stream (Main / Sub)"]
    end

    subgraph VideoPipeline["🖥️ Home Assistant Server (Frigate Stack)"]
        G2R["go2rtc (Port 1984)<br/>Low-latency WebRTC / RTSP restreamer"]
        QS["Hardware Video Acceleration<br/>Video decode"]
        AI_Acc["⚡ AI Accelerator<br/>Local object detection"]
        HA_Events["HA Frigate Integration<br/>(Events, clips, occupancy sensors)"]
    end

    subgraph Displays["📺 Smart Displays"]
        Nest["Google Nest Hubs / Cast<br/>(mp4 stream from go2rtc)"]
    end

    RTSP --> G2R
    G2R --> QS --> AI_Acc --> HA_Events
    G2R -->|"Direct H.264 MP4 stream"| Nest
```

### 🚨 Critical Gotcha: Streaming to Google Cast / Nest Displays

| Method | Syntax | Status | Why |
|---|---|---|---|
| ❌ `camera.play_stream` | `action: camera.play_stream` | **BROKEN (500 Error)** | HA's internal HLS stream packaging fails on Google Cast devices |
| ✅ **go2rtc MP4 Direct** | `action: media_player.play_media` | **FAST & STABLE** | Uses go2rtc's direct MP4 repackaging on port 1984 |

#### Correct Automation Example:
```yaml
action:
  - action: media_player.play_media
    target:
      entity_id: media_player.kitchen_display
    data:
      media_content_id: "http://192.168.1.100:1984/api/stream.mp4?src=driveway"
      media_content_type: "video/mp4"
```

> [!WARNING]
> **Stopping Cast Streams:** Always use `action: media_player.turn_off` (NOT `media_player.media_stop`) to dismiss active camera streams on Google Nest Hubs.

---

## 🐝 Zigbee2MQTT (Z2M) & The 250ms Delay Rule

Our Zigbee network uses a **Network PoE Coordinator** communicating over Ethernet.

```mermaid
flowchart TD
    Coord["📡 Network PoE Coordinator"]
    
    subgraph Mesh["Zigbee Mesh Network"]
        Router1["💡 Hardwired Light / Plug (Router)"]
        Router2["🔌 In-Wall Switch (Router)"]
        End1["🔋 Motion Sensor (End Device)"]
        End2["🌡️ Temperature Sensor (End Device)"]
    end

    Coord <--> Router1 <--> Router2
    Router1 -.-> End1
    Router2 -.-> End2
```

### ⚠️ The 250ms Inter-Command Delay Rule

Zigbee radios can become overwhelmed if bombarded with rapid back-to-back commands for the same device (causing missed commands or dropped packets).

```mermaid
sequenceDiagram
    participant Automation
    participant Zigbee Device

    Note over Automation,Zigbee Device: ❌ BAD: Rapid-fire commands
    Automation->>Zigbee Device: 1. Turn on
    Automation->>Zigbee Device: 2. Set brightness to 100% (Packet Dropped!)
    Automation->>Zigbee Device: 3. Set color temperature (Dropped!)

    Note over Automation,Zigbee Device: ✅ GOOD: 250ms throttle delay
    Automation->>Zigbee Device: 1. Turn on
    Note over Automation: delay: 250ms
    Automation->>Zigbee Device: 2. Set brightness to 100%
    Note over Automation: delay: 250ms
    Automation->>Zigbee Device: 3. Set color temperature
```

Always insert a short `delay: "00:00:00.250"` between multiple sequential commands sent to the same Zigbee entity.

---

## 🖼️ Lovelace Dashboard Storage Mode

Home Assistant dashboards are managed in **Storage Mode** (`.storage/lovelace.lovelace`).

```mermaid
flowchart TD
    Query["REST API call to /api/lovelace"] --> Err["❌ Returns 404 (Expected in Storage Mode)"]
    SSH["SSH to Server (/config/.storage/lovelace.lovelace)"] --> Edit["✅ Direct edit or Web UI"]
    Edit --> Restart["🔄 Restart HA to apply changes"]
```

### Dashboard Entity Cleanup Checklist
When you rename or delete an entity, helper, or timer:
1. **Audit Dashboard Cards**: Check if the old entity name is still in `.storage/lovelace.lovelace`.
2. **Remove Orphaned Cards**: Leaving deleted entities on cards causes ugly "Entity Not Available" warnings in the UI.
