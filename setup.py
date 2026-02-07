#!/usr/bin/env python3
"""
Environment Setup & Verification
==================================
Checks that all dependencies, tools, and API keys are properly configured.
Optionally runs an interactive setup wizard.

Usage:
    python setup.py           # check everything
    python setup.py --wizard  # interactive setup
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from config import API_CONFIG, AUDIO_DIR, ANALYSIS_DIR, DATA_DIR, TRANSCRIPT_DIR


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

REQUIRED_PACKAGES = [
    "requests",
    "yt_dlp",
    "dotenv",
]

OPTIONAL_PACKAGES = [
    "anthropic",
    "deepgram",
]


def check_python_version() -> bool:
    """Check that Python >= 3.8."""
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 8)
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] Python version: {major}.{minor} (need >= 3.8)")
    return ok


def check_ffmpeg() -> bool:
    """Check that ffmpeg is installed and accessible."""
    path = shutil.which("ffmpeg")
    if path:
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True, timeout=10,
            )
            version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
            print(f"  [OK] FFmpeg: {version_line[:60]}")
            return True
        except (subprocess.TimeoutExpired, OSError):
            pass
    print("  [FAIL] FFmpeg not found. Install with: sudo apt install ffmpeg")
    return False


def check_ytdlp() -> bool:
    """Check that yt-dlp is installed."""
    path = shutil.which("yt-dlp")
    if path:
        try:
            result = subprocess.run(
                ["yt-dlp", "--version"], capture_output=True, text=True, timeout=10,
            )
            version = result.stdout.strip()
            print(f"  [OK] yt-dlp: {version}")
            return True
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Check as Python module
    try:
        import yt_dlp
        print(f"  [OK] yt-dlp (Python module): {yt_dlp.version.__version__}")
        return True
    except ImportError:
        pass

    print("  [FAIL] yt-dlp not found. Install with: pip install yt-dlp")
    return False


def check_packages() -> tuple[bool, list[str]]:
    """Check that required Python packages are installed."""
    missing: list[str] = []
    all_ok = True

    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
            print(f"  [OK] Package: {pkg}")
        except ImportError:
            # dotenv is imported as dotenv but installed as python-dotenv
            alt = {"dotenv": "python-dotenv"}.get(pkg, pkg)
            print(f"  [FAIL] Package: {pkg} (pip install {alt})")
            missing.append(alt)
            all_ok = False

    for pkg in OPTIONAL_PACKAGES:
        try:
            __import__(pkg)
            print(f"  [OK] Package (optional): {pkg}")
        except ImportError:
            pip_name = {"deepgram": "deepgram-sdk"}.get(pkg, pkg)
            print(f"  [WARN] Package (optional): {pkg} (pip install {pip_name})")

    return all_ok, missing


def check_api_keys() -> dict[str, bool]:
    """Check API key environment variables."""
    results: dict[str, bool] = {}
    for service, cfg in API_CONFIG.items():
        env_var = cfg["api_key_env"]
        value = os.environ.get(env_var, "")
        if value:
            masked = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
            print(f"  [OK] {env_var}: {masked}")
            results[service] = True
        else:
            print(f"  [MISS] {env_var}: not set")
            results[service] = False
    return results


def check_directories() -> bool:
    """Ensure data directories exist."""
    all_ok = True
    for d in (DATA_DIR, AUDIO_DIR, TRANSCRIPT_DIR, ANALYSIS_DIR):
        d.mkdir(parents=True, exist_ok=True)
        print(f"  [OK] Directory: {d.relative_to(Path.cwd())}")
    return all_ok


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def setup_wizard() -> None:
    """Interactive setup wizard."""
    print("\n" + "=" * 60)
    print("  Civic Housing Intelligence – Setup Wizard")
    print("=" * 60 + "\n")

    # 1. Create .env file if missing
    env_file = Path(".env")
    env_example = Path(".env.example")

    if not env_file.exists():
        if env_example.exists():
            print("Creating .env from .env.example...")
            env_file.write_text(env_example.read_text())
        else:
            print("Creating .env file...")
            env_file.write_text(
                "# Civic Housing Intelligence API Keys\n"
                "DEEPGRAM_API_KEY=\n"
                "ANTHROPIC_API_KEY=\n"
            )
        print(f"  Created: {env_file}")
    else:
        print(f"  .env already exists: {env_file}")

    # 2. Prompt for API keys
    print("\n--- API Key Configuration ---")
    for service, cfg in API_CONFIG.items():
        env_var = cfg["api_key_env"]
        current = os.environ.get(env_var, "")
        if current:
            print(f"  {env_var} is already set.")
            continue

        print(f"\n  {service.upper()} API Key ({env_var}):")
        if service == "deepgram":
            print("    Get your key at: https://console.deepgram.com/")
        elif service == "anthropic":
            print("    Get your key at: https://console.anthropic.com/")

        key = input(f"    Enter {env_var} (or press Enter to skip): ").strip()
        if key:
            # Append to .env
            with open(env_file, "a") as f:
                f.write(f"\n{env_var}={key}\n")
            os.environ[env_var] = key
            print(f"    Saved to .env")

    # 3. Install missing packages
    print("\n--- Package Installation ---")
    _, missing = check_packages()
    if missing:
        answer = input(f"\n  Install missing packages ({', '.join(missing)})? [Y/n] ").strip()
        if answer.lower() != "n":
            subprocess.run([sys.executable, "-m", "pip", "install"] + missing)
            print("  Packages installed.")

    # 4. Check FFmpeg
    if not shutil.which("ffmpeg"):
        print("\n  FFmpeg is required for audio processing.")
        print("  Install with:")
        print("    Ubuntu/Debian: sudo apt install ffmpeg")
        print("    macOS:         brew install ffmpeg")
        print("    Windows:       https://ffmpeg.org/download.html")

    # 5. Create directories
    print("\n--- Directory Setup ---")
    check_directories()

    print("\n" + "=" * 60)
    print("  Setup complete!")
    print("  Run: python meeting_ingestion_pipeline.py --city Denver --limit 3")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Main verification
# ---------------------------------------------------------------------------

def run_checks() -> bool:
    """Run all checks and return overall status."""
    print("\n" + "=" * 60)
    print("  Civic Housing Intelligence – Environment Check")
    print("=" * 60)

    all_ok = True

    print("\n--- Python ---")
    if not check_python_version():
        all_ok = False

    print("\n--- External Tools ---")
    if not check_ffmpeg():
        all_ok = False
    if not check_ytdlp():
        all_ok = False

    print("\n--- Python Packages ---")
    pkg_ok, _ = check_packages()
    if not pkg_ok:
        all_ok = False

    print("\n--- API Keys ---")
    api_status = check_api_keys()
    if not api_status.get("deepgram"):
        print("    (Transcription will be unavailable without Deepgram key)")
    if not api_status.get("anthropic"):
        print("    (AI analysis will be unavailable without Anthropic key)")

    print("\n--- Data Directories ---")
    check_directories()

    print("\n" + "-" * 60)
    if all_ok and all(api_status.values()):
        print("  All checks passed. Ready to run the pipeline.")
    elif all_ok:
        print("  Core dependencies OK. Set API keys to enable full pipeline.")
        print("  Run: python setup.py --wizard")
    else:
        print("  Some checks failed. Fix the issues above.")
        print("  Run: python setup.py --wizard")
    print("-" * 60 + "\n")

    return all_ok


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Environment Setup & Verification")
    parser.add_argument("--wizard", action="store_true", help="Run interactive setup wizard")
    args = parser.parse_args()

    if args.wizard:
        setup_wizard()
    else:
        run_checks()


if __name__ == "__main__":
    main()
