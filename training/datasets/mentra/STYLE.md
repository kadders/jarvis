# MentraOS style guide (Miniapp SDK + Bluetooth SDK)

Preference pairs for Jarvis DPO. Domain: **`mentra`**.

Jarvis coaches Mentra smart-glasses development from [docs.mentraglass.com](https://docs.mentraglass.com/). Prefer **Mentra Miniapp SDK** (`@mentra/miniapp`) for apps inside the Mentra App; prefer **Mentra Bluetooth SDK** for apps that own the phone experience and talk to Mentra Live directly. Do not revive the legacy cloud `@mentra/sdk` as the current path. OEM engine APIs are out of scope for app-dev coaching.

Sources: MentraOS docs (`llms.txt` index). Miniapp SDK is beta; MentraOS 3.0 has no Miniapp Store distribution yet.

---

## SDK choice

### Miniapp vs Bluetooth

#### ✅ CORRECT - Mentra App miniapp uses Miniapp SDK:
```text
Build with @mentra/miniapp (registerMiniapp + session). Glasses connection, TTS/STT, and platform services come from the Mentra App host.
```

#### ❌ INCORRECT - Point Mentra App hosts at Bluetooth only:
```text
Always use @mentra/bluetooth-sdk even when the app runs inside the Mentra App as a miniapp.
```

### Mentra Live direct control

#### ✅ CORRECT - Own mobile app → Bluetooth SDK:
```text
For a first-party Android/iOS/RN app that connects to Mentra Live over BLE, use the Mentra Bluetooth SDK and match glasses software to the SDK version.
```

#### ❌ INCORRECT - Cloud server as current Mentra Live path:
```text
Spin up a cloud @mentra/sdk AppServer; that is still the supported way to drive Mentra Live today.
```

### Legacy cloud SDK

#### ✅ CORRECT - Redirect legacy cloud docs:
```text
@mentra/sdk cloud apps are legacy. Current on-device path is @mentra/miniapp; older cloud docs live under mentraglass.com/legacy.
```

#### ❌ INCORRECT - Scaffold new cloud AppServer:
```text
New Mentra apps should start from AppServer in @mentra/sdk with packageName and apiKey on a public server.
```

---

## Miniapp architecture

### Two layers

#### ✅ CORRECT - Background owns session; UI has no hardware:
```text
Background (@mentra/miniapp/background) always-on, owns session.* Hardware. UI is React WebView only; talk via mentra.* / session.ui.* typed channels. No DOM in background.
```

#### ❌ INCORRECT - Call session from the React UI:
```text
Import registerMiniapp into App.tsx and call session.display.render from the React tree.
```

### registerMiniapp startup

#### ✅ CORRECT - Return setup promise; register UI handlers first:
```text
registerMiniapp(async (session) => { register session.ui.handle / onOpen first; return or await setup. Do not void the setup promise. Keep slow network out of startup. })
```

#### ❌ INCORRECT - Discard setup promise:
```text
registerMiniapp((session) => { void loadEverything(); /* UI opens before handlers exist */ });
```

### Background runtime

#### ✅ CORRECT - Supported background globals only:
```text
Background is bare JS (no window/DOM/Node). Use console, timers, fetch, WebSocket, localStorage, crypto.getRandomValues/randomUUID, TextEncoder/Decoder. For elapsed time use Date.now() — not performance.now().
```

#### ❌ INCORRECT - Node fs in background:
```text
Use require("fs") and performance.now() in src/background/index.ts.
```

### Capabilities after ready

#### ✅ CORRECT - Read capabilities on ready:
```text
session.capabilities is null until ready. Subscribe immediately; read display size / canPosition inside session.on("ready", …) or onCapabilitiesChange.
```

#### ❌ INCORRECT - Read capabilities synchronously at handler start:
```text
At the top of registerMiniapp, assume session.capabilities.display.width is already set.
```

---

## Display

### Full-frame render

#### ✅ CORRECT - render replaces the whole frame:
```text
session.display.render([...]) describes the full current frame. Stable id updates in place; omit an id to remove it; render([]) clears. Prefer boxes from session.capabilities.display when ready.
```

#### ❌ INCORRECT - Append-only display API:
```text
Call session.display.appendText each time; the host accumulates history behind the element id.
```

### Layout target

#### ✅ CORRECT - Design for 500×220:
```text
Design layouts for 500×220 so they fit positioning devices; G2 576×288 leaves margin. Check canPosition for G1/Z100 text fallback.
```

#### ❌ INCORRECT - Hardcode only G2 and ignore older glasses:
```text
Always assume 576×288 positioned bitmaps; never check canPosition or provide a text-only variant.
```

---

## Scaffold and CLI

### create-mentra-miniapp

#### ✅ CORRECT - Scaffold with Bun:
```text
bunx create-mentra-miniapp my-miniapp && bun install && bun dev. Load via Mentra App → Settings → Miniapp Developer Settings → Scan Miniapp QR (same Wi-Fi, or bun dev --usb on Android).
```

#### ❌ INCORRECT - npm create with Expo Go for miniapps:
```text
npx create-expo-app and load the miniapp in Expo Go; Mentra miniapps do not use Expo Go.
```

### Packages

#### ✅ CORRECT - Three packages, one direct invoke:
```text
create-mentra-miniapp scaffolds once via bunx. @mentra/miniapp is the runtime dependency. @mentra/miniapp-cli provides mentra-miniapp dev/pack/release as a devDependency.
```

#### ❌ INCORRECT - Install create-mentra-miniapp as a dependency:
```text
Add create-mentra-miniapp to dependencies and import it from application code.
```

### Distribution caveat

#### ✅ CORRECT - Beta: no store distribution yet:
```text
Miniapp SDK is beta; MentraOS 3.0 has no Developer Console / Miniapp Store upload path yet. Local pack/install for testing only. For Mentra Live shipping apps prefer Bluetooth SDK.
```

#### ❌ INCORRECT - Claim store publish works today:
```text
Upload the miniapp to the Mentra Miniapp Store from the Developer Console in MentraOS 3.0.
```

---

## Bluetooth SDK

### Scan and connect

#### ✅ CORRECT - RN scan MentraLive then connect:
```text
import BluetoothSdk, { DeviceModels } from '@mentra/bluetooth-sdk'; const devices = await BluetoothSdk.scan(DeviceModels.MentraLive, { timeoutMs: 10000 }); await BluetoothSdk.connect(device);
```

#### ❌ INCORRECT - Use Miniapp session APIs in a standalone RN app:
```text
In a standalone React Native app, call registerMiniapp and session.display.render without the Mentra App host.
```

### Expo Go

#### ✅ CORRECT - Development build required:
```text
@mentra/bluetooth-sdk needs a development or production native build. Expo Go cannot load its native modules.
```

#### ❌ INCORRECT - Expo Go is fine:
```text
Install @mentra/bluetooth-sdk and test fully inside Expo Go.
```

### Version match

#### ✅ CORRECT - Match SDK and glasses software:
```text
Bluetooth SDK and Mentra Live glasses software must share the same release (e.g. both 3.3.0). BLE connected ≠ version-compatible; update glasses before testing features.
```

#### ❌ INCORRECT - Any glasses firmware is fine:
```text
Bluetooth connection alone confirms compatibility; skip Mentra Live software updates.
```

### Platform packages

#### ✅ CORRECT - Documented imports only:
```text
Android: com.mentraglass:bluetooth-sdk. iOS/macOS: MentraBluetoothSDK. RN: @mentra/bluetooth-sdk (hooks from @mentra/bluetooth-sdk/react). Do not rely on underscore-prefixed internals.
```

#### ❌ INCORRECT - Import private underscore paths:
```text
Import @mentra/bluetooth-sdk/_internal and treat it as stable public API.
```

---

## Feedback domain

### metadata.domain

#### ✅ CORRECT - Tag Mentra trajectories mentra:
```text
Log Mentra Miniapp and Bluetooth coaching with metadata.domain = mentra via jarvis_log_outcome / feedback.
```

#### ❌ INCORRECT - Tag Mentra as python_jarvis:
```text
Use metadata.domain = python_jarvis for all Mentra TypeScript SDK work.
```
