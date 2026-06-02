#!/usr/bin/env node
/**
 * Generic D3 Scene Renderer
 *
 * Each HTML template must expose:
 *   window._rendererReady  — set to true when initialization is complete
 *   window._renderFrame(f) — advance animation to frame index f
 *   window._rendererError  — (optional) error string if init failed
 *
 * Usage:
 *   node render_scene.js --config <config.json> --template <template.html>
 */

import { readFileSync, mkdirSync, rmSync, existsSync, writeFileSync, unlinkSync, statSync, createReadStream } from "fs";
import { dirname, join, resolve, basename, extname } from "path";
import { fileURLToPath } from "url";
import { execFileSync } from "child_process";
import { createServer } from "http";
import puppeteer from "puppeteer";

const __dirname = dirname(fileURLToPath(import.meta.url));

const args = process.argv.slice(2);

function getArg(name) {
    const idx = args.indexOf(name);
    return idx !== -1 && args[idx + 1] ? args[idx + 1] : null;
}

const configPath = resolve(getArg("--config") || "");
const templateArg = getArg("--template");

if (!configPath || !existsSync(configPath)) {
    console.error("Usage: node render_scene.js --config <config.json> --template <template.html>");
    process.exit(1);
}

const config = JSON.parse(readFileSync(configPath, "utf-8"));

const templateName = templateArg || config.template;
if (!templateName) {
    console.error("Error: No template specified. Use --template <file.html> or set config.template");
    process.exit(1);
}
const templatePath = resolve(__dirname, templateName);
if (!existsSync(templatePath)) {
    console.error(`Error: Template not found: ${templatePath}`);
    process.exit(1);
}

const WIDTH = config.resolution[0];
const HEIGHT = config.resolution[1];
const FPS = config.fps;
const DURATION = config.duration;
const TOTAL_FRAMES = Math.ceil(DURATION * FPS);
const OUTPUT_PATH = resolve(config.output_path);

let html = readFileSync(templatePath, "utf-8");
html = html.replaceAll("__WIDTH__", String(WIDTH));
html = html.replaceAll("__HEIGHT__", String(HEIGHT));
html = html.replace("__CONFIG_JSON__", JSON.stringify(config));

const sceneName = basename(OUTPUT_PATH, ".mp4");
const framesDir = join(dirname(OUTPUT_PATH), `_frames_${sceneName}`);
if (existsSync(framesDir)) {
    rmSync(framesDir, { recursive: true });
}
mkdirSync(framesDir, { recursive: true });

const tempHtmlPath = resolve(__dirname, `_temp_${basename(templatePath)}`);
writeFileSync(tempHtmlPath, html);

async function render() {
    console.log(`[render] ${templateName} → ${TOTAL_FRAMES} frames @ ${WIDTH}x${HEIGHT} ${FPS}fps`);
    console.log(`[render] Output: ${OUTPUT_PATH}`);

    // Spin up a tiny static HTTP server rooted at the repo so the page
    // (and its absolute /projects/... image refs) are same-origin. We
    // pick a random ephemeral port to avoid clashing with anything
    // (was: hardcoded http://localhost:49023/d3_scenes/, which required
    // the workspace's live_server to be running).
    const REPO_ROOT = resolve(__dirname, "..");
    const MIME = {
        ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml",
        ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".wav": "audio/wav",
        ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
    };
    const staticServer = createServer((req, res) => {
        try {
            const urlPath = decodeURIComponent(req.url.split("?")[0]);
            const fp = resolve(join(REPO_ROOT, urlPath));
            if (!fp.startsWith(REPO_ROOT) || !existsSync(fp) || statSync(fp).isDirectory()) {
                res.writeHead(404); res.end(); return;
            }
            res.writeHead(200, { "Content-Type": MIME[extname(fp).toLowerCase()] || "application/octet-stream" });
            createReadStream(fp).pipe(res);
        } catch (e) {
            res.writeHead(500); res.end(String(e));
        }
    });
    await new Promise(r => staticServer.listen(0, "127.0.0.1", r));
    const PORT = staticServer.address().port;

    const browser = await puppeteer.launch({
        headless: true,
        ...(process.env.PUPPETEER_EXECUTABLE_PATH
            ? { executablePath: process.env.PUPPETEER_EXECUTABLE_PATH }
            : process.platform === "darwin"
                ? { executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" }
                : {}),
        args: [
            "--autoplay-policy=no-user-gesture-required",
            "--disable-web-security",
            `--window-size=${WIDTH},${HEIGHT}`,
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--allow-file-access-from-files",
        ],
    });

    const page = await browser.newPage();
    await page.setViewport({ width: WIDTH, height: HEIGHT, deviceScaleFactor: 1 });
    page.on("console", msg => console.log("[PAGE] " + msg.text()));
    page.on("pageerror", err => console.log("[PAGE ERROR] " + err.toString()));


    page.on("requestfailed", req => console.log("[FAILED URL] " + req.url() + " : " + (req.failure() ? req.failure().errorText : "none")));
    await page.goto(`http://127.0.0.1:${PORT}/d3_scenes/${basename(tempHtmlPath)}`, { waitUntil: "networkidle0", timeout: 30000 });

    await page.waitForFunction("window._rendererReady === true", { timeout: 30000 });

    const initError = await page.evaluate(() => window._rendererError);
    if (initError) {
        console.error(`[render] Init error: ${initError}`);
        await browser.close();
        process.exit(1);
    }

    console.log("[render] Ready, capturing frames...");

    const startTime = Date.now();
    const logInterval = Math.max(Math.floor(TOTAL_FRAMES / 20), 1);

    for (let frame = 0; frame < TOTAL_FRAMES; frame++) {
        await page.evaluate((f) => window._renderFrame(f), frame);

        const framePath = join(framesDir, `frame_${String(frame).padStart(5, "0")}.png`);
        await page.screenshot({ path: framePath, type: "png" });

        if (frame % logInterval === 0 || frame === TOTAL_FRAMES - 1) {
            const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
            const pct = ((frame / TOTAL_FRAMES) * 100).toFixed(0);
            console.log(`[render] Frame ${frame + 1}/${TOTAL_FRAMES} (${pct}%) — ${elapsed}s`);
        }
    }

    await browser.close();
    staticServer.close();
    if (existsSync(tempHtmlPath)) unlinkSync(tempHtmlPath);
    const captureTime = ((Date.now() - startTime) / 1000).toFixed(1);
    console.log(`[render] Frames captured in ${captureTime}s`);

    console.log("[render] Stitching with FFmpeg...");

    const ffmpegArgs = [
        "-y",
        "-framerate", String(FPS),
        "-i", join(framesDir, "frame_%05d.png"),
        "-c:v", "libx264",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-movflags", "+faststart",
        OUTPUT_PATH,
    ];

    try {
        execFileSync("ffmpeg", ffmpegArgs, { stdio: "pipe" });
        console.log(`[render] Done: ${OUTPUT_PATH}`);
        rmSync(framesDir, { recursive: true });
    } catch (err) {
        console.error(`[render] FFmpeg failed: ${err.stderr?.toString().slice(0, 500)}`);
        process.exit(1);
    }
}

render().catch((err) => {
    console.error(`[render] Fatal: ${err.message}`);
    process.exit(1);
});
