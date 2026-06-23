// Convert an SVG to a tight-bounding-box PDF using headless Chromium via Playwright.
//
// Usage: bun run svg_to_pdf.mjs <input.svg> [output.pdf]
//
// Two-pass render:
//   1. Load SVG at its native viewBox, query the actual content bounding box
//      via getBBox() so we know where the drawn elements truly start and end.
//   2. Rewrite the viewBox to that tight bbox and render to PDF at exactly
//      that size.
//
// Result: the PDF has no surrounding whitespace and \includegraphics[width=...]
// in LaTeX scales the actual diagram, not the SVG canvas.

import { chromium } from "playwright";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, dirname, basename, extname } from "node:path";

const [, , inputArg, outputArg] = process.argv;
if (!inputArg) {
  console.error("usage: svg_to_pdf.mjs <input.svg> [output.pdf]");
  process.exit(1);
}

const inputPath = resolve(inputArg);
const outputPath = outputArg
  ? resolve(outputArg)
  : resolve(dirname(inputPath), basename(inputPath, extname(inputPath)) + ".pdf");

const PADDING = 2; // px of whitespace around content; small non-zero value avoids clipping stroke widths and arrowheads at the bbox edge

const svgRaw = await readFile(inputPath, "utf8");

// Pass 1: load the SVG at a generous canvas, ask the browser for the bounding
// box of all drawn content. We strip width/height attributes so the SVG fills
// whatever container we give it, which gives getBBox stable user-space coords.
const svgForMeasuring = svgRaw.replace(
  /<svg\b([^>]*)>/,
  (m, attrs) => {
    const cleaned = attrs
      .replace(/\swidth\s*=\s*"[^"]*"/i, "")
      .replace(/\sheight\s*=\s*"[^"]*"/i, "");
    return `<svg${cleaned} width="2000" height="2000">`;
  }
);

const measureHtml = `<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body { margin: 0; padding: 0; background: white; }
  svg { display: block; }
</style></head><body>${svgForMeasuring}</body></html>`;

const browser = await chromium.launch();
const context = await browser.newContext({ deviceScaleFactor: 2 });
const page = await context.newPage();
await page.setContent(measureHtml, { waitUntil: "networkidle" });

// getBBox on the root <svg> returns the union of all drawn descendants in
// SVG user-space coordinates. This is what we want to crop to.
const bbox = await page.evaluate(() => {
  const svg = document.querySelector("svg");
  const b = svg.getBBox();
  return { x: b.x, y: b.y, width: b.width, height: b.height };
});

if (!bbox.width || !bbox.height) {
  console.error("could not determine SVG content bounding box");
  await browser.close();
  process.exit(2);
}

const cropX = bbox.x - PADDING;
const cropY = bbox.y - PADDING;
const cropW = bbox.width + 2 * PADDING;
const cropH = bbox.height + 2 * PADDING;

// Pass 2: rewrite the viewBox to the tight crop, force pixel width/height
// equal to that crop, and render to PDF at the same dimensions.
const svgFixed = svgRaw
  .replace(/viewBox\s*=\s*"[^"]*"/i, `viewBox="${cropX} ${cropY} ${cropW} ${cropH}"`)
  .replace(
    /<svg\b([^>]*)>/,
    (m, attrs) => {
      const cleaned = attrs
        .replace(/\swidth\s*=\s*"[^"]*"/i, "")
        .replace(/\sheight\s*=\s*"[^"]*"/i, "");
      return `<svg${cleaned} width="${cropW}" height="${cropH}">`;
    }
  );

const renderHtml = `<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body { margin: 0; padding: 0; background: white; }
  svg { display: block; }
</style></head><body>${svgFixed}</body></html>`;

await page.setViewportSize({ width: Math.ceil(cropW), height: Math.ceil(cropH) });
await page.setContent(renderHtml, { waitUntil: "networkidle" });

const pdfBuffer = await page.pdf({
  width: `${cropW}px`,
  height: `${cropH}px`,
  margin: { top: 0, right: 0, bottom: 0, left: 0 },
  printBackground: true,
  preferCSSPageSize: false,
});

await writeFile(outputPath, pdfBuffer);
await browser.close();

console.log(
  `wrote ${outputPath}  bbox=${cropW.toFixed(1)}x${cropH.toFixed(1)}  ` +
    `(was ${svgRaw.match(/viewBox\s*=\s*"([^"]+)"/)?.[1] ?? "?"})`
);
