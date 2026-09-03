/** 分析文本四段式解析：总体总结 / 分析链路 / 推理论证 / 交易决策。
 *  v4-flash 输出约定标记【总体总结】【分析链路】【推理论证】【决策】；
 *  其他模型/历史文本无标记时回退启发式（首行=总结、已调用行=链路、
 *  尾部 JSON=决策）。前端按段渲染，决策块渲染为行动卡片。 */
export interface DecItem {
  action: string; // buy / sell / hold / watch
  code?: string;
  name?: string;
  pct?: number;
  stop_loss?: number;
  take_profit?: number;
  move_stop?: number;
  invalidation?: string;
  confidence?: number;
  risk_amount?: number;
  reason?: string;
}

export interface ParsedAnalysis {
  summary: string;
  chain: string;
  reasoning: string;
  decisions: DecItem[];
}

/** 括号匹配提取文本中所有 JSON 对象 */
function extractJsonBlocks(text: string): unknown[] {
  const out: unknown[] = [];
  let i = 0;
  while (i < text.length) {
    const s = text.indexOf('{', i);
    if (s < 0) break;
    let depth = 0;
    let inStr = false;
    let j = s;
    for (; j < text.length; j++) {
      const c = text[j];
      if (inStr) {
        if (c === '\\') j++;
        else if (c === '"') inStr = false;
      } else if (c === '"') inStr = true;
      else if (c === '{') depth++;
      else if (c === '}') {
        depth--;
        if (depth === 0) {
          j++;
          break;
        }
      }
    }
    const raw = text.slice(s, j);
    try {
      out.push(JSON.parse(raw));
    } catch {
      /* 忽略不可解析片段 */
    }
    i = j;
  }
  return out;
}

function toDecItems(blocks: unknown[]): DecItem[] {
  const items: DecItem[] = [];
  for (const b of blocks) {
    if (Array.isArray(b)) {
      for (const it of b) {
        if (it && typeof it === 'object' && (it as DecItem).action) items.push(it as DecItem);
      }
    } else if (b && typeof b === 'object') {
      const rec = b as Record<string, unknown>;
      const arr = rec.decisions ?? rec.actions ?? rec.trades;
      if (Array.isArray(arr)) {
        for (const it of arr) {
          if (it && typeof it === 'object' && (it as DecItem).action) items.push(it as DecItem);
        }
      } else if (rec.action) {
        items.push(rec as unknown as DecItem);
      }
    }
  }
  return items.slice(0, 8);
}

/** 匹配数字序号段（②分析链路… / ④决策…），【】标记不可用时的回退 */
function numberedFallback(t: string): { chain: string; decisionsText: string } {
  const sliceOf = (re: RegExp, endRe: RegExp) => {
    const tail = (x: string) => x.replace(/[①②③④]\s*$/, '').trim();
    const m = t.match(re);
    if (!m || m.index == null) return '';
    const rest = t.slice(m.index + m[0].length);
    const e = rest.match(endRe);
    return tail(e && e.index != null ? rest.slice(0, e.index) : rest);
  };
  const chain = sliceOf(/②\s*分析链路/u, /③/);
  const decisionsText = sliceOf(/④\s*决策/u, /$/u);
  return { chain: chain || '', decisionsText };
}

export function parseAnalysis(thought?: string | null): ParsedAnalysis {
  const t = thought ?? '';
  let summary = '';
  let chain = '';
  let reasoning = t;
  let decisions: DecItem[] = [];

  const mHead = t.match(/【总体总结】([\s\S]*?)(?=【分析链路】|【推理论证】|【决策】|[①②③④]|$)/);
  const mChain = t.match(/【分析链路】([\s\S]*?)(?=【推理论证】|【决策】|$)/);
  const mReason = t.match(/【推理论证】([\s\S]*?)(?=【决策】|$)/);
  const mDec = t.match(/【决策】([\s\S]*)$/);

  if (mHead) summary = mHead[1].trim();
  if (mChain) chain = mChain[1].trim();
  if (mReason) reasoning = mReason[1].trim();
  const decText = mDec ? mDec[1] : '';

  if (!mChain && !mDec) {
    const fb = numberedFallback(t);
    chain = chain || fb.chain;
  }
  decisions = toDecItems(extractJsonBlocks(decText || t));
  if (!decisions.length && !decText) {
    // 回退：任何带 action 的 JSON 块
    decisions = toDecItems(extractJsonBlocks(t));
  }
  if (!mReason) {
    // 无【推理论证】标记时，正文去掉已调用行与决策 JSON 尾巴即为推理
    const lines = reasoning.split('\n').filter((l) => !/^\s*已调用/.test(l));
    reasoning = lines.join('\n').trim();
  }
  if (!mHead) {
    // 默认摘要：完整段落，遇 ②③④/小节标题即停（不硬切在编号上）
    const endM = t.match(/[②③④]|^分析链路|^推理论证|^\s*决策|^【/m);
    const body = endM && endM.index != null ? t.slice(0, endM.index) : t;
    const clean = body.replace(/^#{1,6}\s*/, '').replace(/\*\*/g, '').trim();
    summary = clean.length > 260 ? clean.slice(0, 260) + '…' : clean;
  }
  return { summary, chain, reasoning, decisions };
}