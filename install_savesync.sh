#!/usr/bin/env sh
# Install script for SaveSync (Linux)
#
# This script downloads a prebuilt SaveSync binary release tarball from
# GitHub and installs it to /usr/local/bin (or $HOME/.local/bin when
# run without root). It supports an optional VERSION environment variable
# to pin a release, and --uninstall to remove the installed binary.
#
# Usage examples:
#   curl -fsSL https://raw.githubusercontent.com/700zx1/SaveSync/main/install_savesync.sh | sh
#   VERSION=v1.2.3 sh install_savesync.sh
#   sh install_savesync.sh --uninstall

set -eu

REPO_OWNER="700zx1"
REPO_NAME="SaveSync"
BIN_NAME="savesync"

# Allow overriding version via env var
VERSION=${VERSION:-}

GITHUB_RAW_BASE="https://github.com/${REPO_OWNER}/${REPO_NAME}/releases/download"

usage() {
    cat <<EOF
Usage: sh install_savesync.sh [--uninstall] [--dry-run]

Environment:
  VERSION=tag    Optional: a release tag like v1.2.3. If empty, the script
                 attempts to download the latest release.

Options:
  --uninstall    Remove installed binary from the install location.
  --dry-run      Print actions without downloading or installing.

Installs to /usr/local/bin if writable, otherwise to \$HOME/.local/bin.
After install, ensure that the destination directory is in your PATH.
For fish shell add to \$HOME/.config/fish/config.fish:
  set -gx PATH \$HOME/.local/bin $PATH
EOF
}

DRY_RUN=0
UNINSTALL=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --uninstall) UNINSTALL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
    esac
done

detect_arch() {
    arch=$(uname -m || echo "x86_64")
    case "$arch" in
        x86_64|amd64) echo "x86_64" ;;
        aarch64|arm64) echo "arm64" ;;
        armv7*|armv6*) echo "armv7" ;;
        *) echo "$arch" ;;
    esac
}

ARCH=$(detect_arch)

choose_dest() {
    if [ -w "/usr/local/bin" ]; then
        echo "/usr/local/bin"
    else
        echo "$HOME/.local/bin"
    fi
}

DEST_DIR=$(choose_dest)
DEST_PATH="$DEST_DIR/$BIN_NAME"

download() {
    url="$1"
    out="$2"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "DRY-RUN: would download $url -> $out"
        return 0
    fi
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$out"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$out" "$url"
    else
        echo "Error: curl or wget required to download files." >&2
        return 2
    fi
}

make_tempdir() {
    if command -v mktemp >/dev/null 2>&1; then
        tmp=$(mktemp -d 2>/dev/null || mktemp -d -t savesync)
    else
        tmp="/tmp/savesync_install_$$"
        mkdir -p "$tmp"
    fi
    echo "$tmp"
}

if [ "$UNINSTALL" -eq 1 ]; then
    if [ -f "$DEST_PATH" ]; then
        echo "Removing $DEST_PATH"
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "DRY-RUN: would remove $DEST_PATH"
            exit 0
        fi
        if [ -w "$DEST_PATH" ] || [ "$(id -u)" -eq 0 ]; then
            rm -f "$DEST_PATH"
            echo "Removed. You may also want to remove config files in ~/.config/SaveSync or ~/.local/share/SaveSync"
            exit 0
        else
            echo "Attempting to remove as root..."
            if command -v sudo >/dev/null 2>&1; then
                sudo rm -f "$DEST_PATH"
                echo "Removed via sudo."
                exit 0
            else
                echo "Cannot remove $DEST_PATH: permission denied and sudo not available." >&2
                exit 1
            fi
        fi
    else
        echo "No installed binary found at $DEST_PATH"
        exit 0
    fi
fi

# Prepare download target
TMPDIR=$(make_tempdir)
trap 'rm -rf "$TMPDIR"' EXIT INT TERM

if [ -z "$VERSION" ]; then
    # Try to discover latest tag via GitHub redirect
    LATEST_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}/releases/latest"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "DRY-RUN: would resolve latest release tag from $LATEST_URL"
        VERSION=""
    else
        if command -v curl >/dev/null 2>&1; then
            # curl -I follows redirects differently; use -sI and parse Location
            TAG=$(curl -sI -L -o /dev/null -w "%{url_effective}" "$LATEST_URL" | awk -F/ '{print $NF}')
        elif command -v wget >/dev/null 2>&1; then
            TAG=$(wget --server-response --max-redirect=0 "$LATEST_URL" 2>&1 | awk -F/ '/Location: /{print $NF; exit}')
        else
            TAG=""
        fi
        VERSION=${TAG:-}
    fi
fi

if [ -z "$VERSION" ]; then
    echo "No VERSION determined and none provided. Please set VERSION env or run the script interactively after creating a release." >&2
    exit 2
fi

ARCHIVE_NAME="${BIN_NAME}-${VERSION}-linux-${ARCH}.tar.gz"
DOWNLOAD_URL="${GITHUB_RAW_BASE}/${VERSION}/${ARCHIVE_NAME}"
ARCHIVE_PATH="$TMPDIR/$ARCHIVE_NAME"

echo "Will download: $DOWNLOAD_URL"

download "$DOWNLOAD_URL" "$ARCHIVE_PATH" || {
    echo "Download failed: $DOWNLOAD_URL" >&2
    exit 3
}

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY-RUN: would extract $ARCHIVE_PATH and install $BIN_NAME to $DEST_DIR"
    exit 0
fi

echo "Extracting archive..."
tar -xzf "$ARCHIVE_PATH" -C "$TMPDIR"

if [ ! -f "$TMPDIR/$BIN_NAME" ]; then
    echo "Archive did not contain $BIN_NAME binary." >&2
    ls -la "$TMPDIR" || true
    exit 4
fi

mkdir -p "$DEST_DIR"

install_binary() {
    src="$1"
    dst="$2"
    if [ -w "$dst" ] || [ "$(id -u)" -eq 0 ]; then
        mv "$src" "$dst"
        chmod 755 "$dst"
    else
        if command -v sudo >/dev/null 2>&1; then
            echo "Installing to $dst with sudo..."
            sudo mv "$src" "$dst"
            sudo chmod 755 "$dst"
        else
            echo "Permission denied installing to $dst and sudo is not available." >&2
            exit 1
        fi
    fi
}

install_binary "$TMPDIR/$BIN_NAME" "$DEST_PATH"

echo "Installed $BIN_NAME -> $DEST_PATH"

echo "Checking PATH..."
case ":$PATH:" in
  *":$DEST_DIR:"*) echo "$DEST_DIR is already in PATH" ;;
  *)
    echo
    echo "Note: $DEST_DIR is not in your PATH. To add it for fish shell, run:";
    echo
    echo "  set -U fish_user_paths $DEST_DIR \$fish_user_paths"
    echo
    echo "Or add to ~/.config/fish/config.fish:"
    echo "  set -gx PATH $DEST_DIR \$PATH"
    ;;
esac

echo "Done. Run '$BIN_NAME --help' to get started."
