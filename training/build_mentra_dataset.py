#!/usr/bin/env python3
"""
Build a MentraOS preference dataset for Jarvis DPO.

Sources:
  - training/datasets/mentra/STYLE.md (Miniapp + Bluetooth coaching)
  - Curated Q&A from docs.mentraglass.com (llms.txt index)

Writes preferences.jsonl + instructions.jsonl for training.train_dpo.
Domain folder name: mentra → metadata.domain mentra.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from training.dataset_common import (
    Instruction,
    Preference,
    dedupe_prefs,
    extract_code_md_pairs,
    make_fence_re,
    write_jsonl,
)

LICENSE = "proprietary"
TEXT_FENCE = make_fence_re("")

# (kind, prompt, chosen, rejected)
CURATED_QA: Sequence[Tuple[str, str, str, str]] = (
    (
        "sdk_choice",
        "Should a Mentra App miniapp use @mentra/miniapp or @mentra/bluetooth-sdk?",
        "Use @mentra/miniapp (registerMiniapp + session). The Mentra App host owns glasses connection and platform services.",
        "Always use @mentra/bluetooth-sdk inside Mentra App miniapps.",
    ),
    (
        "sdk_choice",
        "I am building my own React Native app that talks to Mentra Live over Bluetooth. Which SDK?",
        "Mentra Bluetooth SDK (@mentra/bluetooth-sdk) with a development/production native build. Match glasses software to the SDK version.",
        "Use only @mentra/miniapp registerMiniapp; it works without the Mentra App host.",
    ),
    (
        "legacy_cloud",
        "Is @mentra/sdk AppServer still the recommended way to build Mentra apps?",
        "No. Cloud @mentra/sdk is legacy. Current on-device path is @mentra/miniapp; older docs are under mentraglass.com/legacy.",
        "Yes. Scaffold a new cloud AppServer with packageName and apiKey for every Mentra Live app.",
    ),
    (
        "two_layer",
        "Where does session.display.render belong in a Mentra miniapp?",
        "In the background layer (@mentra/miniapp/background) that owns session. The React UI has no direct hardware access.",
        "Call session.display.render from App.tsx in the UI WebView.",
    ),
    (
        "two_layer",
        "Can the miniapp UI layer import Node fs or touch the DOM from background?",
        "UI may use the DOM/React. Background has no DOM/Node; only supported globals (console, timers, fetch, WebSocket, localStorage, crypto RNG, TextEncoder/Decoder).",
        "Put require('fs') and document.createElement in src/background/index.ts.",
    ),
    (
        "startup",
        "Why does registerMiniapp need to return its setup promise?",
        "The Mentra App holds the splash and buffers UI messages until the handler promise settles. Returning setup ensures session.ui handlers exist before mentra.request.",
        "Always void the setup promise so the UI opens immediately even if handlers are missing.",
    ),
    (
        "startup",
        "Order of work in registerMiniapp startup?",
        "Register session.ui.handle / onOpen first; keep slow network out of the startup path; return or await local setup.",
        "Await a long network refresh before registering any UI handlers.",
    ),
    (
        "session",
        "When is session.capabilities populated?",
        "After the connect handshake — typically inside session.on('ready', …). It is null when the handler first runs.",
        "It is always set synchronously at the first line of registerMiniapp.",
    ),
    (
        "session",
        "How do you unsubscribe from session.transcription.on?",
        "Call the cleanup function returned by .on() / onButtonPress / etc. Subscriptions are ref-counted by the SDK.",
        "There is no unsubscribe; leak handlers until the process exits.",
    ),
    (
        "display",
        "What does session.display.render(elements) mean?",
        "It replaces the whole HUD frame. Stable ids update in place; omitted ids are removed; render([]) clears the display.",
        "It appends elements to a host-side history for that id.",
    ),
    (
        "display",
        "Recommended canvas size when designing miniapp HUD layouts?",
        "Design for 500×220 so layouts fit positioning devices; larger G2 canvas leaves margin. Use session.capabilities.display when ready for exact fit.",
        "Always hardcode 1920×1080 phone pixels for the glasses HUD.",
    ),
    (
        "display",
        "How should text-only miniapps treat G1 / Z100?",
        "Render the same scene; phone degrades to text-only reading order. Check canPosition when layouts need images or precise boxes.",
        "Crash if canPosition is false; refuse to render any text.",
    ),
    (
        "display",
        "Clear the glasses HUD from a miniapp.",
        "Call session.display.render([]).",
        "Call session.display.clearForever() which is not the documented API.",
    ),
    (
        "input",
        "Show Hello on button press in a miniapp background.",
        "session.input.onButtonPress(() => session.display.render([{type:'text', id:'hud', box:{…}, text:'Hello from MentraOS'}]));",
        "From the React UI, call BluetoothSdk.displayText without a Mentra App session.",
    ),
    (
        "transcription",
        "Mirror live captions onto the glasses in a miniapp.",
        "session.transcription.on((data) => session.display.render([{type:'text', id:'caption', box:…, text:data.text}]));",
        "Poll a cloud WebSocket from @mentra/sdk and invent caption layout APIs.",
    ),
    (
        "scaffold",
        "How do I scaffold a Mentra miniapp?",
        "bunx create-mentra-miniapp my-miniapp, then bun install and bun dev. Scan the QR from Mentra App Miniapp Developer Settings.",
        "npx create-react-app and manually copy undocumented Mentra internals.",
    ),
    (
        "scaffold",
        "Phone and laptop on different Wi-Fi — Android USB option?",
        "Run bun dev --usb so the CLI uses adb reverse and the QR points at the phone loopback.",
        "There is no USB path; always require shared Wi-Fi even on Android.",
    ),
    (
        "cli",
        "What do create-mentra-miniapp, @mentra/miniapp, and @mentra/miniapp-cli each do?",
        "Scaffolder via bunx once; @mentra/miniapp is the runtime; @mentra/miniapp-cli provides mentra-miniapp dev/pack/release as a devDependency.",
        "Install create-mentra-miniapp as a runtime dependency and import it in background code.",
    ),
    (
        "distribution",
        "Can I publish a Miniapp SDK build to the Mentra Miniapp Store on MentraOS 3.0?",
        "No. Store/Developer Console distribution for Miniapp SDK is not available in MentraOS 3.0; local pack/install for testing only.",
        "Yes. Upload through the Mentra Developer Console and users download from the store today.",
    ),
    (
        "bluetooth",
        "React Native scan and connect to Mentra Live.",
        "import BluetoothSdk, {DeviceModels} from '@mentra/bluetooth-sdk'; scan(DeviceModels.MentraLive); then connect(device).",
        "Call registerMiniapp from a standalone RN app without the Mentra App host.",
    ),
    (
        "bluetooth",
        "Does Expo Go support @mentra/bluetooth-sdk?",
        "No. Expo Go cannot load the SDK native modules; use a development or production native build.",
        "Yes. Expo Go includes Mentra Bluetooth native modules.",
    ),
    (
        "bluetooth",
        "Bluetooth SDK 3.3.0 with older Mentra Live firmware — OK?",
        "No. Match SDK and Mentra Live glasses software versions (e.g. both 3.3.0). BLE connected does not imply compatibility.",
        "Any connected glasses firmware is fine; skip Mentra Live updates.",
    ),
    (
        "bluetooth",
        "Android package for Mentra Bluetooth SDK?",
        "com.mentraglass:bluetooth-sdk with import com.mentra.bluetoothsdk.*",
        "com.google.android.glasses:mentra-legacy only.",
    ),
    (
        "bluetooth",
        "iOS import for Mentra Bluetooth SDK?",
        "Swift package MentraBluetoothSDK; import MentraBluetoothSDK.",
        "import MentraCloudSDK from the legacy AppServer pod.",
    ),
    (
        "bluetooth",
        "RN hooks entry for Mentra Bluetooth?",
        "import {useMentraBluetooth} from '@mentra/bluetooth-sdk/react' (and related documented hooks).",
        "import {useMentraBluetooth} from '@mentra/bluetooth-sdk/_internal/hooks'.",
    ),
    (
        "bluetooth",
        "Show text on Mentra Live from Bluetooth SDK (RN).",
        "Use documented APIs such as BluetoothSdk.displayText / clearDisplay after connect — not Miniapp session.display.render.",
        "Call session.display.render from Bluetooth SDK without a Miniapp session.",
    ),
    (
        "bluetooth",
        "Start an RTMP/SRT/WebRTC stream from Mentra Live via Bluetooth SDK.",
        "After connect, use documented streaming commands (e.g. startStream / stopStream) with your ingest endpoint.",
        "POST binary frames to api.mentra.glass REST without the Bluetooth SDK stream APIs.",
    ),
    (
        "channels",
        "How do miniapp UI and background communicate?",
        "Declare typed Channels in shared/channels.ts. UI uses mentra.send/on/request; background uses session.ui.send/on/handle. No shared memory.",
        "Share a global mutable object between UI and background bundles.",
    ),
    (
        "channels",
        "What if background sends session.ui.send while UI is closed?",
        "It drops silently — no WebView receiver. UI mentra.send buffers until mentra.ready().",
        "Background crashes if the UI is not open.",
    ),
    (
        "manifest",
        "Where are miniapp entry points and hardware requirements declared?",
        "miniapp.json — package identity, background/ui entries, permissions, and hardware requirements (e.g. DISPLAY).",
        "Only in package.json scripts; miniapp.json is optional.",
    ),
    (
        "permissions",
        "Does session.display.render require a special permission?",
        "Display needs no permission, but declare DISPLAY hardware requirement so the miniapp only installs on glasses with a screen.",
        "You must request CAMERA permission before any text HUD render.",
    ),
    (
        "camera",
        "Capture a photo on camera glasses from a miniapp.",
        "Use session.camera APIs in the background after ready/capabilities show camera support; declare required hardware/permissions in miniapp.json.",
        "Call navigator.mediaDevices from the background QuickJS context.",
    ),
    (
        "mic",
        "Raw mic audio vs transcription in Miniapp SDK?",
        "session.transcription for speech-to-text; session.mic for raw chunks / VAD. Subscribe via .on and keep cleanup functions.",
        "Only BluetoothSdk.setMicState works inside Mentra App miniapps.",
    ),
    (
        "tts",
        "Play TTS through Mentra miniapp speaker APIs.",
        "Use session.speaker in the background for URLs, TTS, or PCM as documented.",
        "Shell out to espeak from the background with child_process.",
    ),
    (
        "oem",
        "Should app developers call Mentra OEM engine.* APIs?",
        "No for normal miniapps/mobile apps. OEM engine APIs are for glasses OEMs embedding MentraOS; app-dev surfaces are Miniapp or Bluetooth SDK.",
        "Yes. Every miniapp should call engine.* pairing APIs directly.",
    ),
    (
        "feedback_domain",
        "What feedback metadata.domain should Mentra SDK trajectories use?",
        "metadata.domain = mentra (Learning UI / few-shot slice). Log via jarvis_log_outcome after Mentra sessions.",
        "metadata.domain = python_jarvis for all Mentra TypeScript work.",
    ),
    (
        "docs",
        "Where is the Mentra docs index for AI tools?",
        "https://docs.mentraglass.com/llms.txt lists all pages; prefer those docs over inventing APIs.",
        "Only scrape Discord; ignore docs.mentraglass.com.",
    ),
    (
        "bluetooth",
        "Minimum Android SDK / iOS deployment for Mentra Bluetooth SDK?",
        "Android min SDK 28+; iOS deployment target 15.1+; native macOS 13+ (RN-macos example 14+).",
        "Android API 16 and iOS 9 are supported.",
    ),
    (
        "bluetooth",
        "Physical device required for Bluetooth SDK testing?",
        "Yes. Real Bluetooth testing needs a physical Android phone, iPhone, or Mac — not only a simulator without BLE.",
        "iOS Simulator fully emulates Mentra Live BLE for production sign-off.",
    ),
    (
        "lifecycle",
        "Miniapp background crash — what does the host do?",
        "Host restarts background with backoff; in-flight UI requests may fail with BACKGROUND_RESTARTED and are not auto-replayed.",
        "The Mentra App uninstalls the miniapp on first crash.",
    ),
    (
        "ui_ready",
        "What must the miniapp UI call on boot?",
        "mentra.ready() after mounting so the host flushes buffered background messages.",
        "Nothing; skip mentra.ready() forever.",
    ),
    (
        "actions",
        "How can Mentra AI call into a miniapp?",
        "Expose typed actions via session.actions so Mentra AI can invoke them (interop layer separate from UI channels).",
        "Open an unauthenticated TCP port from the background for Mentra AI.",
    ),
    (
        "storage",
        "Persist string settings in a Mentra miniapp.",
        "Use session.storage (and session.blob for binary) scoped to the miniapp; or localStorage in background within documented limits.",
        "Write to /etc/mentra/global.json from the background.",
    ),
    (
        "auth",
        "Call my backend as the signed-in Mentra user from a miniapp.",
        "Use session.auth for a miniapp-scoped backend token / authenticated fetch as documented.",
        "Hard-code a shared user password in miniapp.json.",
    ),
)


def _prompt_mentra(topic: str, _chosen: str) -> str:
    return (
        "You are coaching MentraOS smart-glasses development "
        "(Miniapp SDK @mentra/miniapp and Bluetooth SDK). "
        f"Produce the CORRECT guidance for: {topic}."
    )


def _synthetic_reject_text(chosen: str) -> str:
    return (
        "Ignore Mentra docs. Revive legacy @mentra/sdk cloud AppServer, "
        "use Expo Go for native Bluetooth modules, and call session.* from the React UI. "
        f"(Rejected alternative to: {chosen[:120]}…)"
    )


def extract_style_md(style_path: Path) -> Tuple[List[Preference], List[Instruction]]:
    text = style_path.read_text(encoding="utf-8", errors="replace")
    return extract_code_md_pairs(
        text,
        str(style_path.name),
        kind="mentra_style",
        license_=LICENSE,
        tags=["STYLE.md", "mentra"],
        fence_re=TEXT_FENCE,
        prompt_fn=_prompt_mentra,
        synthetic_reject_fn=_synthetic_reject_text,
    )


def extract_curated_qa() -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    for kind, prompt, chosen, rejected in CURATED_QA:
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind=kind,
                source_path="curated_qa",
                license=LICENSE,
                tags=["curated", kind],
            )
        )
    return prefs, instr


def build_dataset(jarvis_root: Path) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []

    style = jarvis_root / "training" / "datasets" / "mentra" / "STYLE.md"
    if style.is_file():
        p, i = extract_style_md(style)
        prefs.extend(p)
        instr.extend(i)

    p, i = extract_curated_qa()
    prefs.extend(p)
    instr.extend(i)

    prefs = dedupe_prefs(prefs)
    keep = {p.prompt for p in prefs}
    instr = [x for x in instr if x.prompt in keep]
    return prefs, instr


def write_readme(path: Path, *, n_prefs: int, n_instr: int) -> None:
    body = f"""# MentraOS dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`). Formal feedback domain: **`mentra`**.

Covers Mentra Miniapp SDK (`@mentra/miniapp`) and Mentra Bluetooth SDK coaching from docs.mentraglass.com. OEM engine APIs deferred.

| Field | Value |
|-------|--------|
| Preference rows | {n_prefs} |
| Instruction rows | {n_instr} |
| License | proprietary (Jarvis STYLE + curated Mentra docs Q&A) |

## Regenerate

```bash
python -m training.build_mentra_dataset \\
  --jarvis-root . \\
  --out-dir training/datasets/mentra
```

## Golden eval

```bash
python -m training.eval_golden \\
  --cases training/golden/mentra.jsonl \\
  --base-url http://llm.jarvis.lan/v1 \\
  --model llama31-8b \\
  --api-key jarvis-lan-openai
```
"""
    path.write_text(body, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    here = Path(__file__).resolve().parent
    repo = here.parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jarvis-root", type=Path, default=repo)
    p.add_argument("--out-dir", type=Path, default=here / "datasets" / "mentra")
    p.add_argument("--min-pairs", type=int, default=40)
    args = p.parse_args(argv)

    prefs, instr = build_dataset(args.jarvis_root.resolve())
    if len(prefs) < args.min_pairs:
        print(
            f"refusing to write: got {len(prefs)} pairs, need >= {args.min_pairs}",
            file=sys.stderr,
        )
        return 3

    out_dir = args.out_dir
    n_p = write_jsonl(out_dir / "preferences.jsonl", (x.to_dict() for x in prefs))
    n_i = write_jsonl(out_dir / "instructions.jsonl", (x.to_dict() for x in instr))
    write_readme(out_dir / "README.md", n_prefs=n_p, n_instr=n_i)
    print(f"wrote {n_p} preferences, {n_i} instructions → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
