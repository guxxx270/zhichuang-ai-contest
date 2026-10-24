#!/bin/bash
# render.sh <deck.pptx|docx> <outdir> [dpi] : -> pdf (LibreOffice, 微软雅黑→苹方 替换) -> slide-N.png
set -e
DECK="$1"; OUTD="$2"; DPI="${3:-80}"; mkdir -p "$OUTD"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROFILE=$(mktemp -d "${TMPDIR:-/tmp}/lo_profile_XXXX")
cp -R "$HERE/lo_profile_tpl/user" "$PROFILE/user"
"${SOFFICE:-/Applications/LibreOffice.app/Contents/MacOS/soffice}" -env:UserInstallation=file://$PROFILE --headless --convert-to pdf --outdir "$OUTD" "$DECK" >/dev/null 2>&1
PDF="$OUTD/$(basename "${DECK%.*}").pdf"
rm -f "$OUTD"/slide-*.png
pdftoppm -png -r "$DPI" "$PDF" "$OUTD/slide"
rm -rf "$PROFILE"
ls "$OUTD"/slide-*.png | wc -l
