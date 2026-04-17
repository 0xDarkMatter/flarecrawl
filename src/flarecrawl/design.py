"""Design system extraction engine for Flarecrawl.

Extracts design tokens (colors, typography, spacing, shadows, radii, layout,
gradients, z-index, transitions, media queries, icons, components, images)
from live web pages via CDP computed-style introspection.  Scores design
coherence across 9 categories (A+-F grading).  Formats output as markdown
or self-contained HTML preview.

All functions are pure transforms — no I/O.
"""

from __future__ import annotations

import colorsys
import datetime
import math
import re
from collections import Counter, defaultdict
from functools import reduce
from typing import Any


# ------------------------------------------------------------------
# 1. JavaScript extraction payload (runs in browser via page.evaluate)
# ------------------------------------------------------------------

EXTRACT_JS: str = """
(() => {
  const MAX_ELEMENTS = 5000;
  const result = {
    colors: [],
    cssVars: {},
    typography: {},
    spacing: [],
    radii: [],
    shadows: [],
    gradients: [],
    zIndex: [],
    transitions: [],
    layout: { grid: [], flex: [], containerWidths: [] },
    components: {},
    svgIcons: [],
    fontFiles: [],
    imagePatterns: [],
    mediaQueries: []
  };

  // --- CSS custom properties from :root ---
  try {
    const sheets = document.styleSheets;
    for (let i = 0; i < sheets.length; i++) {
      try {
        const rules = sheets[i].cssRules || sheets[i].rules;
        for (let j = 0; j < rules.length; j++) {
          const rule = rules[j];
          if (rule.selectorText === ':root' || rule.selectorText === ':root, :host') {
            for (let k = 0; k < rule.style.length; k++) {
              const prop = rule.style[k];
              if (prop.startsWith('--')) {
                result.cssVars[prop] = rule.style.getPropertyValue(prop).trim();
              }
            }
          }
        }
      } catch (e) { /* CORS stylesheet */ }
    }
  } catch (e) {}

  // --- Traverse DOM elements ---
  const allElements = document.querySelectorAll('*');
  const count = Math.min(allElements.length, MAX_ELEMENTS);

  const seenColors = new Map();
  const spacingCounts = {};
  const radiiCounts = {};
  const shadowSet = new Set();
  const gradientSet = new Set();
  const zIndexEntries = [];
  const transitionSet = new Set();
  const componentSelectors = {
    button: 'button, [role=button], input[type=submit], input[type=button], .btn',
    input: 'input:not([type=submit]):not([type=button]):not([type=hidden]), .input',
    select: 'select',
    textarea: 'textarea',
    card: '.card, [class*=card]',
    badge: '.badge, [class*=badge]',
    nav: 'nav, [role=navigation]',
    link: 'a'
  };
  const componentSamples = {};
  const svgHashes = new Set();

  for (let i = 0; i < count; i++) {
    const el = allElements[i];
    if (!el || el.tagName === 'SCRIPT' || el.tagName === 'STYLE' || el.tagName === 'NOSCRIPT') continue;

    let cs;
    try { cs = getComputedStyle(el); } catch (e) { continue; }

    const tag = el.tagName.toLowerCase();
    const cls = el.className && typeof el.className === 'string'
      ? el.className.split(/\\s+/).slice(0, 3).join('.')
      : '';
    const ctx = cls ? tag + '.' + cls : tag;

    // Colors
    const colorProps = ['background-color', 'color', 'border-color'];
    for (const prop of colorProps) {
      const val = cs.getPropertyValue(prop);
      if (val && val !== 'rgba(0, 0, 0, 0)' && val !== 'transparent') {
        const key = prop + ':' + val;
        if (seenColors.has(key)) {
          seenColors.get(key).count++;
        } else {
          seenColors.set(key, { property: prop, value: val, context: ctx, count: 1 });
        }
      }
    }

    // Typography
    const typoTags = ['h1','h2','h3','h4','h5','h6','body','p','code','small'];
    if (typoTags.includes(tag) && !result.typography[tag]) {
      result.typography[tag] = {
        fontFamily: cs.fontFamily,
        fontSize: cs.fontSize,
        fontWeight: cs.fontWeight,
        lineHeight: cs.lineHeight,
        letterSpacing: cs.letterSpacing
      };
    }

    // Spacing
    const spacingProps = [
      'margin-top','margin-right','margin-bottom','margin-left',
      'padding-top','padding-right','padding-bottom','padding-left'
    ];
    for (const sp of spacingProps) {
      const v = cs.getPropertyValue(sp);
      if (v && v !== '0px' && v !== 'auto') {
        spacingCounts[v] = (spacingCounts[v] || 0) + 1;
      }
    }

    // Border radii
    const br = cs.borderRadius;
    if (br && br !== '0px') {
      radiiCounts[br] = (radiiCounts[br] || 0) + 1;
    }

    // Box shadows
    const shadow = cs.boxShadow;
    if (shadow && shadow !== 'none') {
      shadowSet.add(shadow);
    }

    // Gradients
    const bgImg = cs.backgroundImage;
    if (bgImg && bgImg !== 'none' && bgImg.includes('gradient')) {
      gradientSet.add(bgImg);
    }

    // Z-index
    const zi = cs.zIndex;
    if (zi && zi !== 'auto' && zi !== '0') {
      zIndexEntries.push({ value: parseInt(zi, 10), context: ctx });
    }

    // Transitions
    const tr = cs.transition;
    if (tr && tr !== 'all 0s ease 0s' && tr !== 'none') {
      transitionSet.add(tr);
    }

    // Layout: grid
    const display = cs.display;
    if (display === 'grid' || display === 'inline-grid') {
      result.layout.grid.push({
        context: ctx,
        gridTemplateColumns: cs.gridTemplateColumns,
        gap: cs.gap
      });
    }
    // Layout: flex
    if (display === 'flex' || display === 'inline-flex') {
      result.layout.flex.push({
        context: ctx,
        flexDirection: cs.flexDirection,
        gap: cs.gap,
        justifyContent: cs.justifyContent,
        alignItems: cs.alignItems
      });
    }
    // Container max-widths
    const mw = cs.maxWidth;
    if (mw && mw !== 'none' && mw !== '0px' && parseFloat(mw) > 0) {
      if (!result.layout.containerWidths.includes(mw)) {
        result.layout.containerWidths.push(mw);
      }
    }
  }

  // Collect component samples (first 3 of each type)
  for (const [type, selector] of Object.entries(componentSelectors)) {
    const els = document.querySelectorAll(selector);
    const samples = [];
    for (let i = 0; i < Math.min(els.length, 3); i++) {
      try {
        const cs = getComputedStyle(els[i]);
        samples.push({
          tag: els[i].tagName.toLowerCase(),
          className: els[i].className && typeof els[i].className === 'string'
            ? els[i].className : '',
          styles: {
            display: cs.display,
            padding: cs.padding,
            margin: cs.margin,
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            color: cs.color,
            backgroundColor: cs.backgroundColor,
            borderRadius: cs.borderRadius,
            border: cs.border,
            boxShadow: cs.boxShadow,
            cursor: cs.cursor,
            textDecoration: cs.textDecoration,
            lineHeight: cs.lineHeight,
            fontFamily: cs.fontFamily,
            gap: cs.gap
          }
        });
      } catch (e) {}
    }
    if (samples.length > 0) {
      componentSamples[type] = samples;
    }
  }
  result.components = componentSamples;

  // Convert maps/sets to arrays
  result.colors = Array.from(seenColors.values());
  result.spacing = Object.entries(spacingCounts)
    .map(([value, count]) => ({ value, count }));
  result.radii = Object.entries(radiiCounts)
    .map(([value, count]) => ({ value, count }));
  result.shadows = Array.from(shadowSet);
  result.gradients = Array.from(gradientSet);
  result.zIndex = zIndexEntries;
  result.transitions = Array.from(transitionSet);

  // --- Inline SVGs (deduplicated by innerHTML hash) ---
  const svgs = document.querySelectorAll('svg');
  for (let i = 0; i < svgs.length; i++) {
    const svg = svgs[i];
    const html = svg.innerHTML.trim();
    if (!html) continue;
    let hash = 0;
    for (let c = 0; c < html.length; c++) {
      hash = ((hash << 5) - hash + html.charCodeAt(c)) | 0;
    }
    const key = String(hash);
    if (svgHashes.has(key)) continue;
    svgHashes.add(key);
    const viewBox = svg.getAttribute('viewBox') || '';
    const paths = svg.querySelectorAll('path');
    const pathData = [];
    for (let p = 0; p < Math.min(paths.length, 3); p++) {
      pathData.push((paths[p].getAttribute('d') || '').substring(0, 100));
    }
    const hasStroke = svg.querySelector('[stroke]:not([stroke=none])') !== null;
    const hasFill = svg.querySelector('[fill]:not([fill=none])') !== null;
    result.svgIcons.push({ viewBox, paths: pathData, hasStroke, hasFill });
  }

  // --- Font sources ---
  const links = document.querySelectorAll('link[rel=stylesheet]');
  for (let i = 0; i < links.length; i++) {
    const href = links[i].href || '';
    if (href.includes('fonts.googleapis.com')) {
      result.fontFiles.push({ source: 'google-fonts', url: href });
    }
  }
  try {
    for (let i = 0; i < document.styleSheets.length; i++) {
      try {
        const rules = document.styleSheets[i].cssRules;
        for (let j = 0; j < rules.length; j++) {
          if (rules[j].type === CSSRule.FONT_FACE_RULE) {
            const ff = rules[j];
            const family = ff.style.getPropertyValue('font-family')
              .replace(/['"]/g, '').trim();
            const src = ff.style.getPropertyValue('src');
            if (family && src) {
              result.fontFiles.push({ family, src, source: 'font-face' });
            }
          }
        }
      } catch (e) { /* CORS */ }
    }
  } catch (e) {}

  // --- Image patterns ---
  const images = document.querySelectorAll('img');
  for (let i = 0; i < Math.min(images.length, 50); i++) {
    const img = images[i];
    const cs = getComputedStyle(img);
    const w = parseFloat(cs.width) || 0;
    const h = parseFloat(cs.height) || 0;
    result.imagePatterns.push({
      src: (img.src || '').substring(0, 200),
      width: w,
      height: h,
      aspectRatio: h > 0 ? Math.round((w / h) * 100) / 100 : 0,
      objectFit: cs.objectFit,
      loading: img.getAttribute('loading'),
      borderRadius: cs.borderRadius
    });
  }

  // --- Media queries ---
  try {
    for (let i = 0; i < document.styleSheets.length; i++) {
      try {
        const rules = document.styleSheets[i].cssRules;
        for (let j = 0; j < rules.length; j++) {
          if (rules[j].type === CSSRule.MEDIA_RULE) {
            const mq = rules[j].conditionText || rules[j].media.mediaText;
            if (mq && !result.mediaQueries.includes(mq)) {
              result.mediaQueries.push(mq);
            }
          }
        }
      } catch (e) { /* CORS */ }
    }
  } catch (e) {}

  return result;
})()
"""


# ------------------------------------------------------------------
# 2. Token processing
# ------------------------------------------------------------------

_RGB_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*[\d.]+)?\s*\)"
)


def _rgb_to_hex(rgb_str: str) -> str:
    """Convert rgb()/rgba() string to #hex."""
    m = _RGB_RE.match(rgb_str.strip())
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"#{r:02x}{g:02x}{b:02x}"
    if rgb_str.startswith("#"):
        h = rgb_str.strip()
        if len(h) == 4:
            return f"#{h[1]*2}{h[2]*2}{h[3]*2}"
        return h.lower()
    return rgb_str.strip()


def _parse_px(value: str) -> float | None:
    """Parse a CSS pixel value to float."""
    if not value:
        return None
    m = re.match(r"([\d.]+)\s*px", value)
    return float(m.group(1)) if m else None


def _gcd(a: int, b: int) -> int:
    """Greatest common divisor."""
    while b:
        a, b = b, a % b
    return a


def _detect_modular_ratio(sizes: list[float]) -> float | None:
    """Detect modular ratio from a sorted list of font sizes."""
    known_ratios = [1.125, 1.2, 1.25, 1.333, 1.5, 1.618]
    if len(sizes) < 3:
        return None
    ratios = []
    for i in range(len(sizes) - 1):
        if sizes[i] > 0:
            ratios.append(sizes[i + 1] / sizes[i])
    if not ratios:
        return None
    avg = sum(ratios) / len(ratios)
    for kr in known_ratios:
        if abs(avg - kr) < 0.08:
            return kr
    return None


def _classify_shadow_tier(shadow: str) -> str:
    """Classify a box-shadow by blur radius into sm/md/lg/xl."""
    m = re.search(r"(?:[\d.]+px\s+){2}([\d.]+)px", shadow)
    if not m:
        return "sm"
    blur = float(m.group(1))
    if blur <= 2:
        return "sm"
    if blur <= 8:
        return "md"
    if blur <= 16:
        return "lg"
    return "xl"


def _gradient_stop_count(gradient: str) -> int:
    """Count color stops in a gradient string."""
    parts = re.split(r",\s*(?![^(]*\))", gradient)
    return max(len(parts) - 1, 1)


def _group_by(items: list[dict], key: str) -> dict[str, list[dict]]:
    """Group list of dicts by a key value."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        groups[item.get(key, "other")].append(item)
    return dict(groups)


def process_tokens(raw_data: dict) -> dict:
    """Process raw JS extraction output into structured design tokens."""
    tokens: dict[str, Any] = {}

    # --- Colors: deduplicate, normalize hex, group by role, frequency sort ---
    color_entries = raw_data.get("colors", [])
    color_map: dict[str, dict] = {}
    for entry in color_entries:
        hex_val = _rgb_to_hex(entry.get("value", ""))
        prop = entry.get("property", "")
        count = entry.get("count", 1)
        ctx = entry.get("context", "")
        if prop == "background-color":
            role = "background"
        elif prop == "color":
            role = "text"
        elif prop == "border-color":
            role = "border"
        else:
            role = "accent"
        key = f"{hex_val}:{role}"
        if key in color_map:
            color_map[key]["count"] += count
        else:
            color_map[key] = {
                "hex": hex_val, "role": role, "count": count, "context": ctx,
            }
    colors_list = sorted(color_map.values(), key=lambda c: c["count"], reverse=True)
    orphans = [c for c in colors_list if c["count"] == 1]
    tokens["colors"] = {
        "all": colors_list,
        "by_role": _group_by(colors_list, "role"),
        "orphan_count": len(orphans),
    }

    # --- CSS vars ---
    tokens["cssVars"] = raw_data.get("cssVars", {})

    # --- Typography: build scale, detect ratio ---
    typo = raw_data.get("typography", {})
    scale_sizes: list[float] = []
    for props in typo.values():
        px = _parse_px(props.get("fontSize", ""))
        if px:
            scale_sizes.append(px)
    scale_sizes = sorted(set(scale_sizes))
    ratio = _detect_modular_ratio(scale_sizes)
    font_families: set[str] = set()
    for props in typo.values():
        ff = props.get("fontFamily", "")
        if ff:
            primary = ff.split(",")[0].strip().strip("'\"")
            font_families.add(primary)
    tokens["typography"] = {
        "elements": typo,
        "scale": scale_sizes,
        "modular_ratio": ratio,
        "font_families": list(font_families),
    }

    # --- Spacing: sort, detect base unit via GCD, flag outliers ---
    spacing_entries = raw_data.get("spacing", [])
    spacing_values: list[tuple[float, int]] = []
    for entry in spacing_entries:
        px = _parse_px(entry.get("value", ""))
        if px is not None and px > 0:
            spacing_values.append((px, entry.get("count", 1)))
    spacing_values.sort(key=lambda x: x[0])
    unique_px = sorted(set(v[0] for v in spacing_values))
    int_vals = [int(v) for v in unique_px if v == int(v) and v > 0]
    base_unit = reduce(_gcd, int_vals) if len(int_vals) >= 2 else (
        int_vals[0] if int_vals else 4
    )
    outliers = [v for v in unique_px if base_unit and v % base_unit != 0]
    tokens["spacing"] = {
        "values": spacing_values,
        "unique": unique_px,
        "base_unit": base_unit,
        "outliers": outliers,
    }

    # --- Radii: sort into scale ---
    radii_entries = raw_data.get("radii", [])
    radii_list = []
    for entry in radii_entries:
        radii_list.append({
            "value": entry.get("value", ""), "count": entry.get("count", 1),
        })
    radii_list.sort(key=lambda r: _parse_px(r["value"]) or 0)
    tokens["radii"] = radii_list

    # --- Shadows: classify tiers by blur ---
    shadows_raw = raw_data.get("shadows", [])
    shadow_tiers: dict[str, list[str]] = defaultdict(list)
    for s in shadows_raw:
        tier = _classify_shadow_tier(s)
        shadow_tiers[tier].append(s)
    tokens["shadows"] = dict(shadow_tiers)

    # --- Gradients: classify by stop count ---
    gradients_raw = raw_data.get("gradients", [])
    grad_list = []
    for g in gradients_raw:
        stops = _gradient_stop_count(g)
        classification = (
            "subtle" if stops <= 2 else ("brand" if stops == 3 else "bold")
        )
        grad_list.append({
            "value": g, "stops": stops, "classification": classification,
        })
    tokens["gradients"] = grad_list

    # --- Z-index: sort layers, flag wars (>9999) and gaps (>100) ---
    z_entries = raw_data.get("zIndex", [])
    z_sorted = sorted(z_entries, key=lambda z: z.get("value", 0))
    wars = [z for z in z_sorted if abs(z.get("value", 0)) > 9999]
    gaps = []
    for i in range(len(z_sorted) - 1):
        diff = abs(
            z_sorted[i + 1].get("value", 0) - z_sorted[i].get("value", 0)
        )
        if diff > 100:
            gaps.append({
                "between": [z_sorted[i], z_sorted[i + 1]], "gap": diff,
            })
    tokens["zIndex"] = {"layers": z_sorted, "wars": wars, "gaps": gaps}

    # --- Transitions ---
    tokens["transitions"] = raw_data.get("transitions", [])

    # --- Layout: count grid/flex, list templates and container widths ---
    layout = raw_data.get("layout", {})
    grid_items = layout.get("grid", [])
    flex_items = layout.get("flex", [])
    containers = layout.get("containerWidths", [])
    grid_templates = list({
        g.get("gridTemplateColumns", "")
        for g in grid_items if g.get("gridTemplateColumns")
    })
    tokens["layout"] = {
        "grid_count": len(grid_items),
        "flex_count": len(flex_items),
        "grid_templates": grid_templates,
        "flex_items": flex_items[:10],
        "container_widths": containers,
    }

    # --- Components: group by type, extract 3-5 key properties ---
    components_raw = raw_data.get("components", {})
    key_props = [
        "display", "padding", "fontSize", "fontWeight", "color",
        "backgroundColor", "borderRadius", "border", "boxShadow", "cursor",
    ]
    processed_components: dict[str, list[dict]] = {}
    for comp_type, samples in components_raw.items():
        processed = []
        for sample in samples:
            styles = sample.get("styles", {})
            filtered = {
                k: v for k, v in styles.items()
                if k in key_props and v and v != "none" and v != "0px"
            }
            processed.append({
                "tag": sample.get("tag", ""),
                "className": sample.get("className", ""),
                "styles": filtered,
            })
        processed_components[comp_type] = processed
    tokens["components"] = processed_components

    # --- SVG icons: count, classify outline vs solid ---
    svgs = raw_data.get("svgIcons", [])
    outline_count = sum(1 for s in svgs if s.get("hasStroke"))
    solid_count = sum(1 for s in svgs if not s.get("hasStroke"))
    tokens["svgIcons"] = {
        "count": len(svgs),
        "outline": outline_count,
        "solid": solid_count,
        "items": svgs,
    }

    # --- Font files: classify source by URL pattern ---
    font_files = raw_data.get("fontFiles", [])
    for ff in font_files:
        url = ff.get("url", "") or ff.get("src", "")
        if "fonts.googleapis.com" in url:
            ff["source_type"] = "google-fonts"
        elif any(cdn in url.lower() for cdn in ("cdn", "cdnjs", "jsdelivr")):
            ff["source_type"] = "cdn"
        elif url.startswith(("/", "./", "../")):
            ff["source_type"] = "self-hosted"
        else:
            ff["source_type"] = "system"
    tokens["fontFiles"] = font_files

    # --- Images: classify by computed size ---
    images = raw_data.get("imagePatterns", [])
    for img in images:
        size = max(img.get("width", 0), img.get("height", 0))
        if size < 64:
            img["classification"] = "avatar"
        elif size < 200:
            img["classification"] = "thumbnail"
        elif size > 600:
            img["classification"] = "hero"
        else:
            img["classification"] = "gallery"
    tokens["imagePatterns"] = images

    # --- Media queries ---
    tokens["mediaQueries"] = raw_data.get("mediaQueries", [])

    return tokens


# ------------------------------------------------------------------
# 3. WCAG contrast
# ------------------------------------------------------------------

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    if len(h) < 6:
        h = h.ljust(6, "0")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(r: int, g: int, b: int) -> float:
    """Calculate relative luminance per WCAG 2.1."""
    def linearize(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def _contrast_ratio(l1: float, l2: float) -> float:
    """Calculate contrast ratio from two luminance values."""
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def wcag_contrast(fg_hex: str, bg_hex: str) -> dict:
    """Calculate WCAG 2.1 contrast ratio between two hex colors."""
    fg_rgb = _hex_to_rgb(fg_hex)
    bg_rgb = _hex_to_rgb(bg_hex)
    l1 = _relative_luminance(*fg_rgb)
    l2 = _relative_luminance(*bg_rgb)
    ratio = round(_contrast_ratio(l1, l2), 2)
    return {
        "ratio": ratio,
        "aa": ratio >= 4.5,
        "aaa": ratio >= 7.0,
        "aa_large": ratio >= 3.0,
    }


def calculate_all_contrasts(colors: dict) -> list[dict]:
    """Pair all text colors against all background colors for WCAG check."""
    text_colors = colors.get("by_role", {}).get("text", [])
    bg_colors = colors.get("by_role", {}).get("background", [])
    results = []
    for tc in text_colors:
        for bc in bg_colors:
            contrast = wcag_contrast(tc["hex"], bc["hex"])
            results.append({
                "foreground": tc["hex"],
                "background": bc["hex"],
                "fg_context": tc.get("context", ""),
                "bg_context": bc.get("context", ""),
                **contrast,
            })
    return results


# ------------------------------------------------------------------
# 4. Coherence scoring
# ------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamp value between lo and hi."""
    return max(lo, min(hi, value))


def score_coherence(tokens: dict) -> dict:
    """Score design system coherence across 9 categories (0-100 each)."""
    categories: dict[str, dict] = {}

    # 1. Color ---------------------------------------------------------------
    all_colors = tokens.get("colors", {}).get("all", [])
    orphan_count = tokens.get("colors", {}).get("orphan_count", 0)
    unique_hex = len({c["hex"] for c in all_colors})
    color_score = 100.0
    if unique_hex > 20:
        color_score -= 2 * (unique_hex - 20)
    color_score -= 5 * orphan_count
    # Bonus for semantic colour groups (success / error / warning)
    all_hex_lower = {c["hex"].lower() for c in all_colors}
    semantic_flags = 0
    for h in all_hex_lower:
        try:
            r, g, b = _hex_to_rgb(h)
            hue, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            hue_deg = hue * 360
            if sat > 0.3:
                if 90 < hue_deg < 160:
                    semantic_flags |= 1  # green / success
                elif hue_deg < 30 or hue_deg > 340:
                    semantic_flags |= 2  # red / error
                elif 30 < hue_deg < 70:
                    semantic_flags |= 4  # yellow / warning
        except (ValueError, ZeroDivisionError):
            pass
    if bin(semantic_flags).count("1") >= 2:
        color_score += 10
    color_score = _clamp(color_score)
    categories["color"] = {
        "score": round(color_score),
        "note": f"{unique_hex} unique colors, {orphan_count} orphans (used once).",
    }

    # 2. Typography ----------------------------------------------------------
    typo = tokens.get("typography", {})
    families = typo.get("font_families", [])
    ratio = typo.get("modular_ratio")
    scale = typo.get("scale", [])
    elements = typo.get("elements", {})
    typo_score = 100.0
    if len(families) > 3:
        typo_score -= 5 * (len(families) - 3)
    if not ratio:
        typo_score -= 10
    heading_tags = [t for t in ["h1", "h2", "h3", "h4", "h5", "h6"] if t in elements]
    if len(heading_tags) >= 4:
        typo_score += 10
    typo_score = _clamp(typo_score)
    ratio_note = f" modular ratio {ratio}" if ratio else " no modular ratio detected"
    categories["typography"] = {
        "score": round(typo_score),
        "note": (
            f"{len(families)} font families,{ratio_note},"
            f" {len(scale)} sizes in scale."
        ),
    }

    # 3. Spacing -------------------------------------------------------------
    spacing = tokens.get("spacing", {})
    base = spacing.get("base_unit", 4)
    sp_outliers = spacing.get("outliers", [])
    unique_vals = spacing.get("unique", [])
    spacing_score = 100.0
    spacing_score -= 5 * len(sp_outliers)
    if not sp_outliers and unique_vals:
        spacing_score += 10
    spacing_score = _clamp(spacing_score)
    categories["spacing"] = {
        "score": round(spacing_score),
        "note": (
            f"Base unit {base}px, {len(unique_vals)} unique values,"
            f" {len(sp_outliers)} off-grid outliers."
        ),
    }

    # 4. Shadows -------------------------------------------------------------
    shadow_tiers = tokens.get("shadows", {})
    tier_count = len(shadow_tiers)
    shadow_score = 100.0
    if 3 <= tier_count <= 5:
        shadow_score += 20
    if tier_count > 6:
        shadow_score -= 10 * (tier_count - 6)
    if tier_count <= 1:
        shadow_score -= 20
    shadow_score = _clamp(shadow_score)
    total_shadows = sum(len(v) for v in shadow_tiers.values())
    categories["shadows"] = {
        "score": round(shadow_score),
        "note": f"{total_shadows} shadows across {tier_count} tiers.",
    }

    # 5. Radii ---------------------------------------------------------------
    radii = tokens.get("radii", [])
    unique_radii = len(radii)
    radii_score = 100.0
    if 3 <= unique_radii <= 6:
        radii_score += 10
    if unique_radii > 8:
        radii_score -= 5 * (unique_radii - 8)
    radii_score = _clamp(radii_score)
    categories["radii"] = {
        "score": round(radii_score),
        "note": f"{unique_radii} unique border-radius values.",
    }

    # 6. Accessibility -------------------------------------------------------
    contrasts = calculate_all_contrasts(tokens.get("colors", {}))
    passing = sum(1 for c in contrasts if c.get("aa"))
    total_pairs = len(contrasts) if contrasts else 1
    a11y_score = _clamp((passing / total_pairs) * 100 if contrasts else 50.0)
    categories["accessibility"] = {
        "score": round(a11y_score),
        "note": f"{passing}/{total_pairs} text/background pairs pass WCAG AA.",
    }

    # 7. Tokenisation --------------------------------------------------------
    css_vars = tokens.get("cssVars", {})
    var_count = len(css_vars)
    hardcoded_count = unique_hex
    total_pool = var_count + hardcoded_count
    token_score = _clamp(
        (var_count / total_pool) * 100 if total_pool else 0.0
    )
    categories["tokenisation"] = {
        "score": round(token_score),
        "note": f"{var_count} CSS variables vs {hardcoded_count} hardcoded values.",
    }

    # 8. Layout --------------------------------------------------------------
    layout = tokens.get("layout", {})
    layout_score = 100.0
    if layout.get("grid_count", 0) > 0:
        layout_score += 10
    # Consistent gaps
    flex_gaps = {
        f.get("gap") for f in layout.get("flex_items", []) if f.get("gap")
    }
    if 0 < len(flex_gaps) <= 3:
        layout_score += 10
    container_widths = layout.get("container_widths", [])
    if len(container_widths) > 5:
        layout_score -= 10
    layout_score = _clamp(layout_score)
    categories["layout"] = {
        "score": round(layout_score),
        "note": (
            f"{layout.get('grid_count', 0)} grid,"
            f" {layout.get('flex_count', 0)} flex elements,"
            f" {len(container_widths)} container widths."
        ),
    }

    # 9. Responsiveness ------------------------------------------------------
    mqs = tokens.get("mediaQueries", [])
    breakpoints = [mq for mq in mqs if "min-width" in mq or "max-width" in mq]
    resp_score = _clamp(70.0 + min(len(breakpoints) * 10, 30))
    categories["responsiveness"] = {
        "score": round(resp_score),
        "note": (
            f"{len(breakpoints)} breakpoints defined"
            f" across {len(mqs)} media queries."
        ),
    }

    # --- Overall + grade ---
    scores = [cat["score"] for cat in categories.values()]
    overall = round(sum(scores) / len(scores)) if scores else 0
    grade_map = [
        (95, "A+"), (90, "A"), (85, "A-"), (80, "B+"), (75, "B"),
        (70, "B-"), (65, "C+"), (60, "C"), (50, "D"),
    ]
    grade = "F"
    for threshold, g in grade_map:
        if overall >= threshold:
            grade = g
            break

    issues = []
    for name, cat in categories.items():
        if cat["score"] < 70:
            issues.append(f"[{name}] Score {cat['score']}/100 -- {cat['note']}")

    return {
        "overall": overall,
        "grade": grade,
        "categories": categories,
        "issues": issues,
    }


# ------------------------------------------------------------------
# 5. Markdown report
# ------------------------------------------------------------------

def _bar(value: int, width: int = 10) -> str:
    """Render a block-char progress bar."""
    filled = round(value / 100 * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _domain_from_url(url: str) -> str:
    """Extract domain from URL."""
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else url


def format_design_md(tokens: dict, coherence: dict, url: str) -> str:
    """Generate a full design system markdown report."""
    domain = _domain_from_url(url)
    today = datetime.date.today().isoformat()
    lines: list[str] = []

    def _w(text: str = "") -> None:
        lines.append(text)

    # 1. Title
    _w(f"# Design System: {domain}")
    _w()

    # 2. Blockquote
    _w(f"> Extracted from {url} on {today} by flarecrawl")
    _w()

    # 3. Design Coherence
    _w("## Design Coherence")
    _w()
    _w(f"**Overall: {coherence['overall']}/100 ({coherence['grade']})**")
    _w()
    for name, cat in coherence.get("categories", {}).items():
        bar = _bar(cat["score"])
        _w(f"{name:<16} {bar} {cat['score']}%")
    _w()
    issues = coherence.get("issues", [])
    if issues:
        _w("### Issues")
        _w()
        for issue in issues:
            _w(f"- {issue}")
        _w()

    # 4. Color Palette
    _w("## Color Palette")
    _w()
    by_role = tokens.get("colors", {}).get("by_role", {})
    for role_name in ["background", "text", "border", "accent"]:
        role_colors = by_role.get(role_name, [])
        if not role_colors:
            continue
        _w(f"### {role_name.title()}")
        _w()
        _w("| Hex | Count | Context |")
        _w("|-----|-------|---------|")
        for c in role_colors[:15]:
            _w(f"| `{c['hex']}` | {c['count']} | {c.get('context', '')} |")
        _w()

    # 5. Dark Mode
    _w("## Dark Mode")
    _w()
    _w("Enable with `--dark` or `--auto-dark`")
    _w()

    # 6. Typography
    _w("## Typography")
    _w()
    typo = tokens.get("typography", {})
    elements = typo.get("elements", {})
    if elements:
        _w("| Element | Font | Size | Weight | Line Height |")
        _w("|---------|------|------|--------|-------------|")
        for tag in ["h1", "h2", "h3", "h4", "h5", "h6", "body", "p", "code", "small"]:
            if tag in elements:
                e = elements[tag]
                font = (e.get("fontFamily", "") or "").split(",")[0].strip("'\" ")
                _w(
                    f"| `{tag}` | {font} | {e.get('fontSize', '')}"
                    f" | {e.get('fontWeight', '')} | {e.get('lineHeight', '')} |"
                )
        _w()
    if typo.get("modular_ratio"):
        _w(f"**Modular ratio:** {typo['modular_ratio']}")
        _w()

    # 7. Font Files
    font_files = tokens.get("fontFiles", [])
    if font_files:
        _w("## Font Files")
        _w()
        _w("| Font | Source | URL |")
        _w("|------|--------|-----|")
        for ff in font_files:
            name = ff.get("family", "")
            src = ff.get("source_type", ff.get("source", ""))
            furl = ff.get("url", ff.get("src", ""))
            if len(furl) > 80:
                furl = furl[:77] + "..."
            _w(f"| {name} | {src} | `{furl}` |")
        _w()

    # 8. Spacing Scale
    _w("## Spacing Scale")
    _w()
    spacing = tokens.get("spacing", {})
    _w(f"**Base unit:** {spacing.get('base_unit', '?')}px")
    _w()
    unique = spacing.get("unique", [])
    if unique:
        scale_str = " -> ".join(f"{v}px" for v in unique[:20])
        _w(f"Scale: {scale_str}")
        _w()

    # 9. Border Radii
    radii = tokens.get("radii", [])
    if radii:
        _w("## Border Radii")
        _w()
        _w("| Value | Usage Count |")
        _w("|-------|-------------|")
        for r in radii:
            _w(f"| `{r['value']}` | {r['count']} |")
        _w()

    # 10. Box Shadows
    shadows = tokens.get("shadows", {})
    if shadows:
        _w("## Box Shadows")
        _w()
        for tier in ["sm", "md", "lg", "xl"]:
            tier_shadows = shadows.get(tier, [])
            if tier_shadows:
                _w(f"### {tier.upper()}")
                _w()
                for s in tier_shadows:
                    _w(f"- `{s[:100]}`")
                _w()

    # 11. CSS Custom Properties
    css_vars = tokens.get("cssVars", {})
    if css_vars:
        _w("## CSS Custom Properties")
        _w()
        grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for prop, val in css_vars.items():
            parts = prop.split("-")
            prefix = parts[2] if len(parts) > 2 else "misc"
            grouped[prefix].append((prop, val))
        for prefix, items in sorted(grouped.items()):
            _w(f"### --{prefix}")
            _w()
            for prop, val in items[:20]:
                _w(f"- `{prop}: {val}`")
            _w()

    # 12. Breakpoints
    mqs = tokens.get("mediaQueries", [])
    breakpoint_mqs = [
        mq for mq in mqs if "min-width" in mq or "max-width" in mq
    ]
    if breakpoint_mqs:
        _w("## Breakpoints")
        _w()
        _w("| Media Query |")
        _w("|-------------|")
        for mq in breakpoint_mqs:
            _w(f"| `{mq}` |")
        _w()

    # 13. Layout System
    layout = tokens.get("layout", {})
    _w("## Layout System")
    _w()
    _w(f"- **Grid elements:** {layout.get('grid_count', 0)}")
    _w(f"- **Flex elements:** {layout.get('flex_count', 0)}")
    templates = layout.get("grid_templates", [])
    if templates:
        _w(f"- **Grid templates:** {', '.join(f'`{t}`' for t in templates[:5])}")
    containers = layout.get("container_widths", [])
    if containers:
        _w(
            f"- **Container widths:**"
            f" {', '.join(f'`{c}`' for c in containers[:10])}"
        )
    _w()

    # 14. Component Patterns
    components = tokens.get("components", {})
    if components:
        _w("## Component Patterns")
        _w()
        for comp_type, samples in components.items():
            _w(f"### {comp_type.title()}")
            _w()
            for sample in samples[:3]:
                tag = sample.get("tag", "")
                cls = sample.get("className", "")
                label = f"{tag}.{cls}" if cls else tag
                _w(f"**{label}**")
                _w("```css")
                for prop, val in sample.get("styles", {}).items():
                    _w(f"  {prop}: {val};")
                _w("```")
            _w()

    # 15. Interaction States
    _w("## Interaction States")
    _w()
    _w("*Placeholder -- requires `:hover`/`:focus` pseudo-class extraction.*")
    _w()

    # 16. Transitions
    transitions = tokens.get("transitions", [])
    if transitions:
        _w("## Transitions")
        _w()
        _w("| Transition |")
        _w("|------------|")
        for t in transitions[:20]:
            _w(f"| `{t[:100]}` |")
        _w()

    # 17. Gradients
    gradients = tokens.get("gradients", [])
    if gradients:
        _w("## Gradients")
        _w()
        _w("| Gradient | Stops | Class |")
        _w("|----------|-------|-------|")
        for g in gradients:
            val = g["value"][:80] + "..." if len(g["value"]) > 80 else g["value"]
            _w(f"| `{val}` | {g['stops']} | {g['classification']} |")
        _w()

    # 18. Z-Index Map
    z = tokens.get("zIndex", {})
    z_layers = z.get("layers", [])
    if z_layers:
        _w("## Z-Index Map")
        _w()
        _w("| Value | Context |")
        _w("|-------|---------|")
        for layer in z_layers:
            flag = " **WAR**" if abs(layer.get("value", 0)) > 9999 else ""
            _w(f"| {layer.get('value', '')} | {layer.get('context', '')}{flag} |")
        _w()

    # 19. SVG Icons
    icons = tokens.get("svgIcons", {})
    if icons.get("count", 0) > 0:
        _w("## SVG Icons")
        _w()
        _w(f"- **Total:** {icons['count']}")
        _w(f"- **Outline:** {icons.get('outline', 0)}")
        _w(f"- **Solid:** {icons.get('solid', 0)}")
        _w()

    # 20. Accessibility
    _w("## Accessibility")
    _w()
    contrasts = calculate_all_contrasts(tokens.get("colors", {}))
    failing = [c for c in contrasts if not c.get("aa")]
    _w(
        f"**WCAG AA compliance:**"
        f" {len(contrasts) - len(failing)}/{len(contrasts)} pairs pass"
    )
    _w()
    if failing:
        _w("### Failing Pairs")
        _w()
        _w("| Foreground | Background | Ratio | AA |")
        _w("|------------|------------|-------|----|")
        for f in failing[:20]:
            _w(
                f"| `{f['foreground']}` | `{f['background']}`"
                f" | {f['ratio']} | FAIL |"
            )
        _w()

    # 21. Image Patterns
    images = tokens.get("imagePatterns", [])
    if images:
        _w("## Image Patterns")
        _w()
        _w("| Classification | Width | Height | Aspect | Object-Fit | Loading |")
        _w("|----------------|-------|--------|--------|------------|---------|")
        for img in images[:20]:
            _w(
                f"| {img.get('classification', '')}"
                f" | {img.get('width', '')}"
                f" | {img.get('height', '')}"
                f" | {img.get('aspectRatio', '')}"
                f" | {img.get('objectFit', '')}"
                f" | {img.get('loading', '')} |"
            )
        _w()

    # 22. Quick Start
    _w("## Quick Start")
    _w()
    _w("```css")
    _w(":root {")
    if css_vars:
        for prop, val in list(css_vars.items())[:50]:
            _w(f"  {prop}: {val};")
    else:
        for i, c in enumerate(tokens.get("colors", {}).get("all", [])[:10]):
            _w(f"  --color-{i}: {c['hex']};")
        sp = tokens.get("spacing", {})
        if sp.get("base_unit"):
            _w(f"  --space-base: {sp['base_unit']}px;")
    _w("}")
    _w("```")
    _w()

    return "\n".join(lines)


# ------------------------------------------------------------------
# 6. HTML preview
# ------------------------------------------------------------------

def format_preview_html(tokens: dict, coherence: dict, url: str) -> str:
    """Generate a self-contained HTML design system preview (dark theme)."""
    domain = _domain_from_url(url)
    grade = coherence.get("grade", "?")
    overall = coherence.get("overall", 0)
    categories = coherence.get("categories", {})
    all_colors = tokens.get("colors", {}).get("all", [])
    typo = tokens.get("typography", {})
    elements = typo.get("elements", {})
    spacing = tokens.get("spacing", {})
    shadows = tokens.get("shadows", {})
    contrasts = calculate_all_contrasts(tokens.get("colors", {}))
    passing_count = sum(1 for c in contrasts if c.get("aa"))
    total_pairs = len(contrasts) or 1

    # Color swatches
    swatches_html = ""
    for c in all_colors[:40]:
        hex_val = c["hex"]
        swatches_html += (
            f'<div class="swatch" style="background:{hex_val}"'
            f' title="{hex_val} ({c.get("role","")}, {c.get("count",0)}x)">'
            f'<span class="swatch-label">{hex_val}</span></div>\n'
        )

    # Typography specimens
    typo_html = ""
    for tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        if tag in elements:
            e = elements[tag]
            ff = e.get("fontFamily", "system-ui")
            fs = e.get("fontSize", "16px")
            fw = e.get("fontWeight", "400")
            lh = e.get("lineHeight", "1.5")
            typo_html += (
                f'<div class="typo-specimen">'
                f'<span class="typo-label">{tag}</span>'
                f'<span style="font-family:{ff};font-size:{fs};'
                f'font-weight:{fw};line-height:{lh}">'
                f'The quick brown fox</span></div>\n'
            )

    # Spacing bars
    spacing_html = ""
    unique_spacing = spacing.get("unique", [])
    for v in unique_spacing[:15]:
        width = min(v, 400)
        spacing_html += (
            f'<div class="spacing-row">'
            f'<span class="spacing-label">{v}px</span>'
            f'<div class="spacing-bar" style="width:{width}px"></div></div>\n'
        )

    # Shadow specimens
    shadow_html = ""
    for tier in ["sm", "md", "lg", "xl"]:
        tier_shadows = shadows.get(tier, [])
        if tier_shadows:
            shadow_html += (
                f'<div class="shadow-specimen"'
                f' style="box-shadow:{tier_shadows[0]}">'
                f'{tier.upper()}</div>\n'
            )

    # Coherence bars
    coherence_bars = ""
    for name, cat in categories.items():
        score = cat["score"]
        color = (
            "#4caf50" if score > 80
            else ("#ff9800" if score > 60 else "#f44336")
        )
        coherence_bars += (
            f'<div class="coherence-row">'
            f'<span class="coherence-label">{name}</span>'
            f'<div class="coherence-track">'
            f'<div class="coherence-fill"'
            f' style="width:{score}%;background:{color}"></div>'
            f'</div>'
            f'<span class="coherence-score">{score}%</span></div>\n'
        )

    pct = round(passing_count / total_pairs * 100)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Design System: {domain}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: system-ui, -apple-system, sans-serif;
    background: #0f0f0f; color: #e0e0e0;
    line-height: 1.6; padding: 2rem;
  }}
  h1 {{ font-size: 2rem; margin-bottom: 0.25rem; color: #fff; }}
  h2 {{
    font-size: 1.25rem; color: #aaa; margin: 2.5rem 0 1rem;
    border-bottom: 1px solid #333; padding-bottom: 0.5rem;
  }}
  .header {{
    display: flex; align-items: baseline; gap: 1rem; margin-bottom: 2rem;
  }}
  .grade {{
    font-size: 2.5rem; font-weight: 800;
    background: linear-gradient(135deg, #6366f1, #a855f7);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }}
  .grade-sub {{ color: #888; font-size: 0.9rem; }}

  .swatches {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(72px, 1fr));
    gap: 8px;
  }}
  .swatch {{
    aspect-ratio: 1; border-radius: 8px; position: relative;
    cursor: pointer; border: 1px solid #333;
    transition: transform 0.15s;
  }}
  .swatch:hover {{ transform: scale(1.1); z-index: 1; }}
  .swatch-label {{
    position: absolute; bottom: 4px; left: 4px;
    font-size: 10px; background: rgba(0,0,0,0.7);
    padding: 1px 4px; border-radius: 3px;
    opacity: 0; transition: opacity 0.15s;
    color: #fff; font-family: monospace;
  }}
  .swatch:hover .swatch-label {{ opacity: 1; }}

  .typo-specimen {{
    display: flex; align-items: baseline; gap: 1rem;
    padding: 0.5rem 0; border-bottom: 1px solid #222;
  }}
  .typo-label {{
    min-width: 3rem; font-size: 0.75rem; color: #888;
    font-family: monospace;
  }}

  .spacing-row {{
    display: flex; align-items: center; gap: 0.75rem; padding: 0.25rem 0;
  }}
  .spacing-label {{
    min-width: 4rem; text-align: right; font-size: 0.8rem;
    color: #888; font-family: monospace;
  }}
  .spacing-bar {{
    height: 12px;
    background: linear-gradient(90deg, #6366f1, #a855f7);
    border-radius: 3px; min-width: 2px;
  }}

  .shadow-specimens {{ display: flex; gap: 1.5rem; flex-wrap: wrap; }}
  .shadow-specimen {{
    width: 100px; height: 100px; background: #1a1a1a;
    border-radius: 8px; display: flex; align-items: center;
    justify-content: center; font-size: 0.8rem; color: #aaa;
    font-family: monospace;
  }}

  .coherence-row {{
    display: grid; grid-template-columns: 120px 1fr 50px;
    align-items: center; gap: 0.75rem; padding: 0.3rem 0;
  }}
  .coherence-label {{
    font-size: 0.8rem; color: #aaa; text-align: right;
  }}
  .coherence-track {{
    height: 16px; background: #222; border-radius: 4px; overflow: hidden;
  }}
  .coherence-fill {{
    height: 100%; border-radius: 4px; transition: width 0.4s ease;
  }}
  .coherence-score {{
    font-size: 0.8rem; color: #ccc; font-family: monospace;
  }}

  .a11y-summary {{
    font-size: 0.9rem; color: #ccc; padding: 1rem;
    background: #1a1a1a; border-radius: 8px; margin-top: 0.5rem;
  }}

  .container {{ max-width: 900px; margin: 0 auto; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <div>
    <h1>{domain}</h1>
    <div class="grade-sub">Design System Extraction</div>
  </div>
  <div>
    <div class="grade">{grade}</div>
    <div class="grade-sub">{overall}/100</div>
  </div>
</div>

<h2>Design Coherence</h2>
{coherence_bars}

<h2>Color Palette</h2>
<div class="swatches">
{swatches_html}
</div>

<h2>Typography</h2>
{typo_html}

<h2>Spacing Scale</h2>
{spacing_html}

<h2>Box Shadows</h2>
<div class="shadow-specimens">
{shadow_html}
</div>

<h2>Accessibility</h2>
<div class="a11y-summary">
  WCAG AA: {passing_count}/{total_pairs} pairs pass ({pct}%)
</div>

</div>
</body>
</html>"""
