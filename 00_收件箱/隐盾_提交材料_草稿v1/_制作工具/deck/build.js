// 隐盾 · 方案 PPT 生成器（由需知的生成器改来）：读 PPT内容.json + 截图 / 配图，排成 16:9 暗色 deck（与隐盾界面同一套配色）。
// 用法：node build.js <content.json> <shots_dir> <out.pptx>
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const Md = require("react-icons/md");

const { applyTheme } = require("./apply_theme.js");   // 把主题色写进 ppt/theme（pptxgenjs 只写字体）

const [, , CONTENT, SHOTS, OUT] = process.argv;
const ASSETS = path.join(__dirname, "assets");
const content = JSON.parse(fs.readFileSync(CONTENT, "utf8"));

const FONT = "Microsoft YaHei";
const C = {
  bg: "070B16", panel: "111A2E", panel2: "0E1626", card: "131D33", line: "22304F", line2: "2C3D63",
  text: "E6ECF8", soft: "C7D2E8", mute: "8A97B4", dim: "5B6785",
  cyan: "22D3EE", blue: "4361EE", violet: "A78BFA", pink: "F472B6", amber: "FBBF24", green: "34D399", teal: "2DD4BF", sky: "60A5FA", red: "F87171", orange: "FB923C",
};
const ACCENTS = [C.green, C.cyan, C.violet, C.amber, C.pink, C.sky, C.teal, C.orange];
// 关键词配色：卡片 / 流程标题里出现这些词就用对应颜色
const SKILL_COLOR = { 网页: C.green, 剪贴板: C.amber, 命令行: C.violet, 嵌入: C.violet, 脱敏: C.green, 还原: C.cyan, 多轮: C.violet, 本机: C.green, 不联网: C.cyan,
  映射表: C.violet, 日志: C.pink, 隐盾: C.green, 个人: C.green, 部门: C.cyan, 平台: C.violet };
const THEME = {
  name: "YinDun Night",
  colors: { dk1: "0B1222", lt1: "FFFFFF", dk2: "111A2E", lt2: "E6ECF8", accent1: C.green, accent2: C.cyan, accent3: C.violet,
            accent4: C.amber, accent5: C.pink, accent6: C.sky, hlink: C.green, folHlink: C.cyan },
};

const W = 13.333, H = 7.5, MX = 0.62;
const TITLE_Y = 0.42, BODY_Y = 1.5, BODY_B = 6.85;

// ---------- 文本度量（中文按 1em，西文 0.55em）与自适应字号 ----------
function emLen(s) {
  let n = 0;
  for (const ch of String(s || "")) n += /[\u0000-ÿ]/.test(ch) ? 0.55 : 1;
  return n;
}
function linesFor(text, wIn, fs) {
  const cpl = Math.max(1, Math.floor((wIn * 72) / fs) - 0.6);
  return String(text || "").split("\n").reduce((acc, p) => acc + Math.max(1, Math.ceil(emLen(p) / cpl)), 0);
}
function fitFont(text, wIn, hIn, { max = 16, min = 11, lh = 1.32, pad = 0.1 } = {}) {
  for (let fs = max; fs >= min; fs -= 0.5) {
    if ((linesFor(text, wIn - pad, fs) * fs * lh) / 72 <= hIn - pad) return fs;
  }
  return min;
}
function fitList(items, wIn, hIn, { max = 16, min = 11, lh = 1.3, gap = 0.55 } = {}) {
  for (let fs = max; fs >= min; fs -= 0.5) {
    const lines = items.reduce((a, t) => a + linesFor(t, wIn - 0.42, fs), 0);
    const hNeed = (lines * fs * lh) / 72 + (items.length - 1) * (fs * gap) / 72 + 0.12;
    if (hNeed <= hIn) return fs;
  }
  return min;
}

// ---------- 图标（react-icons → PNG） ----------
const iconCache = {};
async function icon(name, color = "FFFFFF", size = 256) {
  const key = `${name}-${color}`;
  if (iconCache[key]) return iconCache[key];
  const Comp = Md[name] || Md.MdAutoAwesome;
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color: "#" + color, size: String(size) }));
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  iconCache[key] = "image/png;base64," + buf.toString("base64");
  return iconCache[key];
}
const ICON_RULES = [
  [/成效|口径|省时/, "MdTimer"], [/落盘|权限/, "MdLock"], [/落地|复用/, "MdContentCopy"],
  [/剪贴板/, "MdContentPaste"], [/命令行|嵌入|代码/, "MdTerminal"], [/网页/, "MdWeb"], [/还原/, "MdLockOpen"], [/多轮|会话/, "MdForum"],
  [/映射表/, "MdSwapHoriz"], [/不联网|联网/, "MdWifiOff"], [/本机|内存|不落盘/, "MdMemory"], [/密钥|凭据|口令/, "MdVpnKey"],
  [/日志|留痕/, "MdBlock"], [/疑似|拿不准|提示/, "MdHelpOutline"], [/对照|看得见/, "MdVisibility"], [/持仓|报表|边界/, "MdRule"],
  [/个人|自用/, "MdPerson"], [/部门|名单/, "MdGroups"], [/平台|公司/, "MdDomain"], [/开源|可审|代码/, "MdCode"],
  [/问清|追问|澄清|提问/, "MdQuestionAnswer"], [/写单|需求单|文档|PRD/, "MdDescription"], [/估量|工时|估算|人天|小时/, "MdSpeed"],
  [/定架|架构|数据地图|决策/, "MdAccountTree"], [/出样|原型|页面/, "MdWeb"], [/对账|一本账|台账|看板/, "MdMenuBook"],
  [/隐盾|脱敏|安全|合规|审计|沙箱/, "MdShield"], [/记忆|🧠/, "MdPsychology"], [/纪要|拆分|直通车/, "MdCallSplit"],
  [/版本|变更|对比|diff/i, "MdCompareArrows"], [/Jira|工单/, "MdFactCheck"], [/企微|企业微信|微信/, "MdChat"],
  [/REST|API|接口/, "MdApi"], [/MCP|智能体|IDE/, "MdHub"], [/模型|AI|大模型/, "MdAutoAwesome"], [/测试|评测|成绩/, "MdVerified"],
  [/部署|启动|一键|安装/, "MdRocketLaunch"], [/复制|推广|子公司|分公司/, "MdContentCopy"], [/期货|结算|夜盘|主力|限仓|保证金/, "MdShowChart"],
  [/业务|部门|同事|角色/, "MdGroups"], [/时间|节省|效率|提效/, "MdTimer"], [/风险|偏差/, "MdWarningAmber"], [/知识|规则|目录|历史/, "MdLibraryBooks"],
];
function pickIcon(text, title) {
  // 先按标题选，标题认不出再看正文：正文里顺带提到的词（「剪贴板模式」）不该决定图标
  for (const t of [title, text]) {
    if (!t) continue;
    for (const [re, name] of ICON_RULES) if (re.test(t)) return name;
  }
  return "MdAutoAwesome";
}
function pickColor(text, i) {
  for (const [k, v] of Object.entries(SKILL_COLOR)) if ((text || "").includes(k)) return v;
  return ACCENTS[i % ACCENTS.length];
}

// ---------- 基础形状 ----------
function box(slide, x, y, w, h, { fill = C.card, line = C.line, lw = 1, r = 0.14, transparency = 0, name } = {}) {
  slide.addShape("roundRect", { x, y, w, h, rectRadius: r, fill: { color: fill, transparency }, line: { color: line, width: lw }, objectName: name });
}
function text(slide, t, x, y, w, h, opts = {}) {
  slide.addText(t, { x, y, w, h, fontFace: FONT, color: C.text, fontSize: 14, margin: 0.04, valign: "top", isTextBox: true, ...opts });
}
async function iconDot(slide, name, x, y, d, color) {
  slide.addShape("ellipse", { x, y, w: d, h: d, fill: { color }, line: { color, width: 0 } });
  const pad = d * 0.22;
  slide.addImage({ data: await icon(name, "0B1222"), x: x + pad, y: y + pad, w: d - 2 * pad, h: d - 2 * pad });
}
function numDot(slide, n, x, y, d, color) {
  slide.addShape("ellipse", { x, y, w: d, h: d, fill: { color }, line: { color, width: 0 } });
  text(slide, String(n), x, y, d, d, { fontSize: d * 32, bold: true, color: "0B1222", align: "center", valign: "middle", margin: 0 });
}

// ---------- 截图 ----------
function shotPath(name) {
  if (!name) return null;
  for (const dir of [SHOTS, path.join(__dirname, "figs")]) {   // 截图在 shots/，示意图在 deck/figs/
    for (const ext of [".png", ".jpg", ".jpeg"]) {
      const p = path.join(dir, name.replace(/\.(png|jpe?g)$/i, "") + ext);
      if (fs.existsSync(p)) return p;
    }
  }
  return null;
}
const PREP = path.join(__dirname, "out", "_prep");
async function prepImage(p, maxAR) {
  const meta = await sharp(p).metadata();
  if (!maxAR || meta.height / meta.width <= maxAR * 1.02) return p;
  fs.mkdirSync(PREP, { recursive: true });
  const ch = Math.round(meta.width * maxAR);
  const out = path.join(PREP, path.basename(p).replace(/\.(png|jpe?g)$/i, `_${Math.round(maxAR * 100)}.png`));
  const fadeH = Math.round(ch * 0.12);
  const svg = Buffer.from(`<svg width="${meta.width}" height="${ch}"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="#0E1626" stop-opacity="0"/><stop offset="1" stop-color="#0E1626" stop-opacity="1"/></linearGradient></defs>` +
    `<rect x="0" y="${ch - fadeH}" width="${meta.width}" height="${fadeH}" fill="url(#g)"/></svg>`);
  await sharp(p).extract({ left: 0, top: 0, width: meta.width, height: ch }).composite([{ input: svg, top: 0, left: 0 }]).png().toFile(out);
  return out;
}

async function imageFit(slide, file, x, y, w, h, { frame = true, maxAR = null } = {}) {
  let p = shotPath(file);
  if (p && maxAR) p = await prepImage(p, maxAR);
  if (!p) {
    box(slide, x, y, w, h, { fill: C.panel2, line: C.line2 });
    text(slide, `截图：${file}`, x, y + h / 2 - 0.2, w, 0.4, { align: "center", color: C.mute, fontSize: 12 });
    return { x, y, w, h };
  }
  const meta = await sharp(p).metadata();
  const ar = meta.width / meta.height;
  let iw = w, ih = w / ar;
  if (ih > h) { ih = h; iw = h * ar; }
  const ix = x + (w - iw) / 2, iy = y + (h - ih) / 2;
  if (frame) {
    slide.addShape("roundRect", { x: ix - 0.06, y: iy - 0.06, w: iw + 0.12, h: ih + 0.12, rectRadius: 0.12,
      fill: { color: C.panel2 }, line: { color: C.line2, width: 1 },
      shadow: { type: "outer", color: "000000", opacity: 0.45, blur: 14, offset: 4, angle: 90 } });
  }
  slide.addImage({ path: p, x: ix, y: iy, w: iw, h: ih, altText: file });
  return { x: ix, y: iy, w: iw, h: ih };
}

// ---------- 块渲染 ----------
async function renderBullets(slide, items, x, y, w, h, { max = 17, min = 12, color = C.soft } = {}) {
  const fs = fitList(items, w, h, { max, min });
  const runs = items.map((t, i) => ({ text: t, options: { bullet: { code: "25CF", indent: 16 }, color, breakLine: i < items.length - 1, paraSpaceAfter: fs * 0.55 } }));
  text(slide, runs, x, y, w, h, { fontSize: fs, lineSpacingMultiple: 1.12, valign: "top" });
}

async function renderCards(slide, items, x, y, w, h) {
  const n = items.length;
  const cols = n <= 3 ? n : n === 4 ? 2 : 3;
  const rows = Math.ceil(n / cols);
  const gap = 0.28;
  const cw = (w - gap * (cols - 1)) / cols, ch = (h - gap * (rows - 1)) / rows;
  const titleFs = Math.min(17, ...items.map(it => fitFont(it.title, cw - 1.1, 0.42, { max: 17, min: 12, pad: 0 })));
  const bodyH = ch - 0.98;
  const bodyFs = Math.min(14.5, ...items.map(it => fitFont(it.body || "", cw - 0.44, bodyH, { max: 14.5, min: 11 })));
  for (let i = 0; i < n; i++) {
    const it = items[i];
    const cx = x + (i % cols) * (cw + gap), cy = y + Math.floor(i / cols) * (ch + gap);
    const col = pickColor(`${it.tag || ""}${it.title}`, i);
    box(slide, cx, cy, cw, ch, { fill: C.card, line: C.line });
    await iconDot(slide, pickIcon(`${it.tag || ""}${it.title}${it.body || ""}`, it.title), cx + 0.22, cy + 0.22, 0.52, col);
    text(slide, it.title, cx + 0.88, cy + 0.2, cw - 1.06, 0.56, { fontSize: titleFs, bold: true, valign: "middle" });
    if (it.tag) {
      text(slide, it.tag, cx + cw - 1.5, cy + 0.06, 1.38, 0.24, { fontSize: 9.5, color: col, align: "right" });
    }
    if (it.body) text(slide, it.body, cx + 0.22, cy + 0.88, cw - 0.44, bodyH, { fontSize: bodyFs, color: C.soft, lineSpacingMultiple: 1.1 });
  }
}

async function renderStats(slide, items, x, y, w, h) {
  const n = items.length, gap = 0.3;
  const cw = (w - gap * (n - 1)) / n;
  const vFs = Math.min(46, ...items.map(it => fitFont(it.value, cw - 0.4, 0.95, { max: 46, min: 24, pad: 0 })));
  for (let i = 0; i < n; i++) {
    const it = items[i], cx = x + i * (cw + gap), col = ACCENTS[i % ACCENTS.length];
    box(slide, cx, y, cw, h, { fill: C.card, line: C.line });
    text(slide, it.value, cx + 0.22, y + 0.2, cw - 0.4, 0.95, { fontSize: vFs, bold: true, color: col, valign: "middle", fontFace: FONT });
    text(slide, it.label, cx + 0.22, y + 1.18, cw - 0.4, 0.42, { fontSize: fitFont(it.label, cw - 0.4, 0.42, { max: 16, min: 11 }), bold: true });
    if (it.note) text(slide, it.note, cx + 0.22, y + 1.6, cw - 0.4, h - 1.7, { fontSize: fitFont(it.note, cw - 0.4, h - 1.7, { max: 12.5, min: 10 }), color: C.mute });
  }
}

function renderTable(slide, header, rows, x, y, w, h) {
  const nCol = header.length;
  const lens = header.map((hd, j) => Math.max(emLen(hd), ...rows.map(r => emLen(r[j] || ""))) + 1.2);
  const tot = lens.reduce((a, b) => a + b, 0);
  const colW = lens.map(l => Math.max(0.9, (w * l) / tot));
  const scale = w / colW.reduce((a, b) => a + b, 0);
  for (let j = 0; j < nCol; j++) colW[j] *= scale;
  const rowH = Math.min(0.62, (h - 0.1) / (rows.length + 1));
  let fs = 13;
  for (; fs > 10; fs -= 0.5) {
    const ok = rows.every(r => r.every((c, j) => linesFor(c, colW[j] - 0.16, fs) <= 2)) && (rows.length + 1) * Math.max(rowH, (fs * 2.6) / 72) <= h;
    if (ok) break;
  }
  const head = header.map(hd => ({ text: hd, options: { bold: true, color: C.text, fill: { color: "16233F" }, fontSize: fs } }));
  const body = rows.map((r, i) => r.map((c, j) => ({ text: String(c ?? ""), options: { color: j === 0 ? C.text : C.soft, bold: j === 0,
    fill: { color: i % 2 ? "0F172A" : "111A2E" }, fontSize: fs } })));
  slide.addTable([head, ...body], { x, y, w, colW, fontFace: FONT, fontSize: fs, color: C.soft, valign: "middle",
    border: { type: "solid", pt: 0.75, color: C.line }, rowH: Math.max(0.36, Math.min(rowH, 0.62)), margin: [0.05, 0.08, 0.05, 0.08], autoPage: false });
}

async function renderFlow(slide, steps, x, y, w, h) {
  const n = steps.length, gap = 0.22;
  const cw = (w - gap * (n - 1)) / n;
  const maxLines = Math.max(1, ...steps.map(st => linesFor(st.desc || "", cw - 0.3, 13.5)));
  h = Math.min(h, Math.max(2.15, 1.62 + (maxLines * 13.5 * 1.3) / 72 + 0.25));
  const descFs = Math.min(13.5, ...steps.map(s => fitFont(s.desc || "", cw - 0.3, h - 1.45, { max: 13.5, min: 10.5 })));
  for (let i = 0; i < n; i++) {
    const s = steps[i], cx = x + i * (cw + gap), col = pickColor(s.name, i);
    box(slide, cx, y, cw, h, { fill: C.card, line: C.line });
    await iconDot(slide, pickIcon(s.name), cx + (cw - 0.66) / 2, y + 0.24, 0.66, col);
    text(slide, s.name, cx + 0.1, y + 1.0, cw - 0.2, 0.42, { fontSize: fitFont(s.name, cw - 0.2, 0.42, { max: 18, min: 12, pad: 0 }), bold: true, color: col, align: "center" });
    if (s.desc) text(slide, s.desc, cx + 0.15, y + 1.45, cw - 0.3, h - 1.55, { fontSize: descFs, color: C.soft, align: "center", lineSpacingMultiple: 1.1 });
    if (i < n - 1) {
      slide.addShape("rightArrow", { x: cx + cw + 0.02, y: y + 0.45, w: gap - 0.04, h: 0.22, fill: { color: C.line2 }, line: { color: C.line2, width: 0 } });
    }
  }
}

function renderChat(slide, b, x, y, w, h) {
  // 面板高度随气泡内容收拢，不留大块空白
  const bw = w - 1.3, maxH = h - 0.95;
  const fs = fitFont(b.text, bw - 0.36, maxH - 0.28, { max: 17, min: 11.5 });
  const bh = Math.min(maxH, (linesFor(b.text, bw - 0.36, fs) * fs * 1.42) / 72 + 0.45);
  const ph = Math.min(h, bh + 0.95);
  box(slide, x, y, w, ph, { fill: C.panel2, line: C.line2, r: 0.2 });
  text(slide, b.speaker || "企业微信", x + 0.3, y + 0.16, w - 0.6, 0.36, { fontSize: 13, color: C.mute });
  slide.addShape("ellipse", { x: x + 0.3, y: y + 0.66, w: 0.5, h: 0.5, fill: { color: C.pink }, line: { color: C.pink, width: 0 } });
  text(slide, (b.speaker || "业").replace(/.*·\s*/, "").slice(-1), x + 0.3, y + 0.66, 0.5, 0.5, { fontSize: 16, bold: true, color: "FFFFFF", align: "center", valign: "middle", margin: 0 });
  box(slide, x + 0.95, y + 0.66, bw, bh, { fill: "1B2740", line: "2A3A5E", r: 0.16 });
  text(slide, b.text, x + 1.13, y + 0.8, bw - 0.36, bh - 0.22, { fontSize: fs, color: C.text, lineSpacingMultiple: 1.18 });
  return ph;
}

function renderQuote(slide, b, x, y, w, h) {
  box(slide, x, y, w, h, { fill: C.card, line: C.line2 });
  text(slide, "“", x + 0.2, y - 0.05, 0.8, 0.9, { fontSize: 60, color: C.cyan, bold: true });
  text(slide, b.text, x + 0.9, y + 0.25, w - 1.2, h - (b.by ? 0.85 : 0.45), { fontSize: fitFont(b.text, w - 1.2, h - 0.85, { max: 22, min: 13 }), color: C.text, bold: true, lineSpacingMultiple: 1.15 });
  if (b.by) text(slide, "—— " + b.by, x + 0.9, y + h - 0.55, w - 1.2, 0.36, { fontSize: 12.5, color: C.mute, align: "right" });
}

function renderTimeline(slide, items, x, y, w, h) {
  const n = items.length, ly = y + 0.62;
  slide.addShape("line", { x, y: ly, w, h: 0, line: { color: C.line2, width: 2 } });
  const cw = w / n;
  for (let i = 0; i < n; i++) {
    const it = items[i], cx = x + i * cw, col = ACCENTS[i % ACCENTS.length];
    slide.addShape("ellipse", { x: cx + 0.12, y: ly - 0.13, w: 0.26, h: 0.26, fill: { color: col }, line: { color: C.bg, width: 2 } });
    text(slide, it.when, cx + 0.06, y, cw - 0.18, 0.4, { fontSize: 14, bold: true, color: col });
    const need = (linesFor(it.what, cw - 0.52, 14) * 14 * 1.3) / 72 + 0.4;
    const bh = Math.min(h - 1.0, Math.max(1.1, need + 0.2));
    box(slide, cx + 0.04, ly + 0.32, cw - 0.2, bh, { fill: C.card, line: C.line });
    text(slide, it.what, cx + 0.2, ly + 0.44, cw - 0.52, bh - 0.22, { fontSize: fitFont(it.what, cw - 0.52, bh - 0.22, { max: 14, min: 10.5 }), color: C.soft, lineSpacingMultiple: 1.1 });
  }
}

async function renderLayers(slide, items, x, y, w, h) {
  const n = items.length, gap = 0.16;
  const lh = (h - gap * (n - 1)) / n;
  for (let i = 0; i < n; i++) {
    const it = items[i], ly = y + i * (lh + gap), col = ACCENTS[i % ACCENTS.length];
    box(slide, x, ly, w, lh, { fill: C.card, line: C.line });
    text(slide, it.name, x + 0.2, ly, 1.9, lh, { fontSize: fitFont(it.name, 1.9, lh, { max: 16, min: 11, pad: 0 }), bold: true, color: col, valign: "middle" });
    const parts = it.parts || [];
    const px0 = x + 2.2, pw = w - 2.4;
    const chipGap = 0.14;
    const chipW = (pw - chipGap * (parts.length - 1)) / Math.max(1, parts.length);
    const fs = Math.min(13.5, ...parts.map(p => fitFont(p, chipW - 0.16, lh - 0.24, { max: 13.5, min: 9.5, pad: 0.04 })));
    parts.forEach((p, j) => {
      const cx = px0 + j * (chipW + chipGap);
      slide.addShape("roundRect", { x: cx, y: ly + 0.12, w: chipW, h: lh - 0.24, rectRadius: 0.1, fill: { color: col, transparency: 86 }, line: { color: col, width: 0.75, transparency: 40 } });
      text(slide, p, cx + 0.06, ly + 0.12, chipW - 0.12, lh - 0.24, { fontSize: fs, color: C.text, align: "center", valign: "middle", margin: 0.02 });
    });
  }
}

async function renderCallouts(slide, items, x, y, w, h) {
  const n = items.length, gap = 0.16;
  const per = (h - gap * (n - 1)) / n;
  const fs = Math.min(14.5, ...items.map(t => fitFont(t, w - 0.62, Math.min(per, 1.2), { max: 14.5, min: 10.5 })));
  let cy = y;
  for (let i = 0; i < n; i++) {
    const need = Math.max(0.5, (linesFor(items[i], w - 0.62, fs) * fs * 1.3) / 72 + 0.12);
    numDot(slide, i + 1, x, cy + 0.04, 0.36, C.amber);
    text(slide, items[i], x + 0.52, cy, w - 0.56, need, { fontSize: fs, color: C.soft, lineSpacingMultiple: 1.08 });
    cy += need + gap;
  }
}

// ---------- 版式 ----------
function titleOf(slide, s) {
  slide.addText(s.title || "", { placeholder: "title" });
  if (s.subtitle) text(slide, s.subtitle, MX, 1.08, W - 2 * MX, 0.4, { fontSize: 14.5, color: C.mute });
}

async function renderCardsCompact(slide, items, x, y, w, h) {
  const n = items.length, gap = 0.24, cw = (w - gap * (n - 1)) / n;
  const bodyFs = Math.min(13, ...items.map(it => fitFont(it.body || "", cw - 1.0, h - 0.5, { max: 13, min: 10.5 })));
  for (let i = 0; i < n; i++) {
    const it = items[i], cx = x + i * (cw + gap), col = pickColor(`${it.tag || ""}${it.title}`, i);
    box(slide, cx, y, cw, h, { fill: C.card, line: C.line });
    await iconDot(slide, pickIcon(`${it.tag || ""}${it.title}${it.body || ""}`, it.title), cx + 0.18, y + (h - 0.48) / 2, 0.48, col);
    text(slide, it.title, cx + 0.8, y + 0.1, cw - 0.95, 0.38, { fontSize: fitFont(it.title, cw - 0.95, 0.38, { max: 15, min: 11, pad: 0 }), bold: true, color: col, valign: "middle" });
    if (it.body) text(slide, it.body, cx + 0.8, y + 0.46, cw - 0.95, h - 0.52, { fontSize: bodyFs, color: C.soft });
  }
}

async function renderImageColumns(slide, imgs, x, y, w, h) {
  if (imgs.length === 3) {
    const gap = 0.34, lw = w * 0.6, rw = w - lw - gap, capL = 0.46, capR = 0.4;
    const big = imgs[0];
    const r = await imageFit(slide, big.file, x, y, lw, h - capL, { maxAR: Math.min(1.1, (h - capL) / lw * 1.05) });
    if (big.caption) text(slide, big.caption, x, y + h - capL + 0.06, lw, 0.36, { fontSize: 13.5, bold: true, color: C.text, align: "center" });
    const rh = (h - gap) / 2;
    for (let i = 1; i < 3; i++) {
      const im = imgs[i], ry = y + (i - 1) * (rh + gap);
      await imageFit(slide, im.file, x + lw + gap, ry, rw, rh - capR, { maxAR: Math.min(1.1, (rh - capR) / rw * 1.05) });
      if (im.caption) text(slide, im.caption, x + lw + gap, ry + rh - capR + 0.04, rw, 0.34, { fontSize: fitFont(im.caption, rw, 0.34, { max: 12.5, min: 10, pad: 0 }), bold: true, color: C.text, align: "center" });
    }
    return;
  }
  if (imgs.length === 2) {   // 横条图（高宽比 < 0.36）整宽放上面，另一张放下面
    const ars = [];
    for (const im of imgs) {
      const pth = shotPath(im.file);
      const m = pth ? await sharp(pth).metadata() : { width: 16, height: 9 };
      ars.push(m.height / m.width);
    }
    const stripIdx = ars.findIndex(a => a < 0.36);
    if (stripIdx >= 0 && ars[1 - stripIdx] > 0.7) {   // 另一张是竖长图：竖图在左，横条与要点在右
      const strip = imgs[stripIdx], other = imgs[1 - stripIdx];
      const lw = w * 0.5, rx = x + lw + 0.4, rw = w - lw - 0.4, capO = other.caption ? 0.46 : 0;
      const ro = await imageFit(slide, other.file, x, y, lw, h - capO, { maxAR: Math.min(1.35, (h - capO) / lw * 1.02) });
      if (other.caption) text(slide, other.caption, x, ro.y + ro.h + 0.08, lw, 0.34, { fontSize: 13, bold: true, color: C.text, align: "center" });
      const sh = Math.min(h * 0.42, rw * ars[stripIdx] + 0.1);
      const rs = await imageFit(slide, strip.file, rx, y, rw, sh);
      let cy = rs.y + rs.h + 0.1;
      if (strip.caption) { text(slide, strip.caption, rx, cy, rw, 0.34, { fontSize: 13, bold: true, color: C.text, align: "center" }); cy += 0.42; }
      const calls = [...(other.callouts || []), ...(strip.callouts || [])];
      if (calls.length) await renderCallouts(slide, calls, rx, cy + 0.12, rw, y + h - cy - 0.12);
      return;
    }
    if (stripIdx >= 0) {
      const strip = imgs[stripIdx], other = imgs[1 - stripIdx];
      const sh = Math.min(1.75, w * ars[stripIdx] + 0.1), capS = strip.caption ? 0.36 : 0;
      const rs = await imageFit(slide, strip.file, x, y, w, sh);
      if (strip.caption) text(slide, strip.caption + ((strip.callouts || []).length ? "　·　" + strip.callouts.join("　·　") : ""), x, rs.y + rs.h + 0.06, w, 0.32,
        { fontSize: 12, color: C.mute, align: "center" });
      const oy = y + sh + capS + 0.16, capO = 0.46 + ((other.callouts || []).length ? 0.3 : 0);
      const ow = w, oh = h - (oy - y) - capO;
      const ro = await imageFit(slide, other.file, x, oy, ow, oh, { maxAR: Math.min(1.2, oh / ow * 1.05) });
      if (other.caption) text(slide, other.caption, x, ro.y + ro.h + 0.08, w, 0.34, { fontSize: 13, bold: true, color: C.text, align: "center" });
      if ((other.callouts || []).length) text(slide, other.callouts.join("　·　"), x, ro.y + ro.h + 0.42, w, 0.3, { fontSize: 11.5, color: C.mute, align: "center" });
      return;
    }
  }
  const n = imgs.length, gap = n >= 3 ? 0.26 : 0.36;
  const cw = (w - gap * (n - 1)) / n;
  const hasCall = imgs.some(im => (im.callouts || []).length);
  const capH = hasCall ? 0.92 : 0.46;
  const ih = h - capH;
  for (let i = 0; i < n; i++) {
    const im = imgs[i], ix = x + i * (cw + gap);
    const r = await imageFit(slide, im.file, ix, y, cw, ih, { maxAR: Math.min(1.25, (ih / cw) * 1.08) });
    const ty = y + ih + 0.1;
    if (im.caption) text(slide, im.caption, ix, ty, cw, 0.36, { fontSize: fitFont(im.caption, cw, 0.36, { max: 13.5, min: 10.5, pad: 0 }), bold: true, color: C.text, align: "center" });
    const calls = im.callouts || [];
    if (calls.length) {
      const t = calls.join("　·　");
      text(slide, t, ix, ty + 0.38, cw, capH - 0.48, { fontSize: fitFont(t, cw, capH - 0.48, { max: 12, min: 9.5 }), color: C.mute, align: "center" });
    }
  }
}

async function renderBlocksAuto(slide, blocks, x, y, w, h) {
  // 通用：按块数与类型分配区域。多图并排；单图/对话放左、文字放右；否则自上而下。
  const imgs = blocks.filter(b => b.type === "image");
  const chats = blocks.filter(b => b.type === "chat");
  const rest = blocks.filter(b => b.type !== "image" && b.type !== "chat");
  if (imgs.length >= 2) {
    let top = y, bottom = y + h;
    const cards = rest.find(b => b.type === "cards");
    if (cards) { await renderCardsCompact(slide, cards.items, x, top, w, 0.95); top += 1.18; }
    const bl = rest.find(b => b.type === "bullets");
    if (bl) {
      const t = bl.items.join("　·　");
      bottom -= 0.48;
      box(slide, x, bottom + 0.08, w, 0.42, { fill: C.panel2, line: C.line, r: 0.08 });
      text(slide, t, x + 0.2, bottom + 0.08, w - 0.4, 0.42, { fontSize: fitFont(t, w - 0.4, 0.42, { max: 13.5, min: 10, pad: 0 }), color: C.soft, valign: "middle" });
    }
    await renderImageColumns(slide, imgs, x, top, w, bottom - top - 0.06);
    return;
  }
  const tbl = rest.find(b => b.type === "table");
  if (imgs.length === 1 && tbl) {
    const lw = w * 0.56, rx = x + lw + 0.4, rw = w - lw - 0.4;
    const bl = rest.find(b => b.type === "bullets");
    const tH = bl ? h - 1.15 : h;
    renderTable(slide, tbl.header || [], tbl.rows || [], x, y, lw, tH);
    if (bl) await renderBullets(slide, bl.items, x, y + tH + 0.2, lw, 0.95, { max: 13.5, min: 10.5 });
    const capH = imgs[0].caption ? 0.42 : 0;
    const r = await imageFit(slide, imgs[0].file, rx, y, rw, h - capH, { maxAR: Math.min(1.3, (h - capH) / rw) });
    if (imgs[0].caption) text(slide, imgs[0].caption, rx, r.y + r.h + 0.1, rw, 0.34, { fontSize: 12, color: C.mute, align: "center" });
    return;
  }
  if (imgs.length === 2 && rest.length <= 1) {
    const gap = 0.36, iw = (w - gap) / 2, capH = 0.5, restH = rest.length ? 1.1 : 0;
    for (let i = 0; i < 2; i++) {
      const ix = x + i * (iw + gap);
      await imageFit(slide, imgs[i].file, ix, y, iw, h - capH - restH - 0.1);
      if (imgs[i].caption) text(slide, imgs[i].caption, ix, y + h - capH - restH, iw, capH, { fontSize: 13, color: C.soft, align: "center" });
    }
    if (rest.length) await renderBlock(slide, rest[0], x, y + h - restH + 0.1, w, restH - 0.1);
    return;
  }
  if (imgs.length === 1 || chats.length === 1) {
    const left = imgs[0] || chats[0];
    const leftW = imgs.length ? w * 0.64 : w * 0.46;
    const others = imgs.length ? rest.concat(chats) : rest.concat(imgs);
    const callouts = left.callouts || [];
    if (left.type === "image") {
      const capH = left.caption ? 0.42 : 0;
      const r = await imageFit(slide, left.file, x, y, leftW, h - capH);
      if (left.caption) text(slide, left.caption, x, r.y + r.h + 0.1, leftW, 0.34, { fontSize: 12, color: C.mute, align: "center" });
    } else {
      renderChat(slide, left, x, y, leftW, h);
    }
    const rx = x + leftW + 0.4, rw = w - leftW - 0.4;
    const parts = [];
    if (callouts.length) parts.push({ type: "callouts", items: callouts });
    parts.push(...others);
    if (!parts.length) return;
    const weights = parts.map(b => b.type === "cards" ? Math.max(2, b.items.length) : b.type === "table" ? 3 : b.type === "callouts" ? Math.max(2, b.items.length) : 2);
    const tot = weights.reduce((a, b) => a + b, 0);
    let cy = y;
    for (let i = 0; i < parts.length; i++) {
      const bh = (h - 0.25 * (parts.length - 1)) * weights[i] / tot;
      if (parts[i].type === "callouts") await renderCallouts(slide, parts[i].items, rx, cy, rw, bh);
      else if (parts[i].type === "cards") await renderCardsColumn(slide, parts[i].items, rx, cy, rw, bh);
      else await renderBlock(slide, parts[i], rx, cy, rw, bh);
      cy += bh + 0.25;
    }
    return;
  }
  const weights = rest.map(b => b.weight ? b.weight : b.type === "bullets" ? 0.55 + 0.32 * (b.items || []).length : b.type === "stats" ? 2.0 : b.type === "quote" ? 1.3
    : b.type === "flow" ? 1.35 : b.type === "timeline" ? 1.9 : b.type === "layers" ? 0.75 * (b.items || []).length
    : b.type === "table" ? 0.75 + 0.42 * (b.rows || []).length : 3);
  const tot = weights.reduce((a, b) => a + b, 0);
  let cy = y;
  for (let i = 0; i < rest.length; i++) {
    const bh = (h - 0.28 * (rest.length - 1)) * weights[i] / tot;
    await renderBlock(slide, rest[i], x, cy, w, bh);
    cy += bh + 0.28;
  }
}

async function renderCardsColumn(slide, items, x, y, w, h) {
  const n = items.length, gap = 0.18, ch = (h - gap * (n - 1)) / n;
  const bodyFs = Math.min(13.5, ...items.map(it => fitFont(it.body || "", w - 1.1, ch - 0.5, { max: 13.5, min: 10.5 })));
  for (let i = 0; i < n; i++) {
    const it = items[i], cy = y + i * (ch + gap), col = pickColor(`${it.tag || ""}${it.title}`, i);
    box(slide, x, cy, w, ch, { fill: C.card, line: C.line });
    await iconDot(slide, pickIcon(`${it.tag || ""}${it.title}${it.body || ""}`, it.title), x + 0.18, cy + Math.min(0.2, (ch - 0.46) / 2), 0.46, col);
    text(slide, it.title, x + 0.8, cy + 0.1, w - 0.95, 0.4, { fontSize: fitFont(it.title, w - 0.95, 0.4, { max: 15.5, min: 11, pad: 0 }), bold: true, valign: "middle" });
    if (it.body) text(slide, it.body, x + 0.8, cy + 0.48, w - 0.95, ch - 0.55, { fontSize: bodyFs, color: C.soft });
  }
}

async function renderBlock(slide, b, x, y, w, h) {
  switch (b.type) {
    case "bullets": return renderBullets(slide, b.items || [], x, y, w, h);
    case "cards": return renderCards(slide, b.items || [], x, y, w, h);
    case "stats": return renderStats(slide, b.items || [], x, y, w, Math.min(h, 2.6));
    case "table": return renderTable(slide, b.header || [], b.rows || [], x, y, w, h);
    case "flow": return renderFlow(slide, b.steps || [], x, y, w, h);
    case "chat": return renderChat(slide, b, x, y, w, h);
    case "quote": return renderQuote(slide, b, x, y, w, h);
    case "timeline": return renderTimeline(slide, b.items || [], x, y, w, h);
    case "layers": return renderLayers(slide, b.items || [], x, y, w, h);
    case "image": {
      const r = await imageFit(slide, b.file, x, y, w, h - (b.caption ? 0.42 : 0));
      if (b.caption) text(slide, b.caption, x, r.y + r.h + 0.1, w, 0.34, { fontSize: 12, color: C.mute, align: "center" });
      return;
    }
    default: return;
  }
}

async function coverSlide(pres, s) {
  const slide = pres.addSlide({ masterName: "XZ_COVER", sectionTitle: s.section || "封面" });
  const img = (s.blocks || []).find(b => b.type === "image");
  const tw = img ? 6.3 : W - 2 * MX;   // 有产品截图时文字占左半边
  slide.addShape("roundRect", { x: MX, y: 1.25, w: 1.05, h: 1.05, rectRadius: 0.24, fill: { color: C.green }, line: { color: "6EE7B7", width: 1.5 },
    shadow: { type: "outer", color: "34D399", opacity: 0.5, blur: 24, offset: 0, angle: 90 } });
  text(slide, "盾", MX, 1.25, 1.05, 1.05, { fontSize: 46, bold: true, color: "04121F", align: "center", valign: "middle", margin: 0 });
  text(slide, "隐盾", MX + 1.3, 1.12, 4, 0.95, { fontSize: 52, bold: true, color: C.text, charSpacing: 6, valign: "middle" });
  text(slide, "YinDun · 贴给大模型前先脱敏", MX + 1.34, 2.0, 5, 0.36, { fontSize: 14, color: C.mute, charSpacing: 2 });
  let headline = (s.title || "").includes("：") ? s.title.split("：").slice(1).join("：") : (s.title || "贴给大模型之前，先过一道门");
  if (!headline.includes("\n") && headline.includes("，")) headline = headline.replace("，", "，\n");
  text(slide, headline, MX, 2.75, tw, 1.3, { fontSize: fitFont(headline, tw, 1.3, { max: 32, min: 22 }), bold: true, color: "DCE6FA", lineSpacingMultiple: 1.1 });
  if (s.subtitle) text(slide, s.subtitle, MX, 4.1, tw, 0.7, { fontSize: fitFont(s.subtitle, tw, 0.7, { max: 16, min: 12 }), color: C.soft });
  const chips = s.chips || ["本机运行", "不联网", "多轮一致", "容错还原", "即插即用"];
  const chipW = img ? 1.12 : 1.4, chipGap = img ? 0.12 : 0.16;
  let cx = MX;
  for (const c of chips) {
    const cc = pickColor(c, chips.indexOf(c));
    slide.addShape("roundRect", { x: cx, y: 4.95, w: chipW, h: 0.42, rectRadius: 0.21, fill: { color: cc, transparency: 85 }, line: { color: cc, width: 1 } });
    text(slide, c, cx, 4.95, chipW, 0.42, { fontSize: 13, bold: true, color: cc, align: "center", valign: "middle", margin: 0 });
    cx += chipW + chipGap;
  }
  const lines = [];
  for (const b of s.blocks || []) {
    if (b.type === "bullets") lines.push(...b.items);
    if (b.type === "stats") lines.push(...b.items.map(i => `${i.label}：${i.value}`));
  }
  if (lines.length) text(slide, lines.join("　｜　"), MX, 5.7, tw, 0.6, { fontSize: fitFont(lines.join("　｜　"), tw, 0.6, { max: 14, min: 11 }), color: C.soft });
  if (img) await imageFit(slide, img.file, 7.25, 1.2, W - 7.25 - 0.45, 4.9);
  text(slide, "「智创未来」AI 创新应用大赛 · 个人赛 · 工具赛道　｜　演示数据均为虚构", MX, 6.78, W - 2 * MX, 0.34, { fontSize: 11, color: C.mute });
  if (s.notes) slide.addNotes(s.notes);
}

async function closingSlide(pres, s) {
  const slide = pres.addSlide({ masterName: "XZ_COVER", sectionTitle: s.section || "结尾" });
  text(slide, s.title || "贴给大模型之前，先过一道门", MX, 0.9, W - 2 * MX, 1.0, { fontSize: 46, bold: true, color: C.text, align: "center", valign: "middle", charSpacing: 4 });
  if (s.subtitle) text(slide, s.subtitle, MX, 1.95, W - 2 * MX, 0.5, { fontSize: 18, color: C.soft, align: "center" });
  const tl = (s.blocks || []).find(b => b.type === "timeline");
  const cards = (s.blocks || []).find(b => b.type === "cards");
  const bl = (s.blocks || []).find(b => b.type === "bullets");
  if (tl) { text(slide, "下一步", MX + 0.4, 2.75, 3, 0.36, { fontSize: 14, bold: true, color: C.green }); renderTimeline(slide, tl.items, MX + 0.4, 3.1, W - 2 * MX - 0.8, 1.75); }
  if (cards) await renderCardsCompact(slide, cards.items, MX + 0.4, 5.15, W - 2 * MX - 0.8, 1.05);
  if (bl && !cards) {
    const t = bl.items.join("　·　");
    text(slide, t, MX + 0.6, 5.2, W - 2 * MX - 1.2, 1.0, { fontSize: fitFont(t, W - 2 * MX - 1.2, 1.0, { max: 16, min: 11 }), color: C.mute, align: "center" });
  }
  text(slide, "隐盾 · YinDun", MX, 6.7, W - 2 * MX, 0.36, { fontSize: 12, color: C.dim, align: "center", charSpacing: 3 });
  if (s.notes) slide.addNotes(s.notes);
}

async function statementSlide(pres, s) {
  const slide = pres.addSlide({ masterName: "XZ_SECTION", sectionTitle: s.section || "正文" });
  text(slide, s.title || "", MX + 0.2, 1.1, W - 2 * MX - 0.4, 1.3, { fontSize: fitFont(s.title, W - 2 * MX - 0.4, 1.3, { max: 38, min: 26 }), bold: true, color: C.text, valign: "middle" });
  if (s.subtitle) text(slide, s.subtitle, MX + 0.2, 2.42, W - 2 * MX - 0.4, 0.6, { fontSize: 17, color: C.soft });
  await renderBlocksAuto(slide, s.blocks || [], MX + 0.2, 3.2, W - 2 * MX - 0.4, 3.55);
  if (s.notes) slide.addNotes(s.notes);
}

async function contentSlide(pres, s) {
  const slide = pres.addSlide({ masterName: "XZ_CONTENT", sectionTitle: s.section || "正文" });
  titleOf(slide, s);
  const y0 = s.subtitle ? 1.62 : BODY_Y;
  const chat = (s.blocks || []).find(b => b.type === "chat");
  const bl = (s.blocks || []).find(b => b.type === "bullets");
  if (s.layout === "figure") {   // 单张配图铺满正文区，可带一行说明
    const img = (s.blocks || []).find(b => b.type === "image");
    const capH = img && img.caption ? 0.42 : 0;
    const r = await imageFit(slide, img.file, MX, y0, W - 2 * MX, BODY_B - y0 - capH, { frame: false });
    if (img.caption) text(slide, img.caption, MX, r.y + r.h + 0.08, W - 2 * MX, 0.34, { fontSize: 12.5, color: C.mute, align: "center" });
    if (s.notes) slide.addNotes(s.notes);
    return;
  }
  if (s.layout === "pain" && chat && bl) {
    const lw = 5.4, ch = 3.3;
    renderChat(slide, chat, MX, y0 + 0.25, lw, ch);
    const items = bl.items.map(t => { const k = t.search(/[：:]/); return k > 0 ? { title: t.slice(0, k), body: t.slice(k + 1) } : { title: t, body: "" }; });
    await renderCardsColumn(slide, items, MX + lw + 0.45, y0 + 0.25, W - 2 * MX - lw - 0.45, Math.min(BODY_B - y0 - 0.4, 1.25 * items.length + 0.2));
    if (s.notes) slide.addNotes(s.notes);
    return;
  }
  await renderBlocksAuto(slide, s.blocks || [], MX, y0, W - 2 * MX, BODY_B - y0);
  if (s.notes) slide.addNotes(s.notes);
}

async function main() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.author = "隐盾";
  pres.company = "隐盾";
  pres.title = "隐盾 · 贴给大模型前先脱敏 —— 个人赛工具赛道方案";
  pres.subject = "「智创未来」首届 AI 创新应用大赛 · 个人赛 · 工具赛道";   // 不写就是 pptxgenjs 默认的「PptxGenJS Presentation」，PDF 属性里也看得到
  pres.theme = { headFontFace: FONT, bodyFontFace: FONT };
  const footer = { text: "隐盾 · 贴给大模型前先脱敏", options: { x: MX, y: 7.06, w: 6, h: 0.3, fontFace: FONT, fontSize: 10, color: C.dim } };
  pres.defineSlideMaster({ title: "XZ_COVER", background: { path: path.join(ASSETS, "bg_title.png") } });
  pres.defineSlideMaster({ title: "XZ_SECTION", background: { path: path.join(ASSETS, "bg_section.png") },
    objects: [{ text: footer }], slideNumber: { x: W - MX - 0.6, y: 7.04, w: 0.6, h: 0.3, fontFace: FONT, fontSize: 10, color: C.dim, align: "right" } });
  pres.defineSlideMaster({
    title: "XZ_CONTENT", background: { path: path.join(ASSETS, "bg_content.png") },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: MX, y: TITLE_Y, w: W - 2 * MX, h: 0.7, fontFace: FONT, fontSize: 28, bold: true, color: C.text, valign: "middle", align: "left", margin: 0 }, text: "" } },
      { text: footer },
    ],
    slideNumber: { x: W - MX - 0.6, y: 7.04, w: 0.6, h: 0.3, fontFace: FONT, fontSize: 10, color: C.dim, align: "right" },
  });
  let lastSection = null;
  for (const s of content.slides) {
    const sec = s.section || "正文";
    if (sec !== lastSection) { pres.addSection({ title: sec }); lastSection = sec; }
    if (s.layout === "cover") await coverSlide(pres, s);
    else if (s.layout === "closing") await closingSlide(pres, s);
    else if (s.layout === "statement") await statementSlide(pres, s);
    else await contentSlide(pres, s);
  }
  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT, content.slides.length, "slides");
}
main().catch(e => { console.error(e); process.exit(1); });
