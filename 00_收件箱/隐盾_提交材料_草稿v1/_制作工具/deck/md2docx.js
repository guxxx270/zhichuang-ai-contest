// Markdown（受限子集）→ Word：封面 + 目录 + 页眉页脚 + 表格 + 截图 + 代码块。
// 用法：node md2docx.js <in.md> <out.docx> <shots_dir> "<页眉文字>" [figs_dir]
// 支持：# ~ ####、段落、**粗体**、`代码`、- / * / 1. 列表（两级）、| 表格 |、> 引用、``` 代码块、---、
//      ![说明](S02_clarify) 或单独一行「图：S02_clarify 说明」→ 插入截图；```mermaid 块需事先渲染成 figs_dir/mermaid-N.png。
const fs = require("fs");
const path = require("path");
const sharp = require("sharp");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell, WidthType, ShadingType,
  BorderStyle, ImageRun, Header, Footer, PageNumber, TableOfContents, LevelFormat, PageBreak, TabStopType,
} = require("docx");

const [, , IN, OUT, SHOTS, HEADER_TEXT, FIGS] = process.argv;
const md = fs.readFileSync(IN, "utf8").replace(/\r\n/g, "\n");

const FONT = "Microsoft YaHei";
const MONO = "Consolas";
const BRAND = "1E3A8A", ACCENT = "0E7490", MUTE = "6B7280", INK = "1F2937";
const PAGE_W = 11906, PAGE_H = 16838, MARGIN_X = 1300, MARGIN_Y = 1300;   // A4，约 2.3 cm 边距
const TEXT_W = PAGE_W - 2 * MARGIN_X;

function font(extra = {}) { return { ascii: extra.mono ? MONO : FONT, hAnsi: extra.mono ? MONO : FONT, eastAsia: FONT, cs: FONT }; }

// 行内：**粗体**、`代码`、【待填】高亮、〔待核对〕高亮
function runs(text, base = {}) {
  text = refText(String(text));
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|【[^】]*】|〔[^〕]*〕)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), font: font(), ...base }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ text: t.slice(2, -2), bold: true, font: font(), ...base }));
    else if (t.startsWith("`")) out.push(new TextRun({ text: t.slice(1, -1), font: font({ mono: true }), size: (base.size || 21) - 2, color: "9F1239", shading: { type: ShadingType.CLEAR, color: "auto", fill: "F3F4F6" } }));
    else out.push(new TextRun({ text: t, font: font(), ...base, color: "B45309", highlight: "yellow" }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), font: font(), ...base }));
  return out.length ? out : [new TextRun({ text: "", font: font(), ...base })];
}

function para(text, opts = {}) {
  return new Paragraph({ children: runs(text, { size: opts.size || 21, color: opts.color || INK, bold: opts.bold }),
    spacing: { after: opts.after ?? 120, line: opts.line ?? 360 }, alignment: opts.align, indent: opts.indent,
    numbering: opts.numbering, style: opts.style });
}

function cellPara(text, header) {
  return new Paragraph({ children: runs(String(text), { size: 18, bold: header, color: header ? "111827" : INK }), spacing: { after: 0, line: 300 } });
}

function table(rows) {
  const nCol = Math.max(...rows.map(r => r.length));
  const lens = Array.from({ length: nCol }, (_, j) => Math.max(...rows.map(r => {
    let n = 0; for (const ch of String(r[j] || "")) n += /[\u0000-ÿ]/.test(ch) ? 0.55 : 1; return Math.min(n, 40);
  })) + 2);
  const tot = lens.reduce((a, b) => a + b, 0);
  let widths = lens.map(l => Math.max(900, Math.round(TEXT_W * l / tot)));
  const scale = TEXT_W / widths.reduce((a, b) => a + b, 0);
  widths = widths.map(w => Math.floor(w * scale));
  widths[widths.length - 1] += TEXT_W - widths.reduce((a, b) => a + b, 0);
  const border = { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1" };
  return new Table({
    width: { size: TEXT_W, type: WidthType.DXA }, columnWidths: widths,
    rows: rows.map((r, i) => new TableRow({
      tableHeader: i === 0,
      children: Array.from({ length: nCol }, (_, j) => new TableCell({
        width: { size: widths[j], type: WidthType.DXA },
        shading: i === 0 ? { type: ShadingType.CLEAR, color: "auto", fill: "E0E7FF" } : (i % 2 === 0 ? { type: ShadingType.CLEAR, color: "auto", fill: "F8FAFC" } : undefined),
        margins: { top: 60, bottom: 60, left: 100, right: 100 },
        borders: { top: border, bottom: border, left: border, right: border },
        children: [cellPara(r[j] ?? "", i === 0)],
      })),
    })),
  });
}

const FIG_DESC = {
  S01_home: "首页：贴原文或点示例", S02_redact: "脱敏结果：标签高亮与本次统计", S03_compare: "原文对照：要被换掉的内容标红",
  S04_mapping: "本次映射：标签与原文对照表", S05_copy: "复制去问大模型（开头自带保留标签提示）", S06_restore: "还原：模拟回答里改了写法的标签照样换回",
  S07_round2: "第二轮：同一客户沿用标签，只新增一个账号", S08_restore_warn: "还原：映射表里没有的标签单独提醒", S09_ops: "运维日志：连接串口令、密钥、令牌与内网地址",
  S10_suspect: "疑似遗漏：没有上下文的 8 位数字只提示", S11_sidebar: "侧边栏：会话映射表、自定义名单、白名单与类型开关", S12_rules: "规则与边界页",
  F01_flow: "隐盾数据流与本机边界", F02_engine: "规则流水线与容错还原",
};
const figNo = new Map();
let figCount = 0;
const placed = new Set();
const REF_RE = /[「（(]?图[:：]\s*((?:S|F)\d{2}_[A-Za-z0-9_]+)[」）)]?/g;
function figN(name) { if (!figNo.has(name)) figNo.set(name, ++figCount); return figNo.get(name); }
function refText(t) { return t.replace(REF_RE, (_, name) => `图 ${figN(name)}`); }
function refsIn(t) { return [...t.matchAll(REF_RE)].map(m => m[1]); }

function findImage(name) {
  const dirs = [SHOTS, FIGS].filter(Boolean);
  for (const d of dirs) for (const ext of ["", ".png", ".jpg", ".jpeg"]) {
    const p = path.join(d, name + ext);
    if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
  }
  return null;
}

async function image(name, caption) {
  placed.add(name);
  caption = `图 ${figN(name)}　${caption || FIG_DESC[name] || name}`;
  const p = findImage(name);
  if (!p) return [para(`〔${caption}：截图待补〕`, { color: MUTE, align: AlignmentType.CENTER })];
  const meta = await sharp(p).metadata();
  const maxW = 600, maxH = 420;   // pt-ish 像素，docx-js 以 96dpi 计
  let w = maxW, h = Math.round(maxW * meta.height / meta.width);
  if (h > maxH) { h = maxH; w = Math.round(maxH * meta.width / meta.height); }
  const out = [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 60 },
    children: [new ImageRun({ type: p.endsWith(".png") ? "png" : "jpg", data: fs.readFileSync(p), transformation: { width: w, height: h },
      altText: { title: caption || name, description: caption || name, name } })] })];
  if (caption) out.push(para(caption, { size: 18, color: MUTE, align: AlignmentType.CENTER, after: 200 }));
  return out;
}

async function build() {
  const lines = md.split("\n");
  const body = [];
  let i = 0, mermaidN = 0, title = "", subtitle = "";
  let olInst = 0, olOpen = false;   // 每个编号列表单独计数：非列表行之后的新列表从 1 开始
  // 首个 # 作为封面标题；紧随的 > 引用作为副标题
  while (i < lines.length && !lines[i].trim()) i++;
  if (lines[i] && lines[i].startsWith("# ")) { title = lines[i].slice(2).trim(); i++; }
  const coverNotes = [];
  while (i < lines.length && (lines[i].startsWith(">") || !lines[i].trim())) {
    if (lines[i].startsWith(">")) coverNotes.push(lines[i].replace(/^>\s?/, ""));
    i++;
  }
  subtitle = coverNotes.shift() || "";

  for (; i < lines.length; i++) {
    const line = lines[i];
    const t = line.trim();
    if (!t) continue;
    if (!/^(\s*)([-*+]|\d+[.)、])\s+/.test(line)) olOpen = false;
    if (t.startsWith("```")) {
      const lang = t.slice(3).trim();
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) { buf.push(lines[i]); i++; }
      if (lang === "mermaid") {
        mermaidN++;
        body.push(...await image(`mermaid-${mermaidN}`, ""));
        continue;
      }
      for (const b of buf) body.push(new Paragraph({ children: [new TextRun({ text: b || " ", font: font({ mono: true }), size: 17, color: "111827" })],
        shading: { type: ShadingType.CLEAR, color: "auto", fill: "F3F4F6" }, spacing: { after: 0, line: 260 }, indent: { left: 200, right: 200 } }));
      body.push(new Paragraph({ children: [], spacing: { after: 120 } }));
      continue;
    }
    let m;
    if ((m = t.match(/^(#{1,4})\s+(.*)$/))) {
      const lvl = m[1].length;
      const heading = [HeadingLevel.HEADING_1, HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3][lvl - 1];
      if (lvl <= 2 && body.length) body.push(new Paragraph({ children: [new PageBreak()] }));
      const names = refsIn(m[2]);
      const htext = m[2].replace(/[（(]图[:：][^）)]*[）)]/g, "").replace(REF_RE, "").replace(/\*\*/g, "").trim();
      body.push(new Paragraph({ heading, children: runs(htext, {}) }));
      for (const n of names) if (!placed.has(n)) body.push(...await image(n, ""));
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(t)) continue;
    if (t.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
        if (!cells.every(c => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      i--;
      const pending = rows.flat().flatMap(refsIn).filter(n => !placed.has(n));
      body.push(table(rows));
      body.push(new Paragraph({ children: [], spacing: { after: 160 } }));
      for (const n of [...new Set(pending)]) body.push(...await image(n, ""));
      continue;
    }
    if ((m = t.match(/^!\[([^\]]*)\]\(([^)]+)\)$/))) { body.push(...await image(m[2].replace(/\.(png|jpe?g)$/i, ""), m[1])); continue; }
    if (/^[「（(]?图[:：]/.test(t)) {
      for (const seg of t.split(/[；;]/)) {
        const mm = seg.match(/[「（(]?图[:：]\s*((?:S|F)\d{2}_[A-Za-z0-9_]+)[」）)]?\s*(.*)$/);
        if (mm && !placed.has(mm[1])) body.push(...await image(mm[1], mm[2].replace(/〔[^〕]*〕/g, "").trim()));
      }
      continue;
    }
    if (t.startsWith(">")) {
      body.push(new Paragraph({ children: runs(t.replace(/^>\s?/, ""), { size: 20, color: "334155" }), spacing: { after: 120, line: 340 },
        border: { left: { style: BorderStyle.SINGLE, size: 12, color: "94A3B8", space: 8 } }, indent: { left: 240 },
        shading: { type: ShadingType.CLEAR, color: "auto", fill: "F1F5F9" } }));
      continue;
    }
    if ((m = line.match(/^(\s*)([-*+]|\d+[.)、])\s+(.*)$/))) {
      const level = Math.min(1, Math.floor(m[1].replace(/\t/g, "  ").length / 2));
      const ordered = /\d/.test(m[2]);
      if (level === 0) { if (ordered && !olOpen) olInst++; olOpen = ordered; }
      body.push(new Paragraph({ children: runs(m[3], { size: 21, color: INK }), numbering: { reference: ordered ? "num" : "bul", level, ...(ordered ? { instance: olInst } : {}) },
        spacing: { after: 60, line: 340 } }));
      continue;
    }
    const pending = refsIn(t).filter(n => !placed.has(n));
    body.push(para(t));
    for (const n of [...new Set(pending)]) body.push(...await image(n, ""));
  }

  const cover = [
    new Paragraph({ children: [], spacing: { before: 1200 } }),   // 封面留白收紧：备注长时也不把分页挤到第 2 页
    new Paragraph({ alignment: AlignmentType.LEFT, children: [new TextRun({ text: "隐盾 · YinDun", font: font(), size: 28, color: ACCENT, bold: true })], spacing: { after: 240 } }),
    ...title.split(/\s*——\s*/).map((part, k) => new Paragraph({ alignment: AlignmentType.LEFT,
      children: [new TextRun({ text: part, font: font(), size: k ? 36 : 52, bold: true, color: k ? "334155" : BRAND })],
      spacing: { after: k ? 360 : 160, line: k ? 460 : 600 } })),
    ...(subtitle ? [new Paragraph({ children: runs(subtitle, { size: 26, color: "334155" }), spacing: { after: 600 } })] : []),
    ...coverNotes.map(n => new Paragraph({ children: runs(n, { size: 21, color: MUTE }), spacing: { after: 100 } })),
    new Paragraph({ children: [], spacing: { before: 600 } }),
    new Paragraph({ children: [new TextRun({ text: "「智创未来」首届 AI 创新应用大赛 · 个人赛 · 工具赛道", font: font(), size: 21, color: MUTE })], spacing: { after: 80 } }),
    new Paragraph({ children: [new TextRun({ text: "演示所用文本、号码与机构名均为虚构", font: font(), size: 21, color: MUTE })] }),
    new Paragraph({ children: [new PageBreak()] }),
    new Paragraph({ children: [new TextRun({ text: "目录", font: font(), size: 32, bold: true, color: BRAND })], spacing: { after: 200 } }),
    // 页码由 build_docs.py 按 PDF 页脚回填；Word 打开时会提示更新域，再按自己的排版刷新
    new TableOfContents("目录", { hyperlink: true, headingStyleRange: "1-3" }),
  ];

  const doc = new Document({
    creator: "隐盾", lastModifiedBy: "隐盾", title, description: subtitle.replace(/\*\*|`/g, ""),
    features: { updateFields: true },
    styles: {
      default: { document: { run: { font: font(), size: 21, color: INK }, paragraph: { spacing: { line: 360 } } } },
      paragraphStyles: [
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: font(), size: 34, bold: true, color: BRAND }, paragraph: { spacing: { before: 240, after: 240 }, outlineLevel: 0 } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: font(), size: 27, bold: true, color: "1E40AF" }, paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 } },
        { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: font(), size: 23, bold: true, color: ACCENT }, paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2 } },
        // 目录行距比正文紧：少占页，也不会把最后一两条单甩到下一页
        ...[1, 2, 3].map(n => ({ id: `TOC${n}`, name: `toc ${n}`, basedOn: "Normal", next: "Normal", uiPriority: 39, semiHidden: true, unhideWhenUsed: true,
          paragraph: { spacing: { before: n === 1 ? 60 : 0, after: 0, line: 300 }, indent: { left: (n - 1) * 210 } } })),
      ],
    },
    numbering: { config: [
      { reference: "bul", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 420, hanging: 260 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 840, hanging: 260 } } } } ] },
      { reference: "num", levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 420, hanging: 300 } } } },
        { level: 1, format: LevelFormat.DECIMAL, text: "%2)", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 840, hanging: 300 } } } } ] },
    ] },
    sections: [
      { properties: { page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN_Y, bottom: MARGIN_Y, left: MARGIN_X, right: MARGIN_X } } },
        children: cover },
      { properties: { page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN_Y, bottom: MARGIN_Y, left: MARGIN_X, right: MARGIN_X }, pageNumbers: { start: 1 } } },
        headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: HEADER_TEXT || title, font: font(), size: 17, color: MUTE })],
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1", space: 4 } } })] }) },
        footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
          children: [new TextRun({ children: ["第 ", PageNumber.CURRENT, " 页"], font: font(), size: 17, color: MUTE })] })] }) },
        children: body },
    ],
  });
  fs.writeFileSync(OUT, await Packer.toBuffer(doc));
  console.log("wrote", OUT, "blocks:", body.length);
}
build().catch(e => { console.error(e); process.exit(1); });
