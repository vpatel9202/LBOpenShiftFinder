#!/usr/bin/env bash
# ============================================================
# LBOpenShiftFinder — GCP Provisioning Script
#
# Automates the Google Cloud setup required for calendar sync:
#   1. Creates a GCP project
#   2. Enables the Google Calendar API
#   3. Creates a service account
#   4. Downloads a JSON key
#
# Requirements:
#   gcloud CLI: https://cloud.google.com/sdk/docs/install
#   Authenticated: run `gcloud auth login` first
#
# Usage:
#   ./scripts/setup_gcp.sh <project-id>
#
# The project-id must be globally unique across all of GCP.
# Example: ./scripts/setup_gcp.sh lb-shift-finder-yourname
# ============================================================

set -euo pipefail

# --- Args ---
PROJECT_ID="${1:-}"
if [[ -z "$PROJECT_ID" ]]; then
    echo "Usage: $0 <project-id>"
    echo ""
    echo "  project-id must be globally unique across all of GCP."
    echo "  Example: lb-shift-finder-yourname"
    exit 1
fi

SA_NAME="lb-shift-sync"
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
KEY_FILE="gcp-service-account-key.json"

echo "=== LBOpenShiftFinder — GCP Setup ==="
echo "  Project ID:      $PROJECT_ID"
echo "  Service account: $SA_EMAIL"
echo "  Key output:      $KEY_FILE"
echo ""

# --- Check gcloud is installed and authenticated ---
if ! command -v gcloud &>/dev/null; then
    echo "ERROR: gcloud CLI not found."
    echo "Install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | grep -q .; then
    echo "ERROR: No active gcloud account. Run: gcloud auth login"
    exit 1
fi

# --- Create project ---
if gcloud projects describe "$PROJECT_ID" &>/dev/null; then
    echo "Project '$PROJECT_ID' already exists — skipping creation."
else
    echo "Creating project '$PROJECT_ID'..."
    gcloud projects create "$PROJECT_ID"
fi

gcloud config set project "$PROJECT_ID" --quiet

# --- Enable Calendar API ---
echo "Enabling Google Calendar API..."
gcloud services enable calendar-json.googleapis.com --project="$PROJECT_ID"
echo "  Done."

# --- Create service account ---
if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
    echo "Service account '$SA_NAME' already exists — skipping creation."
else
    echo "Creating service account '$SA_NAME'..."
    gcloud iam service-accounts create "$SA_NAME" \
        --project="$PROJECT_ID" \
        --display-name="LB Shift Sync" \
        --description="Service account for LBOpenShiftFinder Google Calendar sync"
    echo "  Done."
fi

# --- Generate JSON key ---
if [[ -f "$KEY_FILE" ]]; then
    echo "Key file '$KEY_FILE' already exists."
    read -rp "  Overwrite and generate a new key? [y/N] " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "  Skipping key generation."
        KEY_GENERATED=false
    else
        gcloud iam service-accounts keys create "$KEY_FILE" --iam-account="$SA_EMAIL"
        KEY_GENERATED=true
    fi
else
    echo "Generating JSON key ($KEY_FILE)..."
    gcloud iam service-accounts keys create "$KEY_FILE" --iam-account="$SA_EMAIL"
    KEY_GENERATED=true
fi

# --- Done ---
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Manual step required — share your Google Calendar with the service account:"
echo "  1. Open https://calendar.google.com/"
echo "  2. Open your target calendar > Settings and sharing"
echo "  3. Under 'Share with specific people or groups', add:"
echo "       $SA_EMAIL"
echo "  4. Set permission to 'Make changes to events'"
echo ""

if [[ "${KEY_GENERATED:-false}" == "true" ]]; then
    echo "To configure your .env, run the interactive setup script:"
    echo "  python scripts/configure.py"
    echo ""
    echo "Or flatten the JSON key manually for GOOGLE_SERVICE_ACCOUNT_JSON:"
    echo "  python3 -c \"import json,sys; print(json.dumps(json.load(sys.stdin)))\" < $KEY_FILE"
    echo ""
    echo "SECURITY: Keep $KEY_FILE private — do not commit it to git."
    echo "  It is listed in .gitignore to prevent accidental commits."
fi
