#!/usr/bin/env python3
"""
D3 Scene Renderer — renders D3/Canvas HTML templates via Puppeteer.

For each scene with style == "d3_animation", writes a JSON config,
calls `node render_scene.js`, and produces an MP4 clip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

D3_DIR = Path(__file__).resolve().parent.parent / "d3_scenes"
RENDER_SCRIPT = D3_DIR / "render_scene.js"


def render_d3_scene(
    template: str,
    d3_config: dict,
    output_path: Path,
    duration: float,
    resolution: tuple[int, int] = None,
    fps: int = None,
) -> bool:
    """
    Render a single D3 scene to MP4.

    Args:
        template:    HTML template filename in d3_scenes/ (e.g. "migration_map.html")
        d3_config:   Template-specific configuration dict
        output_path: Where to write the .mp4
        duration:    Scene duration in seconds
        resolution:  (width, height), defaults to config.OUTPUT_RESOLUTION
        fps:         Frames per second, defaults to config.OUTPUT_FPS

    Returns:
        True on success, False on failure.
    """
    res = resolution or config.OUTPUT_RESOLUTION
    frame_rate = fps or config.OUTPUT_FPS

    # Build full config for the renderer
    full_config = {
        "resolution": list(res),
        "fps": frame_rate,
        "duration": duration,
        "output_path": str(output_path.resolve()),
        "template": template,
        **d3_config,
    }

    # Write config to a temp JSON next to the output
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    config_path = output_path.parent / f"_d3cfg_{output_path.stem}.json"

    try:
        with open(config_path, "w") as f:
            json.dump(full_config, f, indent=2)

        # Check node + render script exist
        if not RENDER_SCRIPT.exists():
            print(f"    [!] render_scene.js not found at {RENDER_SCRIPT}")
            return False

        # Check node_modules
        if not (D3_DIR / "node_modules").exists():
            print("    [!] node_modules missing — run: cd d3_scenes && npm install")
            return False

        cmd = ["node", str(RENDER_SCRIPT), "--config", str(config_path)]
        print(f"    [d3] {template} → {output_path.name} ({duration}s, {frame_rate}fps)")

        result = subprocess.run(
            cmd,
            cwd=str(D3_DIR),
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max per scene
        )

        # Stream renderer output
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                print(f"    {line}")

        if result.returncode != 0:
            print(f"    [!] Renderer failed (exit {result.returncode})")
            if result.stderr:
                for line in result.stderr.strip().split("\n")[:10]:
                    print(f"    {line}")
            return False

        return output_path.exists()

    except subprocess.TimeoutExpired:
        print("    [!] Render timed out (10 min)")
        return False
    except FileNotFoundError:
        print("    [!] Node.js not found. Install Node.js to render D3 scenes.")
        return False
    finally:
        # Clean up temp config
        if config_path.exists():
            config_path.unlink()
