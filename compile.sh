#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")"

# Note: -halt-on-error is intentionally omitted. The TL2025 kernel ships
# without the LaTeX 2025-06-01 tagged-PDF socket API that tcolorbox 6.9.0
# expects, so we get a recoverable "\relax after \the" warning inside
# tcolorbox boxes. pdflatex falls back to zero and produces a correct PDF;
# halt-on-error would turn the warning into a fatal. Remove this comment
# once the kernel is upgraded or tcolorbox is downgraded.
latexmk -pdf -f -interaction=nonstopmode main.tex
status=$?

latexmk -c

if [[ -f main.pdf ]]; then
  echo "✓ main.pdf built"
  exit 0
else
  echo "✗ main.pdf not produced (latexmk exit $status)"
  exit 1
fi
