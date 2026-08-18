# 🏗️ System Architecture

This page outlines the high-level system topology, software components, and communication channels that make up this smart home management setup.

---

## 🖥️ System Topology

The system operates across dedicated local server infrastructure, network-attached coordinator gateways, and IP security cameras:

```mermaid
flowchart TB
    subgraph Rack["🏠 Home Server & Local Network"]
        direction TB
        Host["🖥️ Home Assistant Server<br/>• OS: Home Assistant OS (HAOS)<br/>• Hardware Video Acceleration<br/>• Container / Add-on Runtime"]
        AI_Acc["⚡ AI Neural Accelerator<br/>(Local Object Detection)"]
        Switch["🔀 Local Network / PoE Switch"]
        Coord["📡 Network Zigbee Coordinator<br/>(PoE / Ethernet Gateway)"]
        Cams["📹 Security Cameras<br/>(RTSP H.264/H.265 streams)"]

        Host --- AI_Acc
        Host <--> Switch
        Switch <--> Coord
        Switch <--> Cams
    end

    subgraph Mesh["🌿 Smart Home Devices"]
        ZDevices["💡 Zigbee Mesh Devices<br/>(Lights, Motion Sensors, Plugs, Climate)"]
        Coord -.->|Zigbee Wireless| ZDevices
    end

    subgraph Client["💻 Development & Management"]
        Workstation["💻 Local Machine / AI Agent<br/>(Git, Python, ha_cli, Make)"]
        Workstation <--> Switch
    end
```

### Key System Components

| Component | Role | Purpose |
|---|---|---|
| **Host Server** | Home Assistant Core & Add-ons | Main smart home operating system and automation engine |
| **AI Co-Processor** | Local Object Detection | Runs real-time AI computer vision on camera feeds with low CPU overhead |
| **Video Processing** | Hardware Video Acceleration | Transcodes and restreams live RTSP camera feeds via go2rtc |
| **Zigbee Gateway** | Network Zigbee Coordinator | Communicates over local network to manage Zigbee mesh devices |

---

## 📡 The 4 Communication Channels

Your local computer (and AI assistants) interact with Home Assistant through **four distinct channels**, each specialized for a specific job:

```mermaid
flowchart LR
    subgraph Local["💻 Local Machine / AI Agent"]
        CLI["ha_cli & Make"]
        Code["Python Modules (HAClient)"]
        AI["AI Agents (OpenCode, Antigravity)"]
    end

    subgraph Channels["📡 4 Access Channels"]
        C1["1. rsync over SSH<br/>Port 22 (SSH Key)"]
        C2["2. REST API<br/>Port 8123 (Long-Lived Token)"]
        C3["3. WebSocket API<br/>Port 8123 (Live events/traces)"]
        C4["4. MCP Server<br/>Port 9583 (JSON-RPC / SSE)"]
    end

    subgraph Server["🏠 Home Assistant Server"]
        HA_FS["📂 /config File System"]
        HA_Core["⚙️ HA Core Engine"]
        HA_MCP["🤖 ha-mcp Addon"]
    end

    CLI -->|make pull / make push| C1 --> HA_FS
    Code -->|curl / state reads / validation| C2 --> HA_Core
    CLI -->|ha_cli trace| C3 --> HA_Core
    AI -->|High-level tool actions| C4 --> HA_MCP
```

### Channel Comparison Table

| Channel | Protocol / Port | Auth Method | Used For | Why Needed |
|---|---|---|---|---|
| **1. rsync over SSH** | SSH (Port 22) | Ed25519 Key | `make pull`, `make push` | Fast, incremental file synchronization of your YAML configs and blueprints. |
| **2. REST API** | HTTP (Port 8123) | Bearer Token (`HA_TOKEN`) | `ha_cli curl`, `make reload`, validator checks | Standard programmatic API for reading states, rendering Jinja2 templates, and triggering reloads. |
| **3. WebSocket API** | WS (Port 8123) | Bearer Token | `ha_cli trace` | Real-time bi-directional streaming of automation execution traces and live bus events. |
| **4. MCP Server** | HTTP / SSE (Port 9583) | Private Token URL | AI Agent Tools | High-level natural-language tools (88+ tools) allowing AI to read states, search configs, and test services cleanly. |

---

## 🔄 The Asymmetric Rsync Safety Boundary

The most critical safety feature in this repository is the **asymmetric push/pull design**:

```mermaid
flowchart TB
    subgraph ServerSide["🏠 Home Assistant Server (/config)"]
        direction LR
        S_Storage[".storage/<br/><b>Live Runtime State</b><br/>(Entity registry, auth, Lovelace UI)"]
        S_Z2M["zigbee2mqtt/<br/>(DB & coordinator backups)"]
        S_YAML["*.yaml<br/>(Automations, scripts, scenes)"]
    end

    subgraph LocalSide["💻 Local Workspace (config/)"]
        direction LR
        L_Storage[".storage/<br/><b>Read-Only Reference</b><br/>(Used by offline validators)"]
        L_Z2M["zigbee2mqtt/<br/>(Local config mirror)"]
        L_YAML["*.yaml<br/>(Your edited YAML files)"]
    end

    %% Pull Flow
    S_Storage ==>|"📥 make pull (Snapshots state)"| L_Storage
    S_Z2M ==>|"📥 make pull"| L_Z2M
    S_YAML ==>|"📥 make pull"| L_YAML

    %% Push Flow
    L_YAML -->|"🚀 make push (Validated files)"| S_YAML
    L_Z2M -->|"🚀 make push (Configs only)"| S_Z2M

    %% Blocked Push
    L_Storage -.->|"⛔ BLOCKED BY .rsync-excludes-push"| S_Storage
```

### Why is `.storage/` Never Pushed?

- **`.storage/` contains Home Assistant's internal database of live entities, tokens, and device states.**
- **During `make pull`:** `.storage/` is copied locally so the local validator (`EntityReferenceValidator`) can check if referenced sensors actually exist without having to query the server each time.
- **During `make push`:** `.rsync-excludes-push` strictly blocks `.storage/` from being sent back. If local files ever overwrote `.storage/`, it would corrupt the live entity registry and erase newly paired devices!

---

> [!IMPORTANT]
> **Golden Rule:** Never manually edit files inside `config/.storage/` on your local computer. If you need to make changes to dashboard UI or entity registries, use Home Assistant's web UI, MCP tools, or SSH directly.
